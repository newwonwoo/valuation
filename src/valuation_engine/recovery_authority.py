from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .llm_staff import RedTeamProposal
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceSourceLayer
from .runtime_authority import llm_proposal_scope


@dataclass(frozen=True)
class RecoveryResolutionReceipt:
    run_id: str
    original_blocker_ids: tuple[str, ...]
    resolution_evidence_ids: tuple[str, ...]
    evidence_hash: str
    receipt_hash: str

    def validate(self) -> None:
        if not self.run_id or not self.original_blocker_ids:
            raise ValueError("recovery resolution receipt identity is incomplete")
        if not self.resolution_evidence_ids or not self.evidence_hash:
            raise ValueError("recovery resolution requires new evidence lineage")
        expected = _receipt_hash(
            run_id=self.run_id,
            original_blocker_ids=self.original_blocker_ids,
            resolution_evidence_ids=self.resolution_evidence_ids,
            evidence_hash=self.evidence_hash,
        )
        if self.receipt_hash != expected:
            raise PermissionError("recovery resolution receipt hash mismatch")


def proposal_only_recovery_adapter(inner: StageAdapter | None) -> StageAdapter | None:
    """Run the external recovery provider under LLM proposal-only authority."""
    if inner is None:
        return None

    def run(context: OrchestratorContext) -> StageExecutionResult:
        with llm_proposal_scope():
            return inner(context)

    return run


def deterministic_recovery_readjudication_adapter(inner: StageAdapter) -> StageAdapter:
    """Require evidence-backed deterministic re-adjudication after recovery.

    The inner legacy recovery adapter may validate shape and retain blocker IDs,
    but a recovered LLM/provider flag is not sufficient. If there were original
    unresolved blockers, the strict runtime requires explicit resolution Evidence
    already present in the canonical EvidenceLedger and emits an immutable receipt.
    """

    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = inner(context)
        original = context.data.get("red_team_proposal")
        if not isinstance(original, RedTeamProposal):
            return result
        blocker_ids = tuple(
            item.id for item in original.issues if item.blocking and not item.resolved
        )
        if not blocker_ids:
            return result
        if result.blocking or result.status not in {StageStatus.RECOVERED, StageStatus.PASS}:
            return result

        recovered = result.outputs.get("recovered_red_team_proposal")
        if not isinstance(recovered, RedTeamProposal):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "strict recovery re-adjudication requires recovered_red_team_proposal",
                blocking=True,
            )
        recovered_by_id = {item.id: item for item in recovered.issues}
        unresolved = tuple(
            issue_id
            for issue_id in blocker_ids
            if issue_id not in recovered_by_id or not recovered_by_id[issue_id].resolved
        )
        if unresolved:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "deterministic recovery re-adjudication found unresolved original blockers: "
                + ", ".join(unresolved),
                blocking=True,
            )

        evidence_ids = result.outputs.get("recovery_resolution_evidence_ids")
        if not isinstance(evidence_ids, tuple) or not evidence_ids or not all(
            isinstance(item, str) and item for item in evidence_ids
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "LLM/provider resolved flags are insufficient; recovery must emit recovery_resolution_evidence_ids",
                blocking=True,
            )
        ledger = context.data.get("evidence_ledger")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "EvidenceLedger missing for deterministic recovery re-adjudication",
                blocking=True,
            )
        parts: list[str] = []
        try:
            for evidence_id in tuple(dict.fromkeys(evidence_ids)):
                evidence = ledger.get(evidence_id)
                if evidence.source_layer is EvidenceSourceLayer.MARKET_COMPARISON:
                    raise PermissionError(
                        "post-freeze market evidence cannot resolve a pre-freeze blocker"
                    )
                parts.append(
                    "|".join(
                        (
                            evidence.id,
                            evidence.metric,
                            str(evidence.value),
                            evidence.unit,
                            evidence.effective_date,
                            evidence.observed_date,
                            evidence.source_ref,
                        )
                    )
                )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"recovery resolution evidence validation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        normalized_evidence_ids = tuple(dict.fromkeys(evidence_ids))
        evidence_hash = sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()
        receipt = RecoveryResolutionReceipt(
            run_id=context.run_id,
            original_blocker_ids=blocker_ids,
            resolution_evidence_ids=normalized_evidence_ids,
            evidence_hash=evidence_hash,
            receipt_hash=_receipt_hash(
                run_id=context.run_id,
                original_blocker_ids=blocker_ids,
                resolution_evidence_ids=normalized_evidence_ids,
                evidence_hash=evidence_hash,
            ),
        )
        receipt.validate()
        outputs = dict(result.outputs)
        outputs["recovery_resolution_receipt"] = receipt
        outputs["recovery_resolution_receipt_hash"] = receipt.receipt_hash
        return StageExecutionResult(
            StageStatus.RECOVERED,
            result.rationale + "; deterministic evidence-backed re-adjudication PASS",
            outputs,
            blocking=False,
        )

    return run


def _receipt_hash(
    *,
    run_id: str,
    original_blocker_ids: tuple[str, ...],
    resolution_evidence_ids: tuple[str, ...],
    evidence_hash: str,
) -> str:
    payload = {
        "contract": "recovery_resolution_receipt/v1",
        "run_id": run_id,
        "original_blocker_ids": original_blocker_ids,
        "resolution_evidence_ids": resolution_evidence_ids,
        "evidence_hash": evidence_hash,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
