from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import ExecutionMode, IntrinsicFreezeToken, StageStatus
from .orchestrator import (
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
    load_stage_sequence,
)
from .records import AuditReport
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import selected_methods_require_discount_rate


_ACCEPTABLE_STATUSES = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


def _stable_hash(payload: dict) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ExecutionAttestation:
    run_id: str
    execution_mode: str
    expected_stage_prefix: tuple[str, ...]
    observed_stage_prefix: tuple[str, ...]
    stage_statuses: tuple[tuple[str, str], ...]
    ledger_snapshot_hash: str
    assumption_set_hash: str
    scenario_set_hash: str
    beta_snapshot_hash: str | None
    wacc_snapshot_hash: str | None
    capacity_assessment_hash: str
    capacity_audit_hash: str
    valuation_hash: str
    audit_hash: str
    freeze_token_hash: str
    attestation_hash: str

    def __post_init__(self) -> None:
        required = (
            self.run_id,
            self.execution_mode,
            self.ledger_snapshot_hash,
            self.assumption_set_hash,
            self.scenario_set_hash,
            self.capacity_assessment_hash,
            self.capacity_audit_hash,
            self.valuation_hash,
            self.audit_hash,
            self.freeze_token_hash,
            self.attestation_hash,
        )
        if any(not value for value in required):
            raise ValueError("execution attestation requires all mandatory identities")
        if self.expected_stage_prefix != self.observed_stage_prefix:
            raise ValueError("execution attestation stage prefixes must match")


def build_execution_attestation(
    context: OrchestratorContext,
    *,
    stage_registry_path: str | Path,
) -> ExecutionAttestation:
    if context.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        raise PermissionError("publishable execution attestation requires LIVE_PRIMARY")

    sequence = load_stage_sequence(stage_registry_path)
    try:
        save_index = sequence.index("SAVE_STATE")
    except ValueError as exc:
        raise ValueError("canonical stage registry has no SAVE_STATE") from exc
    expected = sequence[:save_index]
    observed = tuple(item.stage for item in context.stage_traces)
    if observed != expected:
        raise ValueError(
            "observed stage prefix does not match canonical pre-SAVE_STATE sequence: "
            f"expected={expected}, observed={observed}"
        )
    unacceptable = tuple(
        f"{item.stage}:{item.status.value}"
        for item in context.stage_traces
        if item.status not in _ACCEPTABLE_STATUSES or item.blocking
    )
    if unacceptable:
        raise ValueError(
            "execution attestation found non-acceptable stage traces: "
            + ", ".join(unacceptable)
        )

    audit = context.data.get("generic_audit_report")
    if not isinstance(audit, AuditReport) or not audit.passed:
        raise ValueError("passed Generic Audit is required for execution attestation")
    if not bool(context.data.get("audit_passed")):
        raise ValueError("audit_passed flag is required for execution attestation")
    capacity_audit = context.data.get("capacity_audit_report")
    if not isinstance(capacity_audit, AuditReport) or not capacity_audit.passed:
        raise ValueError("passed Capacity Audit is required for execution attestation")
    capacity_assessment = context.data.get("capacity_commitment_assessment")
    if not isinstance(capacity_assessment, CapacityCommitmentAssessment):
        raise ValueError(
            "typed CapacityCommitmentAssessment is required for execution attestation"
        )
    token = context.data.get("intrinsic_freeze_token")
    if not isinstance(token, IntrinsicFreezeToken):
        raise ValueError("IntrinsicFreezeToken is required for execution attestation")
    if token.run_id != context.run_id:
        raise ValueError("freeze token run_id does not match attested run")

    hashes: dict[str, str] = {}
    for key in (
        "ledger_snapshot_hash",
        "assumption_set_hash",
        "scenario_set_hash",
        "valuation_hash",
        "audit_hash",
        "capacity_audit_hash",
    ):
        value = context.data.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"execution attestation missing {key}")
        hashes[key] = value
    if token.ledger_snapshot_hash != hashes["ledger_snapshot_hash"]:
        raise ValueError("freeze token ledger hash does not match runtime")
    if token.assumption_set_hash != hashes["assumption_set_hash"]:
        raise ValueError("freeze token assumption hash does not match runtime")
    if token.valuation_hash != hashes["valuation_hash"]:
        raise ValueError("freeze token valuation hash does not match runtime")
    if token.audit_hash != hashes["audit_hash"]:
        raise ValueError("freeze token audit hash does not match runtime")

    selected_methods = context.data.get("selected_methods", ())
    if not isinstance(selected_methods, tuple) or not all(
        isinstance(item, str) for item in selected_methods
    ):
        raise ValueError("selected_methods must be a string tuple")
    risk_required = selected_methods_require_discount_rate(selected_methods)
    beta = context.data.get("live_beta_result")
    wacc = context.data.get("live_wacc_result")
    beta_hash: str | None = None
    wacc_hash: str | None = None
    if risk_required:
        if not isinstance(beta, LiveBetaStageResult):
            raise ValueError("discount-rate method requires executed Beta stage")
        if not isinstance(wacc, LiveWACCStageResult):
            raise ValueError("discount-rate method requires executed WACC stage")
        if wacc.beta_result.snapshot_hash != beta.snapshot_hash:
            raise ValueError("Beta and WACC snapshots are not from one risk chain")
        beta_hash = beta.snapshot_hash
        wacc_hash = wacc.snapshot_hash
    else:
        if isinstance(beta, LiveBetaStageResult):
            beta_hash = beta.snapshot_hash
        if isinstance(wacc, LiveWACCStageResult):
            wacc_hash = wacc.snapshot_hash

    stage_statuses = tuple(
        (item.stage, item.status.value) for item in context.stage_traces
    )
    payload = {
        "contract": "live_primary_execution_attestation/v1",
        "run_id": context.run_id,
        "execution_mode": context.execution_mode.value,
        "expected_stage_prefix": expected,
        "observed_stage_prefix": observed,
        "stage_statuses": stage_statuses,
        "ledger_snapshot_hash": hashes["ledger_snapshot_hash"],
        "assumption_set_hash": hashes["assumption_set_hash"],
        "scenario_set_hash": hashes["scenario_set_hash"],
        "beta_snapshot_hash": beta_hash,
        "wacc_snapshot_hash": wacc_hash,
        "capacity_assessment_hash": capacity_assessment.assessment_hash,
        "capacity_audit_hash": hashes["capacity_audit_hash"],
        "valuation_hash": hashes["valuation_hash"],
        "audit_hash": hashes["audit_hash"],
        "freeze_token_hash": token.token_hash,
    }
    return ExecutionAttestation(
        run_id=context.run_id,
        execution_mode=context.execution_mode.value,
        expected_stage_prefix=expected,
        observed_stage_prefix=observed,
        stage_statuses=stage_statuses,
        ledger_snapshot_hash=hashes["ledger_snapshot_hash"],
        assumption_set_hash=hashes["assumption_set_hash"],
        scenario_set_hash=hashes["scenario_set_hash"],
        beta_snapshot_hash=beta_hash,
        wacc_snapshot_hash=wacc_hash,
        capacity_assessment_hash=capacity_assessment.assessment_hash,
        capacity_audit_hash=hashes["capacity_audit_hash"],
        valuation_hash=hashes["valuation_hash"],
        audit_hash=hashes["audit_hash"],
        freeze_token_hash=token.token_hash,
        attestation_hash=_stable_hash(payload),
    )


def execution_attestation_adapter(
    *,
    stage_registry_path: str | Path,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            attestation = build_execution_attestation(
                context,
                stage_registry_path=stage_registry_path,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"execution attestation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "LIVE_PRIMARY stage trace, risk chain, capacity chain, audit and Freeze were attested",
            {
                "execution_attestation": attestation,
                "execution_attestation_hash": attestation.attestation_hash,
            },
        )

    return run
