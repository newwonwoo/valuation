from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from .collection_plan import (
    CompanyCollectionPlan,
    module_plan_collection_requirement_contract,
    module_plan_routing_hash,
    normalize_jurisdiction,
)
from .control_plane import StageStatus
from .evidence_collection import EvidenceCollector, collect_primary_evidence
from .ledger import EvidenceLedger
from .live_primary_adapters import ResolvedCompanyIdentity
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


@dataclass(frozen=True)
class SelectedEvidenceCollector:
    collector_id: str
    collector: EvidenceCollector

    def validate(self) -> None:
        if not self.collector_id:
            raise ValueError(
                "selected evidence collector requires collector_id"
            )
        if not callable(self.collector):
            raise TypeError(
                f"selected evidence collector {self.collector_id} is not callable"
            )


@dataclass(frozen=True)
class EvidenceCollectorSelection:
    plan: CompanyCollectionPlan
    collectors: tuple[SelectedEvidenceCollector, ...]


EvidenceCollectorSelectionLoader = Callable[
    [OrchestratorContext],
    EvidenceCollectorSelection,
]


def primary_evidence_collection_adapter(
    *,
    collectors: tuple[EvidenceCollector, ...] = (),
    selection_loader: EvidenceCollectorSelectionLoader | None = None,
    strict_required_coverage: bool = True,
) -> StageAdapter:
    if selection_loader is not None and collectors:
        raise ValueError(
            "primary evidence adapter accepts static collectors or a "
            "selection_loader, not both"
        )
    if selection_loader is None and not collectors:
        raise ValueError(
            "primary evidence adapter requires collectors or a selection_loader"
        )

    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        required = context.data.get("required_evidence")
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "target_id missing for primary collection",
                blocking=True,
            )
        if not isinstance(required, tuple) or not all(
            isinstance(item, str) and item for item in required
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "required_evidence missing from Module Requirement Plan",
                blocking=True,
            )

        active_collectors = collectors
        collection_plan: CompanyCollectionPlan | None = None
        selected_collectors: tuple[SelectedEvidenceCollector, ...] = ()
        selected_collector_ids: tuple[str, ...] = ()
        if selection_loader is not None:
            identity = context.data.get("resolved_company_identity")
            current_module_plan = context.data.get("module_requirement_plan")
            current_jurisdiction = context.data.get("jurisdiction")
            if not isinstance(identity, ResolvedCompanyIdentity):
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "resolved company identity is required before dynamic "
                    "primary collection",
                    blocking=True,
                )
            if not isinstance(current_module_plan, ModuleRequirementPlan):
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "current ModuleRequirementPlan is required before dynamic "
                    "primary collection",
                    blocking=True,
                )
            if (
                not isinstance(current_jurisdiction, str)
                or not current_jurisdiction
            ):
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "jurisdiction is required before dynamic primary collection",
                    blocking=True,
                )
            try:
                selection = selection_loader(context)
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "primary evidence collector selection failed: "
                    f"{type(exc).__name__}: {exc}",
                    blocking=True,
                )
            if not isinstance(selection, EvidenceCollectorSelection):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "selection_loader must return EvidenceCollectorSelection",
                    blocking=True,
                )
            collection_plan = selection.plan
            try:
                collection_plan.validate()
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"CompanyCollectionPlan is invalid: "
                    f"{type(exc).__name__}: {exc}",
                    blocking=True,
                )
            if collection_plan.company != identity:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan company identity does not match the "
                    "current resolved company identity",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            if normalize_jurisdiction(
                collection_plan.company.jurisdiction
            ) != normalize_jurisdiction(current_jurisdiction):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan jurisdiction does not match the current "
                    "resolved jurisdiction",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            current_routing_hash = module_plan_routing_hash(
                current_module_plan
            )
            if collection_plan.routing_hash != current_routing_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan routing hash does not match the current "
                    "Module Requirement Plan",
                    {
                        "collection_plan": collection_plan,
                        "current_module_plan_routing_hash": current_routing_hash,
                    },
                    blocking=True,
                )

            current_requirement_contract = (
                module_plan_collection_requirement_contract(
                    current_module_plan
                )
            )
            if (
                collection_plan.requirement_contract
                != current_requirement_contract
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan exact segment/metric/kind requirements do "
                    "not match the current Module Requirement Plan",
                    {
                        "collection_plan": collection_plan,
                        "current_collection_requirement_contract": (
                            current_requirement_contract
                        ),
                    },
                    blocking=True,
                )
            if collection_plan.company.target_id != target_id:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan target_id does not match orchestration target",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )

            current_required = tuple(current_module_plan.required_evidence)
            planned_required = tuple(
                dict.fromkeys(
                    item.metric for item in collection_plan.required_evidence
                )
            )
            if required != current_required or planned_required != current_required:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collection plan/context required metrics do not match the "
                    "current Module Requirement Plan",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            try:
                for item in selection.collectors:
                    item.validate()
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "selected evidence collector is invalid: "
                    f"{type(exc).__name__}: {exc}",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            selected_collectors = selection.collectors
            selected_collector_ids = tuple(
                item.collector_id for item in selected_collectors
            )
            if len(selected_collector_ids) != len(
                set(selected_collector_ids)
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collector selection contains duplicate collector IDs",
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            unauthorized = tuple(
                sorted(
                    set(selected_collector_ids)
                    - set(collection_plan.runnable_collector_ids)
                )
            )
            if unauthorized:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "collector selection is not authorized by Collection Plan: "
                    + ", ".join(unauthorized),
                    {"collection_plan": collection_plan},
                    blocking=True,
                )
            active_collectors = tuple(
                item.collector for item in selected_collectors
            )
            if not active_collectors:
                return StageExecutionResult(
                    StageStatus.NOT_IMPLEMENTED,
                    "no runnable collector is available for the compiled "
                    "CompanyCollectionPlan",
                    {
                        "collection_plan": collection_plan,
                        "collection_missing_required_requirements": (
                            collection_plan.missing_required_requirements
                        ),
                        "collection_missing_required_metrics": (
                            collection_plan.missing_required_metrics
                        ),
                        "collection_no_source_required_metrics": (
                            collection_plan.no_source_required_metrics
                        ),
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
                "primary evidence collection failed: "
                f"{type(exc).__name__}: {exc}",
                (
                    {"collection_plan": collection_plan}
                    if collection_plan is not None
                    else {}
                ),
                blocking=True,
            )

        segment_missing: tuple[str, ...] = ()
        if collection_plan is not None:
            for selected, batch in zip(
                selected_collectors,
                result.batches,
                strict=True,
            ):
                task = collection_plan.task_for_collector(
                    selected.collector_id
                )
                if batch.source_id != task.source_id:
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        f"collector {selected.collector_id} emitted source "
                        f"{batch.source_id}, but Collection Plan requires "
                        f"{task.source_id}",
                        {
                            "collection_plan": collection_plan,
                            "collection_selected_collector_ids": (
                                selected_collector_ids
                            ),
                        },
                        blocking=True,
                    )
                authorized = set(
                    collection_plan.authorized_segment_metrics_for_collector(
                        selected.collector_id
                    )
                )
                emitted = {
                    (record.segment, record.metric)
                    for record in batch.records
                }
                unauthorized_pairs = tuple(sorted(emitted - authorized))
                if unauthorized_pairs:
                    rendered = ", ".join(
                        f"{segment}/{metric}"
                        for segment, metric in unauthorized_pairs
                    )
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        f"collector {selected.collector_id} emitted "
                        "segment/metrics outside Collection Plan: "
                        f"{rendered}",
                        {
                            "collection_plan": collection_plan,
                            "collection_selected_collector_ids": (
                                selected_collector_ids
                            ),
                        },
                        blocking=True,
                    )
            active_pairs = {
                (record.segment, record.metric)
                for record in result.ledger.active()
            }
            segment_missing = tuple(
                item.requirement_id
                for item in collection_plan.required_evidence
                if (item.segment_id, item.metric) not in active_pairs
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
            outputs["collection_selected_collector_ids"] = (
                selected_collector_ids
            )
            outputs["collection_missing_required_requirements"] = (
                segment_missing
            )
            outputs["collection_missing_required_metrics"] = (
                collection_plan.missing_required_metrics
            )
            outputs["collection_no_source_required_metrics"] = (
                collection_plan.no_source_required_metrics
            )

        if result.missing_metrics or segment_missing:
            parts = []
            if result.missing_metrics:
                parts.append(
                    "metrics=" + ", ".join(result.missing_metrics)
                )
            if segment_missing:
                parts.append(
                    "segment requirements=" + ", ".join(segment_missing)
                )
            return StageExecutionResult(
                (
                    StageStatus.RECOVERY_REQUIRED
                    if strict_required_coverage
                    else StageStatus.WARNING
                ),
                "required primary evidence missing: " + "; ".join(parts),
                outputs,
                blocking=strict_required_coverage,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "primary evidence collected with complete required "
            "segment/metric coverage and planned source lineage",
            outputs,
        )

    return run


def evidence_ledger_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        ledger = context.data.get("evidence_ledger")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger missing",
                blocking=True,
            )
        active = ledger.active()
        if not active:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger has no active evidence",
                blocking=True,
            )
        payload = ledger.to_list()
        ledger_hash = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
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
