from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from .collection_plan import PrimaryCollectionPlan
from .control_plane import StageStatus
from .evidence_collection import EvidenceCollector, collect_primary_evidence
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


@dataclass(frozen=True)
class EvidenceCollectorSelection:
    plan: PrimaryCollectionPlan
    collectors: tuple[EvidenceCollector, ...]


EvidenceCollectorSelectionLoader = Callable[[OrchestratorContext], EvidenceCollectorSelection]


def primary_evidence_collection_adapter(
    *,
    collectors: tuple[EvidenceCollector, ...] = (),
    selection_loader: EvidenceCollectorSelectionLoader | None = None,
    strict_required_coverage: bool = True,
) -> StageAdapter:
    if selection_loader is not None and collectors:
        raise ValueError("primary evidence adapter accepts static collectors or a selection_loader, not both")
    if selection_loader is None and not collectors:
        raise ValueError("primary evidence adapter requires collectors or a selection_loader")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        required = context.data.get("required_evidence")
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "target_id missing for primary collection", blocking=True)
        if not isinstance(required, tuple) or not all(isinstance(item, str) and item for item in required):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "required_evidence missing from Module Requirement Plan", blocking=True)

        active_collectors = collectors
        collection_plan: PrimaryCollectionPlan | None = None
        if selection_loader is not None:
            try:
                selection = selection_loader(context)
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    f"primary evidence collector selection failed: {type(exc).__name__}: {exc}",
                    blocking=True,
                )
            if not isinstance(selection, EvidenceCollectorSelection):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "selection_loader must return EvidenceCollectorSelection",
                    blocking=True,
                )
            collection_plan = selection.plan
            if collection_plan.target_id != target_id:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan target_id does not match orchestration target",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            planned_required = tuple(item.metric for item in collection_plan.required_evidence)
            if planned_required != required:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan required metrics do not match Module Requirement Plan",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            active_collectors = selection.collectors
            if not active_collectors:
                return StageExecutionResult(
                    StageStatus.NOT_IMPLEMENTED,
                    "no runnable collector is available for the compiled primary-evidence plan",
                    {
                        "collection_plan": collection_plan,
                        "collection_missing_required_metrics": collection_plan.missing_required_metrics,
                        "collection_no_source_required_metrics": collection_plan.no_source_required_metrics,
                    },
                    blocking=True,
                )

        try:
            result = collect_primary_evidence(
                target_id=target_id,
                required_metrics=required,
                collectors=active_collectors,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"primary evidence collection failed: {type(exc).__name__}: {exc}",
                {"collection_plan": collection_plan} if collection_plan is not None else {},
                blocking=True,
            )

        outputs = {
            "evidence_collection_result": result,
            "evidence_ledger": result.ledger,
            "source_snapshot_hash": result.source_snapshot_hash,
            "evidence_covered_metrics": result.covered_metrics,
            "evidence_missing_metrics": result.missing_metrics,
        }
        if collection_plan is not None:
            outputs["collection_plan"] = collection_plan
            outputs["collection_missing_required_metrics"] = collection_plan.missing_required_metrics
            outputs["collection_no_source_required_metrics"] = collection_plan.no_source_required_metrics
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
