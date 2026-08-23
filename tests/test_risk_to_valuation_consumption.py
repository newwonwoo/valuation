from dataclasses import replace
from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
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
from valuation_engine.risk_impact import audit_risk_consumption, build_risk_impact_traces
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import CompanyValuationPlan, SegmentValuationPlan
from valuation_engine.wacc import WACCResult


def assumption(key: str, value: str, unit: str, path: str) -> CompiledAssumption:
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


def live_wacc() -> LiveWACCStageResult:
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
        WACCResult(0.10, 0.04, 0.75, 0.25, 0.08),
        None,
        ("WACC:SOURCE",),
        ("EV-CREDIT-1",),
        False,
        "WACC-HASH",
    )


def dcf_result():
    assumptions = (
        assumption("core_fcff_year_1", "10", "KRW_billion", "fcff1"),
        assumption("core_fcff_year_2", "12", "KRW_billion", "fcff2"),
        assumption("core_fcff_year_3", "14", "KRW_billion", "fcff3"),
        assumption("core_terminal_growth", "0.025", "ratio", "terminal-growth"),
        assumption("core_terminal_roic", "0.12", "ratio", "terminal-roic"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("net_debt", "0", "KRW_billion", "net-debt"),
        assumption("shares", "10", "shares", "dilution"),
    )
    bound = BoundScenarioSet(
        "T",
        (BoundScenario("BASE", assumptions),),
        CalibrationStatus.UNCALIBRATED,
        False,
        "SCENARIO-HASH",
    )
    plan = CompanyValuationPlan(
        (
            SegmentValuationPlan(
                "asset",
                "core",
                ModelKey("capacity_manufacturing", "driver_dcf", "core-v1"),
                "ownership",
                "net_debt",
            ),
        ),
        "KRW_billion",
        "shares",
    )
    wacc = live_wacc()
    stage = deterministic_valuation_adapter(
        plan=plan,
        registry_loader=live_fcff_dcf_registry_loader(
            registrations=(LiveDCFRegistration("capacity_manufacturing", "driver_dcf", "core-v1", 3, "core_"),)
        ),
    )(
        OrchestratorContext(
            "RISK-CONSUMPTION",
            ExecutionMode.LIVE_PRIMARY,
            {"bound_scenario_set": bound, "live_wacc_result": wacc},
        )
    )
    return stage.outputs["generic_valuation_result"], wacc


def test_live_dcf_carries_beta_and_wacc_to_final_valuation_path():
    valuation, wacc = dcf_result()
    paths = valuation.scenarios[0].economic_path_ids
    assert "beta:BETA-HASH:core" in paths
    assert "wacc:WACC-HASH:core" in paths
    audit = audit_risk_consumption(
        valuation=valuation,
        selected_methods=("driver_dcf",),
        beta_result=wacc.beta_result,
        wacc_result=wacc,
    )
    assert audit.passed
    traces = build_risk_impact_traces(
        beta_result=wacc.beta_result,
        wacc_result=wacc,
        valuation=valuation,
        selected_methods=("driver_dcf",),
    )
    assert {item.module_id for item in traces} == {"HIERARCHICAL_BETA_ENGINE", "WACC_ENGINE"}


def test_missing_beta_path_fails_risk_consumption_audit():
    valuation, wacc = dcf_result()
    scenario = valuation.scenarios[0]
    stripped = replace(
        scenario,
        economic_path_ids=tuple(path for path in scenario.economic_path_ids if not path.startswith("beta:")),
    )
    broken = replace(valuation, scenarios=(stripped,))
    audit = audit_risk_consumption(
        valuation=broken,
        selected_methods=("driver_dcf",),
        beta_result=wacc.beta_result,
        wacc_result=wacc,
    )
    assert not audit.passed
    assert audit.missing_scenarios == ("BASE",)


def test_non_discount_method_does_not_require_risk_chain():
    valuation, _ = dcf_result()
    audit = audit_risk_consumption(
        valuation=valuation,
        selected_methods=("normalized_multiple",),
        beta_result=None,
        wacc_result=None,
    )
    assert audit.passed
    assert not audit.required
