from decimal import Decimal
from types import SimpleNamespace

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import (
    CompiledAssumption,
    CompiledAssumptionSet,
)
from valuation_engine.capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentAssessment,
    CapacityProjectAssessment,
    CapacityProjectDisposition,
    CapacityQuantificationStatus,
    CapacitySegmentAssessment,
)
from valuation_engine.capacity_consumption import CapacityBridgeConsumptionResult
from valuation_engine.capacity_runtime_integrity import (
    CapacityPERBindingResult,
    capacity_audit_adapter,
    capacity_per_binding_adapter,
    capacity_scenario_binding_adapter,
    capacity_valuation_binding_adapter,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.records import CalibrationStatus
from valuation_engine.signal_intelligence import ProjectGate
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.valuation_execution import (
    GenericValuationResult,
    ScenarioPerShareValue,
)


def assessment():
    project = CapacityProjectAssessment(
        project_id="P1",
        segment_id="core",
        verified_gates=(ProjectGate.ANNOUNCEMENT, ProjectGate.LAND_CONTROL),
        land_control_verified=True,
        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,
        disposition=CapacityProjectDisposition.ACTIVE,
        core_inclusion_required=True,
        quantification_status=CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE,
        qualifying_evidence_ids=("E_LAND", "E_SITE", "E_CAPEX", "E_RAMP"),
        recovery_required=False,
        rationale="fixture",
    )
    return CapacityCommitmentAssessment(
        segments=(
            CapacitySegmentAssessment(
                segment_id="core",
                projects=(project,),
                no_active_expansion_verified=False,
                no_active_expansion_evidence_ids=(),
                recovery_required=False,
                rationale="fixture",
            ),
        ),
        assessment_hash="ASSESSMENT-HASH",
    )


def compiled(path="capacity_project:P1"):
    items = tuple(
        CompiledAssumption(
            key=f"capacity_{index}",
            scenario_id="core",
            measure=Measure(Decimal(str(index + 1)), "count", "2026-08-26"),
            bridge_id=bridge_id,
            evidence_ids=("E_LAND",),
            hypothesis_id=f"H{index}",
            economic_path_id=path,
            transform_id="identity_observation",
            input_evidence_hash=f"HASH{index}",
        )
        for index, bridge_id in enumerate(("B_CAPACITY", "B_CAPEX", "B_RAMP"))
    )
    return CompiledAssumptionSet("T", items, "ASSUMPTION-HASH")


def consumption(path="capacity_project:P1"):
    return CapacityBridgeConsumptionResult(
        assessment_hash="ASSESSMENT-HASH",
        consumed_project_ids=("P1",),
        project_economic_paths=(("P1", path),),
        bridge_ids=("B_CAPACITY", "B_CAPEX", "B_RAMP"),
        consumption_hash="CONSUMPTION-HASH",
    )


def scenario_set(compiled_set=None):
    compiled_set = compiled_set or compiled()
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("core", compiled_set.assumptions, None),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIO-HASH",
        calibration_snapshot_hash=None,
    )


def valuation(path="capacity_project:P1"):
    row = ScenarioPerShareValue(
        scenario_id="core",
        equity_value_amount=Decimal("100"),
        reporting_unit="KRW",
        diluted_shares=Decimal("10"),
        value_per_share=Decimal("10"),
        aggregation_hash="AGG",
        economic_path_ids=(path,),
    )
    return GenericValuationResult(
        scenarios=(row,),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None,
        reporting_unit="KRW",
        valuation_hash="VALUATION-HASH",
    )


def context(data):
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data)


def test_capacity_paths_bind_scenario_then_valuation_then_audit():
    a = assessment()
    c = compiled()
    scenario = capacity_scenario_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": a,
                "capacity_bridge_consumption_result": consumption(),
                "compiled_assumption_set": c,
                "bound_scenario_set": scenario_set(c),
            }
        )
    )
    assert scenario.status is StageStatus.PASS
    value = capacity_valuation_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": a,
                **scenario.outputs,
                "generic_valuation_result": valuation(),
            }
        )
    )
    assert value.status is StageStatus.PASS
    audit = capacity_audit_adapter()(
        context(
            {
                "capacity_commitment_assessment": a,
                "capacity_bridge_consumption_result": consumption(),
                **scenario.outputs,
                **value.outputs,
                "warranted_per_applicable": False,
            }
        )
    )
    assert audit.status is StageStatus.PASS
    assert audit.outputs["capacity_audit_result"].passed
    assert audit.outputs["capacity_audit_hash"]


def test_scenario_binding_blocks_an_omitted_capacity_bridge():
    c = compiled()
    broken = CompiledAssumptionSet("T", c.assumptions[:-1], "BROKEN")
    result = capacity_scenario_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": assessment(),
                "capacity_bridge_consumption_result": consumption(),
                "compiled_assumption_set": broken,
                "bound_scenario_set": scenario_set(broken),
            }
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert "compile exactly once" in result.rationale


def test_valuation_binding_blocks_zero_expansion_path_omission():
    a = assessment()
    c = compiled()
    scenario = capacity_scenario_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": a,
                "capacity_bridge_consumption_result": consumption(),
                "compiled_assumption_set": c,
                "bound_scenario_set": scenario_set(c),
            }
        )
    )
    result = capacity_valuation_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": a,
                **scenario.outputs,
                "generic_valuation_result": valuation("different_path"),
            }
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert "omitted capacity economic paths" in result.rationale


def test_core_capacity_evidence_cannot_reopen_expansion_per():
    result = capacity_per_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": assessment(),
                "warranted_per_applicable": True,
                "live_warranted_per_result": SimpleNamespace(
                    expansion_evidence_ids=("E_LAND",)
                ),
                "per_snapshot_hash": "PER-HASH",
            }
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert "cannot be reused" in result.rationale


def test_separate_incremental_evidence_allows_capacity_per_binding():
    result = capacity_per_binding_adapter()(
        context(
            {
                "capacity_commitment_assessment": assessment(),
                "warranted_per_applicable": True,
                "live_warranted_per_result": SimpleNamespace(
                    expansion_evidence_ids=("E_INCREMENTAL",)
                ),
                "per_snapshot_hash": "PER-HASH",
            }
        )
    )
    assert result.status is StageStatus.PASS
    assert isinstance(
        result.outputs["capacity_per_binding_result"],
        CapacityPERBindingResult,
    )
