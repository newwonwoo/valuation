from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.capacity_yield_operating_paths import (
    CapacityYieldMetricMapping,
    CapacityYieldModuleMapping,
    CapacityYieldProfile,
    CostBasis,
    OperatingPolicy,
    VariableCostRule,
)
from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    StageStatus,
    authorize_post_freeze,
    issue_freeze_token,
)
from valuation_engine.distribution_route_policy import DistributionIntegrationRoute
from valuation_engine.distributional_reporting_canonical import render_canonical_distributional_report
from valuation_engine.distributional_runtime import (
    DistributionPathExecutionInput,
    DistributionalAPVExecutionSpec,
    execute_distributional_apv,
)
from valuation_engine.dynamic_driver_distribution import DriverPath
from valuation_engine.entry_price import EntryPricePolicy
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.intrinsic_envelope import IntrinsicEnvelopeError
from valuation_engine.levered_financing_paths import (
    AssetSalePolicy,
    EquityRaisePolicy,
    FinancingPathSpec,
    RecoveryWaterfallPolicy,
)
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import build_module_requirement_plan
from valuation_engine.payoff_model_ambiguity import RobustEntryPolicy
from valuation_engine.probability_ambiguity import ProbabilityVector
from valuation_engine.records import AuditFinding, AuditReport
from valuation_engine.valuation_method_intent import resolve_valuation_method_intent


D = Decimal
ROOT = Path(__file__).resolve().parents[1]


def _profile(*, archetypes=(EconomicArchetype.CAPACITY_YIELD_LEVERED,)) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="transport",
        sector_adapter="transport_industrial.airline",
        archetypes=archetypes,
        revenue_recognition="completed_transport_service",
        price_formation="capacity_utilization_times_realized_unit_yield",
        asset_ownership="owned_and_leased_long_lived_assets",
        capital_intensity="high",
        regulation_intensity="high",
        customer_structure="diversified_transport_customers",
        reinvestment_model="asset_replacement_growth_and_lease_renewal",
        cashflow_duration="cyclical_demand_with_long_lived_asset_commitments",
        evidence_keys=("EV-TRANSPORT",),
    )


def _metric_profile() -> tuple[CapacityYieldProfile, CapacityYieldMetricMapping]:
    profile = CapacityYieldProfile(
        target_id="carrier-x",
        economic_archetype="capacity_yield_levered",
        reporting_currency="KRW",
        accounting_basis="IFRS",
        active_module_ids=("network",),
        non_capacity_segment_ids=(),
        metric_mapping_version="test-map-v1",
    )
    mapping = CapacityYieldMetricMapping(
        version="test-map-v1",
        modules=(
            CapacityYieldModuleMapping(
                "network",
                "capacity",
                "yield",
                "utilization",
                "ECON-NETWORK",
            ),
        ),
        variable_cost_rules=(
            VariableCostRule(
                "network-variable",
                "network",
                CostBasis.CAPACITY,
                ("unit_variable_cost",),
            ),
        ),
        fixed_cost_driver_ids=("fixed_cost",),
        depreciation_driver_id="depreciation",
        owned_capex_driver_id="owned_capex",
        lease_additions_driver_id="lease_additions",
        change_in_working_capital_driver_id="delta_nwc",
    )
    return profile, mapping


def _driver(path_id: str, *, capacity: str, yield_value: str) -> DriverPath:
    return DriverPath(
        path_id=path_id,
        seed=1 if path_id.endswith("1") else 2,
        values=(
            ("capacity", (D(capacity), D(capacity))),
            ("yield", (D(yield_value), D(yield_value))),
            ("utilization", (D("0.80"), D("0.82"))),
            ("unit_variable_cost", (D("0.60"), D("0.62"))),
            ("fixed_cost", (D("40"), D("42"))),
            ("depreciation", (D("10"), D("10"))),
            ("owned_capex", (D("20"), D("20"))),
            ("lease_additions", (D("0"), D("0"))),
            ("delta_nwc", (D("2"), D("2"))),
        ),
    )


def _financing() -> FinancingPathSpec:
    return FinancingPathSpec(
        opening_cash=D("100"),
        minimum_operating_cash=D("10"),
        debt_schedules=(),
        lease_schedules=(),
        refinancing_facilities=(),
        asset_sale_policy=AssetSalePolicy((D("0"), D("0")), D("0")),
        equity_raise_policy=EquityRaisePolicy(
            (D("0"), D("0")),
            D("0"),
            (D("0"), D("0")),
        ),
        recovery_waterfall=RecoveryWaterfallPolicy(
            fixed_distress_cost=D("0"),
            distress_cost_rate=D("0"),
            old_shareholder_retention=D("0"),
        ),
    )


def _path(
    *,
    path_id: str,
    outcome_id: str,
    model_case_id: str = "BASE-FINANCING",
    capacity: str,
    yield_value: str,
) -> DistributionPathExecutionInput:
    return DistributionPathExecutionInput(
        model_case_id=model_case_id,
        outcome_id=outcome_id,
        driver_path=_driver(path_id, capacity=capacity, yield_value=yield_value),
        financing_spec=_financing(),
        distress_asset_proceeds_by_period=(D("0"), D("0")),
        core_segment_id="network",
        core_economic_path_id="ECON-NETWORK",
        asset_required_return=D("0.10"),
        terminal_growth=D("0.02"),
        tax_shield_discount_rate=D("0.04"),
        equity_required_return=D("0.12"),
        non_operating_assets_present=D("20"),
        non_operating_assets_at_horizon=D("20"),
        distributions_to_old_holders=(D("0"), D("0")),
        initial_shares=D("10"),
    )


def _pathwise_spec() -> DistributionalAPVExecutionSpec:
    profile, mapping = _metric_profile()
    return DistributionalAPVExecutionSpec(
        target_id="carrier-x",
        profile=profile,
        metric_mapping=mapping,
        operating_policy=OperatingPolicy(D("0.25")),
        paths=(
            _path(path_id="PATH-1", outcome_id="LOW", capacity="90", yield_value="3.5"),
            _path(path_id="PATH-2", outcome_id="HIGH", capacity="110", yield_value="4.5"),
        ),
        route=DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION,
        route_evidence_path_ids=("EV-TRANSPORT", "beta:BETA-HASH"),
        entry_policy=EntryPricePolicy(
            policy_version="entry-v1",
            horizon_years=2,
            required_annual_return=D("0.12"),
            success_quantile=D("0.20"),
            sensitivity_returns=(D("0.08"), D("0.16")),
        ),
        driver_distribution_authorized=True,
        driver_distribution_authorization_hash="DRIVER-AUTH",
        seed_set=(11, 17),
    )


def _ambiguity_spec() -> DistributionalAPVExecutionSpec:
    profile, mapping = _metric_profile()
    return DistributionalAPVExecutionSpec(
        target_id="carrier-x",
        profile=profile,
        metric_mapping=mapping,
        operating_policy=OperatingPolicy(D("0.25")),
        paths=(
            _path(path_id="PATH-1", outcome_id="LOW", capacity="90", yield_value="3.5"),
            _path(path_id="PATH-2", outcome_id="HIGH", capacity="110", yield_value="4.5"),
        ),
        route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
        route_evidence_path_ids=("EV-TRANSPORT", "beta:BETA-HASH"),
        entry_policy=RobustEntryPolicy(
            policy_version="robust-entry-v1",
            horizon_years=2,
            required_annual_return=D("0.12"),
            sensitivity_returns=(D("0.08"), D("0.16")),
        ),
        probability_vectors=(
            ProbabilityVector(
                "CONSERVATIVE",
                (("LOW", D("0.70")), ("HIGH", D("0.30"))),
                ("PRIOR-A",),
            ),
            ProbabilityVector(
                "CONSTRUCTIVE",
                (("LOW", D("0.40")), ("HIGH", D("0.60"))),
                ("PRIOR-B",),
            ),
        ),
    )


def test_primary_aggregator_is_resolved_without_faking_a_segment_evaluator():
    plan = build_module_requirement_plan(
        (_profile(),),
        registry_path=ROOT / "config/archetype_module_registry.yaml",
        control_requirements_path=ROOT / "config/archetype_control_requirements.yaml",
    )
    intent = resolve_valuation_method_intent(
        plan,
        capability_registry=load_default_method_capability_registry(),
    )
    assert intent.ready
    assert intent.primary_aggregator is not None
    assert intent.primary_aggregator.binding == "capacity_yield_levered/driver_distributional_apv"
    assert intent.segments[0].delegated_to_primary_aggregator
    assert intent.method_choices() == ()
    assert intent.requires_beta


def test_airline_keeps_legacy_dcf_reference_while_distributional_apv_is_primary():
    plan = build_module_requirement_plan(
        (
            _profile(
                archetypes=(
                    EconomicArchetype.AIRLINE_TRANSPORT,
                    EconomicArchetype.CAPACITY_YIELD_LEVERED,
                )
            ),
        ),
        registry_path=ROOT / "config/archetype_module_registry.yaml",
        control_requirements_path=ROOT / "config/archetype_control_requirements.yaml",
    )
    intent = resolve_valuation_method_intent(
        plan,
        capability_registry=load_default_method_capability_registry(),
    )
    assert intent.ready
    assert intent.primary_aggregator is not None
    assert intent.primary_aggregator.binding == "capacity_yield_levered/driver_distributional_apv"
    choices = intent.method_choices()
    assert len(choices) == 1
    assert choices[0].archetype == "airline_transport"
    assert choices[0].method == "traffic_yield_dcf"
    assert intent.requires_wacc


def test_calibrated_pathwise_distribution_seals_entry_and_distribution_in_one_envelope():
    result = execute_distributional_apv(_pathwise_spec())
    assert result.pathwise_distribution is not None
    assert result.ambiguity_intrinsic_range is None
    assert result.pathwise_entry is not None
    assert result.pathwise_entry.entry_price is not None
    assert result.route_authorization.pathwise_value_distribution_authorized
    assert result.route_authorization.entry_price_authorized
    assert result.route_authorization.success_probability_claim_authorized
    assert result.envelope.distribution_lineage is not None
    assert result.envelope.distribution_lineage.distribution_hash == result.distribution_hash
    result.envelope.validate()

    tampered = replace(result.envelope, source_value_hash="TAMPERED")
    with pytest.raises(IntrinsicEnvelopeError, match="hash does not replay"):
        tampered.validate()


def test_uncalibrated_prior_uses_ambiguity_range_and_never_claims_success_probability():
    result = execute_distributional_apv(_ambiguity_spec())
    assert result.pathwise_distribution is None
    assert result.ambiguity_intrinsic_range is not None
    assert result.robust_entry is not None
    assert result.route_authorization.expected_value_interval_authorized
    assert result.route_authorization.entry_price_authorized
    assert not result.route_authorization.point_target_authorized
    assert not result.route_authorization.success_probability_claim_authorized
    assert result.ambiguity_intrinsic_range.minimum_expected_value <= result.ambiguity_intrinsic_range.maximum_expected_value
    assert result.envelope.distribution_lineage is not None
    assert result.envelope.distribution_lineage.ambiguity_set_hash
    assert result.envelope.distribution_lineage.payoff_model_set_hash


def test_distribution_lineage_is_cryptographically_bound_into_freeze_token():
    result = execute_distributional_apv(_ambiguity_spec())
    lineage = result.envelope.distribution_lineage
    assert lineage is not None
    coverage = (DoctrineCoverageEntry("AUDIT", StageStatus.PASS, "ok"),)
    token = issue_freeze_token(
        run_id="RUN-DIST",
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=("AUDIT",),
        ledger_snapshot_hash="LEDGER",
        assumption_set_hash="ASSUMPTIONS",
        valuation_hash=result.envelope.envelope_hash,
        audit_hash="AUDIT-HASH",
        industry_snapshot_hash="INDUSTRY",
        source_snapshot_hash="SOURCE",
        distribution_hash=lineage.distribution_hash,
        ambiguity_set_hash=lineage.ambiguity_set_hash,
        payoff_model_set_hash=lineage.payoff_model_set_hash,
        route_authorization_hash=lineage.route_authorization_hash,
        entry_policy_version=lineage.entry_policy_version,
        entry_calculation_hash=lineage.entry_calculation_hash,
    )
    authorize_post_freeze(token, run_id="RUN-DIST")
    with pytest.raises(PermissionError, match="invalid intrinsic freeze token"):
        authorize_post_freeze(replace(token, distribution_hash="TAMPER"), run_id="RUN-DIST")


def test_distributional_report_keeps_uncalibrated_prior_out_of_success_probability_claims():
    result = execute_distributional_apv(_ambiguity_spec())
    audit = AuditReport((AuditFinding("distribution", True, True, "ok"),))
    report = render_canonical_distributional_report(
        {
            "company": "테스트운송",
            "distributional_primary_result": result,
            "audit_report": audit,
            "current_thesis": "수요와 단가가 현금흐름으로 이어지는지를 확인한다.",
        },
        require_verifiable_sources=False,
    )
    for section in (
        "## 투자 요약",
        "## 가치평가",
        "## 핵심 가정과 위험",
        "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
        "## 증권사·시장 비교",
        "## 정보 출처 — 원문 바로 확인",
    ):
        assert section in report
    assert "단일 확률가중 목표가: 미산출" in report
    assert "보정 성공확률: 미산출" in report
    assert "**투자판단**" in report
    assert "**현재가**" in report
    assert "**기준 내재가치**" in report
    assert "**가치평가 범위**" in report
    assert "**시나리오 가능성**" in report
