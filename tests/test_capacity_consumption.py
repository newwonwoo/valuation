from __future__ import annotations

import pytest

from valuation_engine.capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentAssessment,
    CapacityProjectAssessment,
    CapacityProjectDisposition,
    CapacityQuantificationStatus,
    CapacitySegmentAssessment,
)
from valuation_engine.capacity_consumption import (
    CapacityBridgeBinding,
    CapacityBridgeConsumptionContract,
    CapacityBridgeRole,
    capacity_bridge_consumption_gate_adapter,
    validate_capacity_bridge_consumption,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
)
from valuation_engine.signal_intelligence import ProjectGate


def assessment(*, core_required=True, recovery_required=False):
    project = CapacityProjectAssessment(
        project_id="P1",
        segment_id="core",
        verified_gates=(ProjectGate.ANNOUNCEMENT, ProjectGate.LAND_CONTROL),
        land_control_verified=True,
        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,
        disposition=CapacityProjectDisposition.ACTIVE,
        core_inclusion_required=core_required,
        quantification_status=(
            CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE
            if core_required
            else CapacityQuantificationStatus.NOT_REQUIRED
        ),
        qualifying_evidence_ids=("E_LAND", "E_SITE", "E_CAPEX", "E_RAMP"),
        recovery_required=recovery_required,
        rationale="test capacity project",
    )
    segment = CapacitySegmentAssessment(
        segment_id="core",
        projects=(project,),
        no_active_expansion_verified=False,
        no_active_expansion_evidence_ids=(),
        recovery_required=recovery_required,
        rationale="test segment",
    )
    return CapacityCommitmentAssessment((segment,), "ASSESSMENT-HASH")


def bridge(
    bridge_id: str,
    evidence_ids: tuple[str, ...],
    *,
    old_value: float,
    new_value: float,
    path="capacity_project:P1",
) -> BridgeRecord:
    return BridgeRecord(
        id=bridge_id,
        evidence_ids=evidence_ids,
        hypothesis_id=f"H-{bridge_id}",
        affected_variable=AffectedVariable.QUANTITY,
        direction=Direction.UP,
        old_value=old_value,
        new_value=new_value,
        unit="dimensionless",
        rationale="capacity project bridge",
        confidence=0.8,
        kill_condition="project cancelled",
        verification_event="next filing",
        economic_path_id=path,
    )


def bridges(*, ramp_path="capacity_project:P1", capacity_new=120, capex_new=420):
    return (
        bridge(
            "B_CAPACITY",
            ("E_LAND", "E_SITE"),
            old_value=100,
            new_value=capacity_new,
        ),
        bridge(
            "B_CAPEX",
            ("E_LAND", "E_CAPEX"),
            old_value=0,
            new_value=capex_new,
        ),
        bridge(
            "B_RAMP",
            ("E_LAND", "E_RAMP"),
            old_value=0,
            new_value=2,
            path=ramp_path,
        ),
    )


def contract(*, assessment_hash="ASSESSMENT-HASH", include_ramp=True):
    values = [
        CapacityBridgeBinding(
            "P1",
            CapacityBridgeRole.CAPACITY,
            "B_CAPACITY",
            ("E_LAND", "E_SITE"),
        ),
        CapacityBridgeBinding(
            "P1",
            CapacityBridgeRole.CAPEX,
            "B_CAPEX",
            ("E_CAPEX",),
        ),
    ]
    if include_ramp:
        values.append(
            CapacityBridgeBinding(
                "P1",
                CapacityBridgeRole.RAMP,
                "B_RAMP",
                ("E_RAMP",),
            )
        )
    return CapacityBridgeConsumptionContract(assessment_hash, tuple(values))


def test_core_capacity_requires_capacity_capex_and_ramp_consumption():
    result = validate_capacity_bridge_consumption(
        assessment=assessment(),
        bridges=bridges(),
        contract=contract(),
    )

    assert result.consumed_project_ids == ("P1",)
    assert result.project_economic_paths == (("P1", "capacity_project:P1"),)
    assert result.bridge_ids == ("B_CAPACITY", "B_CAPEX", "B_RAMP")
    assert result.consumption_hash


def test_missing_ramp_bridge_blocks_material_evidence_omission():
    with pytest.raises(ValueError, match="missing bridge roles: ramp"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=bridges(),
            contract=contract(include_ramp=False),
        )


def test_contract_must_bind_to_the_frozen_capacity_assessment():
    with pytest.raises(ValueError, match="assessment_hash"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=bridges(),
            contract=contract(assessment_hash="WRONG"),
        )


def test_bridge_must_consume_the_required_project_evidence():
    broken = (
        bridge(
            "B_CAPACITY",
            ("E_LAND",),
            old_value=100,
            new_value=120,
        ),
        *bridges()[1:],
    )
    with pytest.raises(ValueError, match="omits required Evidence: E_SITE"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=broken,
            contract=contract(),
        )


def test_capacity_capex_and_ramp_must_share_one_project_economic_path():
    with pytest.raises(ValueError, match="share one project economic_path_id"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=bridges(ramp_path="different_path"),
            contract=contract(),
        )


def test_capacity_bridge_must_raise_capacity():
    with pytest.raises(ValueError, match="must increase capacity"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=bridges(capacity_new=100),
            contract=contract(),
        )


def test_capex_bridge_must_carry_positive_expansion_capex():
    with pytest.raises(ValueError, match="positive expansion CAPEX"):
        validate_capacity_bridge_consumption(
            assessment=assessment(),
            bridges=bridges(capex_new=0),
            contract=contract(),
        )


def test_non_core_project_cannot_receive_a_capacity_bridge_binding():
    with pytest.raises(ValueError, match="non-Core/unknown project"):
        validate_capacity_bridge_consumption(
            assessment=assessment(core_required=False),
            bridges=bridges(),
            contract=contract(),
        )


def test_bridge_consumption_gate_skips_when_no_project_is_core_required():
    stage = capacity_bridge_consumption_gate_adapter(loader=None)(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"capacity_commitment_assessment": assessment(core_required=False)},
        )
    )

    assert stage.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert not stage.outputs["capacity_bridge_consumption_required"]


def test_bridge_consumption_loader_is_mandatory_for_core_projects():
    stage = capacity_bridge_consumption_gate_adapter(loader=None)(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {
                "capacity_commitment_assessment": assessment(),
                "bridges": bridges(),
            },
        )
    )

    assert stage.status is StageStatus.NOT_IMPLEMENTED
    assert stage.blocking


def test_bridge_consumption_adapter_passes_complete_contract():
    stage = capacity_bridge_consumption_gate_adapter(loader=lambda _: contract())(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {
                "capacity_commitment_assessment": assessment(),
                "bridges": bridges(),
            },
        )
    )

    assert stage.status is StageStatus.PASS
    assert stage.outputs["capacity_bridge_consumption_hash"]
