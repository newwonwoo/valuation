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
class SelectedEvidenceCollector:
    collector_id: str
    collector: EvidenceCollector

    def validate(self) -> None:
        if not self.collector_id:
            raise ValueError("selected evidence collector requires collector_id")
        if not callable(self.collector):
            raise TypeError(f"selected evidence collector {self.collector_id} is not callable")


@dataclass(frozen=True)
class EvidenceCollectorSelection:
    plan: PrimaryCollectionPlan
    collectors: tuple[SelectedEvidenceCollector, ...]


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
        selected_collectors: tuple[SelectedEvidenceCollector, ...] = ()
        selected_collector_ids: tuple[str, ...] = ()
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
            try:
                for item in selection.collectors:
                    item.validate()
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"selected evidence collector is invalid: {type(exc).__name__}: {exc}",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            selected_collectors = selection.collectors
            selected_collector_ids = tuple(item.collector_id for item in selected_collectors)
            if len(selected_collector_ids) != len(set(selected_collector_ids)):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collector selection contains duplicate collector IDs",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            unauthorized = tuple(
                sorted(set(selected_collector_ids) - set(collection_plan.runnable_collector_ids))
            )
            if unauthorized:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collector selection is not authorized by Collection Plan: " + ", ".join(unauthorized),
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            active_collectors = tuple(item.collector for item in selected_collectors)
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

        if collection_plan is not None:
            for selected, batch in zip(selected_collectors, result.batches, strict=True):
                authorized_metrics = set(
                    collection_plan.authorized_metrics_for_collector(selected.collector_id)
                )
                emitted_metrics = {record.metric for record in batch.records}
                unauthorized_metrics = tuple(sorted(emitted_metrics - authorized_metrics))
                if unauthorized_metrics:
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        f"collector {selected.collector_id} emitted metrics outside Collection Plan: "
                        + ", ".join(unauthorized_metrics),
                        {
                            "collection_plan": collection_plan,
                            "collection_selected_collector_ids": selected_collector_ids,
                        },
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
            outputs["collection_selected_collector_ids"] = selected_collector_ids
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
