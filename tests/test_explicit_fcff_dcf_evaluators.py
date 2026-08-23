from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dcf_evaluators import (
    ExplicitFCFFDCFEvaluator,
    LiveDCFRegistration,
    live_fcff_dcf_registry_loader,
)
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import CalibrationStatus
from valuation_engine.risk import HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import CompanyValuationPlan, SegmentValuationPlan
from valuation_engine.wacc import WACCResult


def assumption(key: str, value: str, unit: str, *, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="BASE",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def dcf_assumptions(prefix: str, *, final_fcff="14", terminal_growth="0.025"):
    return (
        assumption(f"{prefix}fcff_year_1", "10", "KRW_billion", path=f"{prefix}fcff1"),
        assumption(f"{prefix}fcff_year_2", "12", "KRW_billion", path=f"{prefix}fcff2"),
        assumption(f"{prefix}fcff_year_3", final_fcff, "KRW_billion", path=f"{prefix}fcff3"),
        assumption(
            f"{prefix}terminal_growth",
            terminal_growth,
            "ratio",
            path=f"{prefix}terminal_growth",
        ),
        assumption(f"{prefix}terminal_roic", "0.12", "ratio", path=f"{prefix}terminal_roic"),
    )


def structure() -> LiveCapitalStructureObservation:
    return LiveCapitalStructureObservation(
        equity_weight=0.75,
        debt_weight=0.25,
        tax_rate=0.22,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of="2026-08-22",
        source_refs=("CAPITAL:1",),
        rationale="normalized target structure",
    )


def live_wacc(wacc: float) -> LiveWACCStageResult:
    beta = LiveBetaStageResult(
        estimate=HierarchicalBetaEstimate(0.9, 0.01, ()),
        target_asset_beta=0.9,
        target_levered_beta=1.1,
        target_capital_structure=structure(),
        peer_ids=("P1", "P2", "P3", "P4"),
        source_refs=("BETA:1",),
        selection_evidence_ids=("E1", "E2", "E3", "E4"),
        snapshot_hash="BETA-HASH",
    )
    return LiveWACCStageResult(
        beta_result=beta,
        wacc_result=WACCResult(0.10, 0.04, 0.75, 0.25, wacc),
        terminal_consistency=None,
        source_refs=("WACC:1",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash=f"WACC-{wacc}",
    )


def scenario(*assumptions: CompiledAssumption) -> BoundScenario:
    return BoundScenario("BASE", assumptions)


def scenario_set(*assumptions: CompiledAssumption) -> BoundScenarioSet:
    return BoundScenarioSet(
        target_id="T",
        scenarios=(scenario(*assumptions),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIO-HASH",
    )


def test_explicit_fcff_dcf_matches_formula_and_carries_discount_path():
    evaluator = ExplicitFCFFDCFEvaluator(
        archetype="capacity_manufacturing",
        method="driver_dcf",
        version="core-v1",
        forecast_years=3,
        discount_rate=Decimal("0.08"),
        discount_rate_path_id="wacc:WACC-HASH",
        assumption_prefix="core_",
    )
    result = evaluator.evaluate(scenario(*dcf_assumptions("core_")), segment_id="core")
    expected = (
        Decimal("10") / Decimal("1.08")
        + Decimal("12") / Decimal("1.08") ** 2
        + Decimal("14") / Decimal("1.08") ** 3
        + (Decimal("14") * Decimal("1.025") / Decimal("0.055"))
        / Decimal("1.08") ** 3
    )
    assert abs(result.value.amount - expected) < Decimal("1e-20")
    assert "wacc:WACC-HASH:core" in result.economic_path_ids


def test_higher_live_wacc_reduces_dcf_value():
    registration = (
        LiveDCFRegistration(
            "capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"
        ),
    )
    scenario_value = scenario(*dcf_assumptions("core_"))
    low_registry = live_fcff_dcf_registry_loader(registrations=registration)(
        OrchestratorContext(
            "LOW", ExecutionMode.LIVE_PRIMARY, {"live_wacc_result": live_wacc(0.08)}
        )
    )
    high_registry = live_fcff_dcf_registry_loader(registrations=registration)(
        OrchestratorContext(
            "HIGH", ExecutionMode.LIVE_PRIMARY, {"live_wacc_result": live_wacc(0.12)}
        )
    )
    key = ModelKey("capacity_manufacturing", "driver_dcf", "core-v1")
    low = low_registry.evaluate(key, scenario_value, segment_id="core").value.amount
    high = high_registry.evaluate(key, scenario_value, segment_id="core").value.amount
    assert low > high


def test_terminal_growth_at_or_above_wacc_is_blocked():
    evaluator = ExplicitFCFFDCFEvaluator(
        "capacity_manufacturing",
        "driver_dcf",
        "core-v1",
        3,
        Decimal("0.08"),
        "wacc:HASH",
        "core_",
    )
    with pytest.raises(ValueError, match="WACC must exceed terminal growth"):
        evaluator.evaluate(
            scenario(*dcf_assumptions("core_", terminal_growth="0.08")),
            segment_id="core",
        )


def test_nonpositive_final_fcff_cannot_enter_gordon_terminal():
    evaluator = ExplicitFCFFDCFEvaluator(
        "capacity_manufacturing",
        "driver_dcf",
        "core-v1",
        3,
        Decimal("0.08"),
        "wacc:HASH",
        "core_",
    )
    with pytest.raises(ValueError, match="positive final-year FCFF"):
        evaluator.evaluate(
            scenario(*dcf_assumptions("core_", final_fcff="0")),
            segment_id="core",
        )


def test_runtime_registry_supports_two_exact_segment_models_without_path_collision():
    assumptions = (
        *dcf_assumptions("cap_"),
        *dcf_assumptions("sub_"),
        assumption("cap_ownership", "1", "ratio", path="cap_ownership"),
        assumption("sub_ownership", "1", "ratio", path="sub_ownership"),
        assumption("cap_net_debt", "-5", "KRW_billion", path="cap_net_debt"),
        assumption("sub_net_debt", "-3", "KRW_billion", path="sub_net_debt"),
        assumption("diluted_shares", "10", "shares", path="dilution"),
    )
    bound = scenario_set(*assumptions)
    registrations = (
        LiveDCFRegistration(
            "capacity_manufacturing", "driver_dcf", "cap-v1", 3, "cap_"
        ),
        LiveDCFRegistration(
            "recurring_subscription", "arr_fcf_dcf", "sub-v1", 3, "sub_"
        ),
    )
    plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                "cap-asset",
                "capacity",
                ModelKey("capacity_manufacturing", "driver_dcf", "cap-v1"),
                "cap_ownership",
                "cap_net_debt",
            ),
            SegmentValuationPlan(
                "sub-asset",
                "subscription",
                ModelKey("recurring_subscription", "arr_fcf_dcf", "sub-v1"),
                "sub_ownership",
                "sub_net_debt",
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="diluted_shares",
    )
    adapter = deterministic_valuation_adapter(
        plan=plan,
        registry_loader=live_fcff_dcf_registry_loader(registrations=registrations),
    )
    result = adapter(
        OrchestratorContext(
            "DCF",
            ExecutionMode.LIVE_PRIMARY,
            {"bound_scenario_set": bound, "live_wacc_result": live_wacc(0.08)},
        )
    )
    assert result.status is StageStatus.PASS
    components = result.outputs["generic_valuation_result"].equity_aggregation.scenario_values[0].components
    assert len(components) == 2
    assert result.outputs["intrinsic_scenario_values"][0].value_per_share > 0


def test_unregistered_exact_model_remains_not_implemented():
    bound = scenario_set(
        *dcf_assumptions("core_"),
        assumption("ownership", "1", "ratio", path="ownership"),
        assumption("net_debt", "0", "KRW_billion", path="net_debt"),
        assumption("shares", "10", "shares", path="shares"),
    )
    plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                "asset",
                "core",
                ModelKey("capacity_manufacturing", "unknown_dcf", "v1"),
                "ownership",
                "net_debt",
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
    )
    result = deterministic_valuation_adapter(
        plan=plan,
        registry_loader=live_fcff_dcf_registry_loader(
            registrations=(
                LiveDCFRegistration(
                    "capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"
                ),
            )
        ),
    )(
        OrchestratorContext(
            "NO-FALLBACK",
            ExecutionMode.LIVE_PRIMARY,
            {"bound_scenario_set": bound, "live_wacc_result": live_wacc(0.08)},
        )
    )
    assert result.status is StageStatus.NOT_IMPLEMENTED


def test_runtime_registry_rejects_target_market_leakage():
    loader = live_fcff_dcf_registry_loader(
        registrations=(
            LiveDCFRegistration(
                "capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"
            ),
        )
    )
    with pytest.raises(PermissionError, match="target Street/market"):
        loader(
            OrchestratorContext(
                "LEAK",
                ExecutionMode.LIVE_PRIMARY,
                {"live_wacc_result": live_wacc(0.08), "current_market_price": 100000},
            )
        )


def test_runtime_registry_requires_live_wacc():
    loader = live_fcff_dcf_registry_loader(
        registrations=(
            LiveDCFRegistration(
                "capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"
            ),
        )
    )
    with pytest.raises(ValueError, match="LiveWACCStageResult"):
        loader(OrchestratorContext("NO-WACC", ExecutionMode.LIVE_PRIMARY, {}))


def test_duplicate_exact_model_registration_is_rejected():
    duplicate = LiveDCFRegistration(
        "capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"
    )
    with pytest.raises(ValueError, match="duplicate live DCF ModelKey"):
        live_fcff_dcf_registry_loader(registrations=(duplicate, duplicate))
