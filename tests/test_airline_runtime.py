"""Airline routing must reach genuine traffic economics and an executable DCF."""
from pathlib import Path

from valuation_engine.generic_kr_industry import load_kr_industry_classification
from valuation_engine.generic_live_providers import required_assumption_keys_by_segment
from valuation_engine.generic_scanners import generic_scanner_runners
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile, compose_modules
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_requirements import build_module_requirement_plan_from_repo
from valuation_engine.scanner_runtime import ScannerContext, ScannerFindingStatus
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice

ROOT = Path(__file__).resolve().parents[1]


def profile():
    entry = load_kr_industry_classification().lookup("51100")
    return IndustryDNAProfile(segment_id="airline", sector_adapter=entry.sector_adapter,
        archetypes=entry.archetypes, evidence_keys=("EV-AIRLINE-ROUTE",), **entry.structure)


def test_passenger_airline_route_does_not_import_plant_or_retail_requirements():
    routed = profile()
    assert routed.archetypes == (EconomicArchetype.AIRLINE_TRANSPORT,)
    assert compose_modules(routed).allowed_valuation_methods == ("traffic_yield_dcf",)
    plan = build_module_requirement_plan_from_repo(routed, repo_root=ROOT)
    assert {"passenger_capacity", "passenger_traffic", "passenger_yield", "cargo_traffic",
            "cargo_yield", "fuel_cost", "nonfuel_operating_cost", "fleet_capex",
            "lease_liabilities"} <= set(plan.required_evidence)
    assert not {"feedstock", "turnaround", "same_store_growth", "inventory_turn",
                "nameplate_capacity", "input_price"} & set(plan.required_evidence)
    assert "IFRS16_lease_cashflows_and_debt_consistent_treatment" in plan.normalization_rules


def test_airline_method_has_real_family_and_separate_aerospace_assumption_keys():
    capability = load_default_method_capability_registry().get("airline_transport", "traffic_yield_dcf")
    assert capability.execution_family == "explicit_fcff_dcf"
    assert capability.requires_beta and capability.requires_wacc
    keys = required_assumption_keys_by_segment(method_choices=(
        SegmentMethodChoice("airline", "airline_transport", "traffic_yield_dcf"),
        SegmentMethodChoice("aerospace", "contracted_backlog", "normalized_dcf"),
    ), forecast_years=5)
    assert {"airline_fcff_year_1", "airline_fcff_year_5", "airline_terminal_growth",
            "airline_terminal_roic", "airline_ownership", "airline_ev_adjustment",
            "diluted_shares"} <= set(keys["airline"])
    assert "aerospace_fcff_year_1" in keys["aerospace"]
    assert not set(keys["airline"]) & set(keys["aerospace"])


def test_airline_scanners_do_not_pass_without_observed_ledger_evidence():
    plan = build_module_requirement_plan_from_repo(profile(), repo_root=ROOT)
    runners = generic_scanner_runners()
    for scanner_id in plan.mandatory_scanner_ids:
        result = runners[scanner_id](ScannerContext(scanner_id=scanner_id,
            company="Test airline", ticker="TEST", target_id="TEST:AIRLINE",
            ledger=EvidenceLedger(), module_requirement_plan=plan))
        assert result.status is ScannerFindingStatus.WARNING
        assert result.verification_requests
        assert not result.evidence_ids


def test_four_reportable_segments_route_to_evidenced_methods_with_isolated_values():
    routes = (
        ("airline", "51100", "airline_transport", "traffic_yield_dcf"),
        ("aerospace", "31311", "contracted_backlog", "normalized_dcf"),
        ("hotel", "68112", "asset_yield_nav", "nav"),
        ("other", "62021", "service_operations", "normalized_service_dcf"),
    )
    mapping = load_kr_industry_classification()
    choices = []
    profiles = []
    for segment, code, archetype, method in routes:
        entry = mapping.lookup(code)
        assert entry.archetypes == (EconomicArchetype(archetype),)
        routed = IndustryDNAProfile(segment_id=segment, sector_adapter=entry.sector_adapter,
            archetypes=entry.archetypes, evidence_keys=("EV-ROUTE-" + segment,), **entry.structure)
        profiles.append(routed)
        plan = build_module_requirement_plan_from_repo(routed, repo_root=ROOT)
        assert method in plan.allowed_valuation_methods
        assert set(plan.mandatory_scanner_ids) <= set(generic_scanner_runners())
        choices.append(SegmentMethodChoice(segment, archetype, method))
    keys = required_assumption_keys_by_segment(method_choices=tuple(choices), forecast_years=5)
    assert set(keys["hotel"]) == {"hotel_gross_asset_value", "hotel_liabilities", "hotel_ownership"}
    # NAV already emits equity: a second segment EV-to-equity bridge would double-deduct debt.
    assert "hotel_ev_adjustment" not in keys["hotel"]
    assert "other_fcff_year_5" in keys["other"]
    assert "aerospace_fcff_year_5" in keys["aerospace"]
    combined = [key for segment_keys in keys.values() for key in segment_keys]
    assert len(combined) == len(set(combined))

    from valuation_engine.module_plan import build_module_requirement_plan as build_control_plan
    control_plan = build_control_plan(tuple(profiles),
        registry_path=ROOT / "config/archetype_module_registry.yaml",
        control_requirements_path=ROOT / "config/archetype_control_requirements.yaml")
    assert len(control_plan.segments) == 4
    assert set(control_plan.mandatory_scanners) <= set(generic_scanner_runners())
