from pathlib import Path

import yaml

from valuation_engine.industry_dna import (
    EconomicArchetype,
    IndustryDNAProfile,
    compose_modules,
)
from valuation_engine.method_capabilities import (
    MethodKind,
    MethodRuntimeStatus,
    load_default_method_capability_registry,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHETYPE = "capacity_yield_levered"
METHOD = "driver_distributional_apv"


def _unit(unit_id: str) -> dict[str, object]:
    payload = yaml.safe_load(
        (ROOT / "config/unit_contract_registry.yaml").read_text(encoding="utf-8")
    )
    return next(item for item in payload["units"] if item["unit_id"] == unit_id)


def _profile(*, segment_id: str, sector_adapter: str) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id=segment_id,
        sector_adapter=sector_adapter,
        archetypes=(EconomicArchetype.CAPACITY_YIELD_LEVERED,),
        revenue_recognition="completed_transport_service",
        price_formation="capacity_utilization_times_realized_unit_yield",
        asset_ownership="owned_and_leased_long_lived_assets",
        capital_intensity="high",
        regulation_intensity="high",
        customer_structure="diversified_transport_customers",
        reinvestment_model="asset_replacement_growth_and_lease_renewal",
        cashflow_duration="cyclical_demand_with_long_lived_asset_commitments",
        evidence_keys=(f"EV-{segment_id}",),
    )


def test_distributional_apv_has_one_exact_company_independent_binding():
    registry = load_default_method_capability_registry()
    capability = registry.get(ARCHETYPE, METHOD)
    legacy = registry.get("airline_transport", "traffic_yield_dcf")

    assert capability.execution_family == "distributional_apv"
    assert capability.kind is MethodKind.AGGREGATOR
    assert capability.output_kind == "equity_value_distribution"
    assert capability.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME
    assert capability.stage == "DETERMINISTIC_VALUATION"
    assert capability.requires_beta
    assert not capability.requires_wacc
    assert legacy.execution_family == "explicit_fcff_dcf"
    assert legacy.kind is MethodKind.SEGMENT_EVALUATOR
    assert legacy.identity != capability.identity

    registry_payload = yaml.safe_load(
        (ROOT / "config/valuation_method_capability_registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    family = dict(registry_payload["execution_families"]["distributional_apv"])
    family.pop("canonical_refs")
    semantic_binding = yaml.safe_dump(
        {
            "family": family,
            "binding": registry_payload["bindings"][ARCHETYPE][METHOD],
        }
    )
    assert "korean_air" not in semantic_binding.lower()
    assert "003490" not in semantic_binding


def test_airline_and_shipping_profiles_compile_the_same_common_method():
    airline = compose_modules(
        _profile(segment_id="airline", sector_adapter="transport_industrial.airline")
    )
    shipping = compose_modules(
        _profile(segment_id="shipping", sector_adapter="transport_industrial.shipping")
    )

    assert airline.allowed_valuation_methods == (METHOD,)
    assert shipping.allowed_valuation_methods == (METHOD,)
    assert airline.archetype_modules == shipping.archetype_modules == (ARCHETYPE,)


def test_sector_adapters_map_airline_and_shipping_to_the_common_archetype():
    adapters = yaml.safe_load(
        (ROOT / "config/sector_adapter_registry.yaml").read_text(encoding="utf-8")
    )["adapters"]

    assert ARCHETYPE in adapters["transport_industrial.airline"]["default_archetypes"]
    assert ARCHETYPE in adapters["transport_industrial.shipping"]["default_archetypes"]


def test_distribution_contract_forbids_anchor_probability_and_report_floor():
    scenario = _unit("SCENARIO_ENGINE")
    valuation = _unit("DETERMINISTIC_VALUATION")
    audit = _unit("AUDIT_GATE")
    freeze = _unit("INTRINSIC_FREEZE")

    assert {
        "recursive_driver_paths",
        "calibration_diagnostics",
        "valuation_distribution_authorized",
    } <= set(scenario["outputs"])
    assert "nearest_scenario_anchor_probability" in scenario["forbidden_effects"]
    assert "peer_company_outcome_as_target_probability_sample" in scenario["forbidden_effects"]

    assert {
        "equity_value_distribution",
        "distress_probability",
        "dilution_probability",
        "governed_entry_price",
    } <= set(valuation["outputs"])
    assert "target_id_formula_selection" in valuation["forbidden_effects"]
    assert "report_time_equity_zero_floor" in valuation["forbidden_effects"]

    assert "authorize_distribution_without_oos_skill" in audit["forbidden_effects"]
    assert "authorize_entry_price_from_current_market_price" in audit["forbidden_effects"]
    assert "frozen_equity_value_distribution" in freeze["outputs"]
    assert "frozen_entry_price" in freeze["outputs"]


def test_root_and_canonical_skills_are_identical_and_name_the_exact_route():
    root_skill = (ROOT / "SKILL.md").read_bytes()
    canonical_skill = (
        ROOT / ".agents/skills/valuation-analysis/SKILL.md"
    ).read_bytes()

    assert root_skill == canonical_skill
    assert b"capacity_yield_levered/driver_distributional_apv" in root_skill
