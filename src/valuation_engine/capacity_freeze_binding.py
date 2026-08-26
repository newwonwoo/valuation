from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


@dataclass(frozen=True)
class CapacityFreezeBindingResult:
    assessment_hash: str
    capacity_audit_hash: str
    freeze_token_hash: str
    binding_hash: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.assessment_hash,
                self.capacity_audit_hash,
                self.freeze_token_hash,
                self.binding_hash,
            )
        ):
            raise ValueError("capacity Freeze binding requires complete hash identities")


def _freeze_token_hash(token: object) -> str:
    for name in (
        "token_hash",
        "freeze_hash",
        "intrinsic_hash",
        "run_hash",
    ):
        value = getattr(token, name, None)
        if isinstance(value, str) and value:
            return value
    value = str(token)
    if not value or value.startswith("<"):
        raise ValueError("Intrinsic Freeze token has no stable hash identity")
    return value


def build_capacity_freeze_binding(
    *,
    assessment: CapacityCommitmentAssessment,
    capacity_audit_hash: str,
    freeze_token: object,
) -> CapacityFreezeBindingResult:
    if not assessment.core_inclusion_required_projects:
        raise ValueError("capacity Freeze binding is only required for Core capacity projects")
    if not isinstance(capacity_audit_hash, str) or not capacity_audit_hash:
        raise ValueError("capacity_audit_hash is required before Freeze binding")
    token_hash = _freeze_token_hash(freeze_token)
    payload = {
        "contract": "capacity_freeze_binding/v1",
        "assessment_hash": assessment.assessment_hash,
        "capacity_audit_hash": capacity_audit_hash,
        "freeze_token_hash": token_hash,
    }
    binding_hash = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return CapacityFreezeBindingResult(
        assessment_hash=assessment.assessment_hash,
        capacity_audit_hash=capacity_audit_hash,
        freeze_token_hash=token_hash,
        binding_hash=binding_hash,
    )


def capacity_freeze_binding_adapter() -> StageAdapter:
    """Bind Capacity Audit to the immutable Freeze token before Street/market loading."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityCommitmentAssessment missing at post-Freeze boundary",
                blocking=True,
            )
        if not assessment.core_inclusion_required_projects:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core capacity project requires a Freeze binding certificate",
                {"capacity_freeze_binding_required": False},
            )
        capacity_audit_hash = context.data.get("capacity_audit_hash")
        if context.freeze_token is None:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Intrinsic Freeze token missing before Capacity Freeze binding",
                blocking=True,
            )
        try:
            result = build_capacity_freeze_binding(
                assessment=assessment,
                capacity_audit_hash=capacity_audit_hash,
                freeze_token=context.freeze_token,
            )
        except (TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Capacity Freeze binding failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Capacity Audit and Intrinsic Freeze identities were bound before Street load",
            {
                "capacity_freeze_binding_required": True,
                "capacity_freeze_binding_result": result,
                "capacity_freeze_binding_hash": result.binding_hash,
            },
        )

    return run
