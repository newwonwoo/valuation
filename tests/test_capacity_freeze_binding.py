from types import SimpleNamespace

from valuation_engine.capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentAssessment,
    CapacityProjectAssessment,
    CapacityProjectDisposition,
    CapacityQuantificationStatus,
    CapacitySegmentAssessment,
)
from valuation_engine.capacity_freeze_binding import (
    build_capacity_freeze_binding,
    capacity_freeze_binding_adapter,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.signal_intelligence import ProjectGate


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


def context(*, audit_hash="AUDIT-HASH", token_hash="FREEZE-HASH"):
    return OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "capacity_commitment_assessment": assessment(),
            "capacity_audit_hash": audit_hash,
        },
        [],
        SimpleNamespace(token_hash=token_hash),
    )


def test_capacity_freeze_binding_combines_audit_and_freeze_identities():
    result = capacity_freeze_binding_adapter()(context())
    assert result.status is StageStatus.PASS
    binding = result.outputs["capacity_freeze_binding_result"]
    assert binding.capacity_audit_hash == "AUDIT-HASH"
    assert binding.freeze_token_hash == "FREEZE-HASH"
    assert result.outputs["capacity_freeze_binding_hash"] == binding.binding_hash


def test_capacity_freeze_binding_changes_when_audit_or_freeze_changes():
    base = build_capacity_freeze_binding(
        assessment=assessment(),
        capacity_audit_hash="AUDIT-1",
        freeze_token=SimpleNamespace(token_hash="FREEZE-1"),
    )
    changed_audit = build_capacity_freeze_binding(
        assessment=assessment(),
        capacity_audit_hash="AUDIT-2",
        freeze_token=SimpleNamespace(token_hash="FREEZE-1"),
    )
    changed_freeze = build_capacity_freeze_binding(
        assessment=assessment(),
        capacity_audit_hash="AUDIT-1",
        freeze_token=SimpleNamespace(token_hash="FREEZE-2"),
    )
    assert len({base.binding_hash, changed_audit.binding_hash, changed_freeze.binding_hash}) == 3


def test_capacity_freeze_binding_blocks_missing_audit_identity():
    result = capacity_freeze_binding_adapter()(context(audit_hash=""))
    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "capacity_audit_hash" in result.rationale


def test_capacity_freeze_binding_blocks_missing_freeze_token():
    result = capacity_freeze_binding_adapter()(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {
                "capacity_commitment_assessment": assessment(),
                "capacity_audit_hash": "AUDIT-HASH",
            },
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "Freeze token" in result.rationale
