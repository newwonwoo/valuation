from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.finite_life_evaluators import (
    FiniteLifeNPVRegistration,
    live_finite_npv_registry_loader,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.risk import HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.scenario_binding import BoundScenario
from valuation_engine.wacc import WACCResult


def assumption(key: str, value: str, unit: str, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Base",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def live_wacc(rate: float = 0.10) -> LiveWACCStageResult:
    structure = LiveCapitalStructureObservation(
        0.75,
        0.25,
        0.22,
        TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        "2026-08-22",
        ("CAPITAL:1",),
        "normalized target structure",
    )
    beta = LiveBetaStageResult(
        HierarchicalBetaEstimate(0.9, 0.01, ()),
        0.9,
        1.1,
        structure,
        ("P1", "P2", "P3", "P4"),
        ("BETA:SOURCE",),
        ("EV-BETA-1",),
        "BETA-HASH",
    )
    return LiveWACCStageResult(
        beta,
        WACCResult(0.10, 0.04, 0.75, 0.25, rate),
        None,
        ("WACC:SOURCE",),
        ("EV-CREDIT-1",),
        False,
        "WACC-HASH",
    )


def project_scenario() -> BoundScenario:
    return BoundScenario(
        "Base",
        (
            assumption("project_cashflow_year_0", "-100", "KRW_billion", "project:construction-capex"),
            assumption("project_cashflow_year_1", "60", "KRW_billion", "project:cod-year1"),
            assumption("project_cashflow_year_2", "60", "KRW_billion", "project:cod-year2"),
        ),
    )


def loader(rate: float = 0.10):
    runtime = live_finite_npv_registry_loader(
        registrations=(
            FiniteLifeNPVRegistration("project_finance", "project_npv", "project-v1", 2, "project_"),
            FiniteLifeNPVRegistration("reserve_depletion", "reserve_npv", "reserve-v1", 2, "reserve_"),
            FiniteLifeNPVRegistration("hit_driven_content", "cohort_npv", "cohort-v1", 2, "cohort_"),
        ),
        include_default_normalized_multiples=False,
    )
    return runtime(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"live_wacc_result": live_wacc(rate)},
        )
    )


def test_project_npv_is_finite_life_and_has_no_terminal_value():
    registry = loader()
    value = registry.evaluate(
        ModelKey("project_finance", "project_npv", "project-v1"),
        project_scenario(),
        segment_id="project",
    )
    expected = Decimal("-100") + Decimal("60") / Decimal("1.10") + Decimal("60") / Decimal("1.10") ** 2
    assert value.value.amount == pytest.approx(expected)
    assert "project:construction-capex" in value.economic_path_ids
    assert "wacc:WACC-HASH:project" in value.economic_path_ids
    assert "beta:BETA-HASH:project" in value.economic_path_ids
    assert all("terminal" not in path for path in value.economic_path_ids)


def test_higher_wacc_reduces_finite_life_project_value():
    low = loader(0.08).evaluate(
        ModelKey("project_finance", "project_npv", "project-v1"), project_scenario(), segment_id="project"
    )
    high = loader(0.12).evaluate(
        ModelKey("project_finance", "project_npv", "project-v1"), project_scenario(), segment_id="project"
    )
    assert high.value.amount < low.value.amount


def test_reserve_and_cohort_require_exact_registrations():
    registry = loader()
    reserve = BoundScenario(
        "Base",
        (
            assumption("reserve_cashflow_year_0", "-20", "KRW_billion", "reserve:development"),
            assumption("reserve_cashflow_year_1", "40", "KRW_billion", "reserve:production1"),
            assumption("reserve_cashflow_year_2", "25", "KRW_billion", "reserve:production2"),
        ),
    )
    cohort = BoundScenario(
        "Base",
        (
            assumption("cohort_cashflow_year_0", "-15", "KRW_billion", "cohort:development"),
            assumption("cohort_cashflow_year_1", "30", "KRW_billion", "cohort:launch1"),
            assumption("cohort_cashflow_year_2", "10", "KRW_billion", "cohort:decay2"),
        ),
    )
    assert registry.evaluate(ModelKey("reserve_depletion", "reserve_npv", "reserve-v1"), reserve, segment_id="reserve").value.amount > 0
    assert registry.evaluate(ModelKey("hit_driven_content", "cohort_npv", "cohort-v1"), cohort, segment_id="content").value.amount > 0
    with pytest.raises(KeyError, match="no exact evaluator"):
        registry.evaluate(ModelKey("project_finance", "generic_dcf", "1"), project_scenario(), segment_id="project")


def test_finite_life_loader_composes_with_existing_fcff_dcf_registry():
    base = live_fcff_dcf_registry_loader(
        registrations=(
            LiveDCFRegistration("capacity_manufacturing", "driver_dcf", "cap-v1", 2, "cap_"),
        ),
        include_default_normalized_multiples=False,
    )
    combined_loader = live_finite_npv_registry_loader(
        registrations=(FiniteLifeNPVRegistration("project_finance", "project_npv", "project-v1", 2, "project_"),),
        base_loader=base,
        include_default_normalized_multiples=False,
    )
    registry = combined_loader(
        OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {"live_wacc_result": live_wacc()})
    )
    assert ModelKey("capacity_manufacturing", "driver_dcf", "cap-v1") in registry.keys()
    assert ModelKey("project_finance", "project_npv", "project-v1") in registry.keys()


def test_market_leakage_is_rejected_before_finite_life_registry_build():
    runtime = live_finite_npv_registry_loader(
        registrations=(FiniteLifeNPVRegistration("project_finance", "project_npv", "project-v1", 2, "project_"),)
    )
    with pytest.raises(PermissionError, match="target Street/market"):
        runtime(
            OrchestratorContext(
                "LEAK",
                ExecutionMode.LIVE_PRIMARY,
                {"live_wacc_result": live_wacc(), "current_market_price": 100000},
            )
        )
