from decimal import Decimal

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
from valuation_engine.capacity_runtime import (
    capacity_audit_adapter,
    capacity_consistency_gate_adapter,
    capacity_per_binding_adapter,
    capacity_scenario_binding_adapter,
    capacity_valuation_binding_adapter,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.signal_intelligence import ProjectGate
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.valuation_execution import (
    GenericValuationResult,
    ScenarioPerShareValue,
)


ROLE_ROWS = (
    ("P1", "core", "capacity", "B_CAPACITY", "capacity_project:P1:capacity"),
    ("P1", "core", "capex", "B_CAPEX", "capacity_project:P1:capex"),
    ("P1", "core", "ramp", "B_RAMP", "capacity_project:P1:ramp"),
)


def assessment() -> CapacityCommitmentAssessment:
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
        rationale="source-backed fixture",
    )
    return CapacityCommitmentAssessment(
        (
            CapacitySegmentAssessment(
                segment_id="core",
                projects=(project,),
                no_active_expansion_verified=False,
                no_active_expansion_evidence_ids=(),
                recovery_required=False,
                rationale="fixture",
            ),
        ),
        "ASSESSMENT",
    )


def consumption() -> CapacityBridgeConsumptionResult:
    return CapacityBridgeConsumptionResult(
        assessment_hash="ASSESSMENT",
        consumed_project_ids=("P1",),
        project_economic_paths=(("P1", "capacity_project:P1"),),
        role_bindings=ROLE_ROWS,
        bridge_ids=("B_CAPACITY", "B_CAPEX", "B_RAMP"),
        consumption_hash="CONSUMPTION",
    )


def compiled(*, include_ramp=True) -> CompiledAssumptionSet:
    rows = ROLE_ROWS if include_ramp else ROLE_ROWS[:-1]
    assumptions = tuple(
        CompiledAssumption(
            key=f"{role}_input",
            scenario_id="Core",
            measure=Measure(
                Decimal("1"),
                "KRW" if role in {"capacity", "capex"} else "ratio",
                "2026-06-30",
            ),
            bridge_id=bridge_id,
            evidence_ids=("E_LAND",),
            hypothesis_id=f"H_{role}",
            economic_path_id=path,
            transform_id="identity_observation",
            input_evidence_hash=f"HASH_{role}",
        )
        for _project, _segment, role, bridge_id, path in rows
    )
    return CompiledAssumptionSet("SANIL", assumptions, "ASSUMPTIONS")


def scenarios(compiled_set: CompiledAssumptionSet) -> BoundScenarioSet:
    return BoundScenarioSet(
        target_id="SANIL",
        scenarios=(BoundScenario("Core", compiled_set.assumptions, None),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIOS",
    )


def valuation(paths=tuple(row[4] for row in ROLE_ROWS)) -> GenericValuationResult:
    per_share = ScenarioPerShareValue(
        scenario_id="Core",
        equity_value_amount=Decimal("100"),
        reporting_unit="KRW",
        diluted_shares=Decimal("1"),
        value_per_share=Decimal("100"),
        aggregation_hash="AGG",
        economic_path_ids=paths,
    )
    return GenericValuationResult(
        scenarios=(per_share,),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None,
        reporting_unit="KRW",
        valuation_hash="VALUATION",
    )


def base_data():
    compiled_set = compiled()
    return {
        "capacity_commitment_assessment": assessment(),
        "capacity_bridge_consumption_result": consumption(),
        "compiled_assumption_set": compiled_set,
        "bound_scenario_set": scenarios(compiled_set),
    }


def test_capacity_runtime_binds_scenario_valuation_per_and_audit():
    data = base_data()
    scenario_stage = capacity_scenario_binding_adapter(core_scenario_id="Core")(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    assert scenario_stage.status is StageStatus.PASS
    data.update(scenario_stage.outputs)
    data["generic_valuation_result"] = valuation()

    valuation_stage = capacity_valuation_binding_adapter()(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    assert valuation_stage.status is StageStatus.PASS
    data.update(valuation_stage.outputs)
    data["warranted_per_applicable"] = False

    per_stage = capacity_per_binding_adapter()(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    assert per_stage.status is StageStatus.SKIPPED_NOT_APPLICABLE
    data.update(per_stage.outputs)

    consistency = capacity_consistency_gate_adapter()(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    assert consistency.status is StageStatus.PASS
    data.update(consistency.outputs)

    audit = capacity_audit_adapter()(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    assert audit.status is StageStatus.PASS
    assert audit.outputs["capacity_audit_report"].passed


def test_capacity_scenario_binding_blocks_omitted_ramp_path():
    data = base_data()
    incomplete = compiled(include_ramp=False)
    data["compiled_assumption_set"] = incomplete
    data["bound_scenario_set"] = scenarios(incomplete)

    result = capacity_scenario_binding_adapter(core_scenario_id="Core")(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )

    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "ramp" in result.rationale


def test_capacity_valuation_binding_blocks_unconsumed_capex_path():
    data = base_data()
    scenario_stage = capacity_scenario_binding_adapter(core_scenario_id="Core")(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )
    data.update(scenario_stage.outputs)
    data["generic_valuation_result"] = valuation(
        tuple(
            path
            for path in (row[4] for row in ROLE_ROWS)
            if not path.endswith(":capex")
        )
    )

    result = capacity_valuation_binding_adapter()(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )

    assert result.status is StageStatus.BLOCKED
    assert ":capex" in result.rationale
