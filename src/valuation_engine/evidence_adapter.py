from __future__ import annotations

from hashlib import sha256
import json

from .control_plane import StageStatus
from .evidence_collection import EvidenceCollector, collect_primary_evidence
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


def primary_evidence_collection_adapter(
    *,
    collectors: tuple[EvidenceCollector, ...],
    strict_required_coverage: bool = True,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        required = context.data.get("required_evidence")
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "target_id missing for primary collection", blocking=True)
        if not isinstance(required, tuple) or not all(isinstance(item, str) and item for item in required):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "required_evidence missing from Module Requirement Plan", blocking=True)
        try:
            result = collect_primary_evidence(
                target_id=target_id,
                required_metrics=required,
                collectors=collectors,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"primary evidence collection failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {
            "evidence_collection_result": result,
            "evidence_ledger": result.ledger,
            "source_snapshot_hash": result.source_snapshot_hash,
            "evidence_covered_metrics": result.covered_metrics,
            "evidence_missing_metrics": result.missing_metrics,
        }
        if result.missing_metrics:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED if strict_required_coverage else StageStatus.WARNING,
                "required primary evidence missing: " + ", ".join(result.missing_metrics),
                outputs,
                blocking=strict_required_coverage,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "primary evidence collected with complete required-metric coverage",
            outputs,
        )

    return run


def evidence_ledger_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        ledger = context.data.get("evidence_ledger")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger missing", blocking=True)
        active = ledger.active()
        if not active:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger has no active evidence", blocking=True)
        payload = ledger.to_list()
        ledger_hash = sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return StageExecutionResult(
            StageStatus.PASS,
            "append-only EvidenceLedger validated and snapshot hash frozen",
            {
                "ledger_snapshot_hash": ledger_hash,
                "active_evidence_ids": tuple(item.id for item in active),
            },
        )

    return run
