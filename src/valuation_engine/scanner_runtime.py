from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Callable

from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AffectedVariable


class ScannerFindingStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    MISSING_EVIDENCE = "missing_evidence"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


@dataclass(frozen=True)
class ScannerRequest:
    scanner_id: str
    target_id: str
    run_id: str
    segment_ids: tuple[str, ...]
    required_kpis: tuple[str, ...]
    required_evidence_metrics: tuple[str, ...]
    kill_conditions: tuple[str, ...]
    active_evidence_ids: tuple[str, ...]

    def validate(self) -> None:
        if not all((self.scanner_id, self.target_id, self.run_id)):
            raise ValueError("scanner request requires scanner_id, target_id and run_id")
        if not self.segment_ids:
            raise ValueError("scanner request requires at least one segment")
        if len(self.active_evidence_ids) != len(set(self.active_evidence_ids)):
            raise ValueError("scanner request active_evidence_ids must be unique")


@dataclass(frozen=True)
class ScannerFinding:
    scanner_id: str
    status: ScannerFindingStatus
    summary: str
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    missing_evidence_metrics: tuple[str, ...] = ()
    affected_variables: tuple[AffectedVariable, ...] = ()
    economic_path_ids: tuple[str, ...] = ()
    kill_condition_hits: tuple[str, ...] = ()
    reinforcement_scanner_ids: tuple[str, ...] = ()

    def validate(self, *, mandatory: bool, active_evidence_ids: set[str]) -> None:
        if not self.scanner_id or not self.summary:
            raise ValueError("scanner finding requires scanner_id and summary")
        referenced = set(self.supporting_evidence_ids) | set(self.contradicting_evidence_ids)
        unknown = tuple(sorted(referenced - active_evidence_ids))
        if unknown:
            raise ValueError(
                f"scanner {self.scanner_id} references inactive/unknown Evidence IDs: {', '.join(unknown)}"
            )
        if len(referenced) != len(self.supporting_evidence_ids) + len(self.contradicting_evidence_ids):
            raise ValueError(f"scanner {self.scanner_id} repeats one Evidence ID across support/contradiction")
        if mandatory and self.status is ScannerFindingStatus.NOT_APPLICABLE:
            raise ValueError(f"mandatory scanner {self.scanner_id} cannot be NOT_APPLICABLE")
        if self.status is ScannerFindingStatus.MISSING_EVIDENCE and not self.missing_evidence_metrics:
            raise ValueError(f"scanner {self.scanner_id} missing-evidence result requires metric requests")
        if self.status in {ScannerFindingStatus.PASS, ScannerFindingStatus.WARNING}:
            has_impact_path = bool(
                self.affected_variables
                or self.economic_path_ids
                or self.kill_condition_hits
                or self.reinforcement_scanner_ids
            )
            if not has_impact_path:
                raise ValueError(
                    f"scanner {self.scanner_id} has no affected variable, economic path, "
                    "kill-condition hit or reinforcement request"
                )
        for value in (
            *self.missing_evidence_metrics,
            *self.economic_path_ids,
            *self.kill_condition_hits,
            *self.reinforcement_scanner_ids,
        ):
            if not value:
                raise ValueError(f"scanner {self.scanner_id} contains a blank output identifier")


ScannerHandler = Callable[[ScannerRequest, EvidenceLedger], ScannerFinding]


@dataclass(frozen=True)
class ScannerHandlerSpec:
    scanner_id: str
    handler: ScannerHandler

    def validate(self) -> None:
        if not self.scanner_id or not callable(self.handler):
            raise ValueError("scanner handler spec requires scanner_id and callable handler")


@dataclass(frozen=True)
class ScannerExecutionResult:
    target_id: str
    run_id: str
    mandatory_scanner_ids: tuple[str, ...]
    reinforcement_scanner_ids: tuple[str, ...]
    findings: tuple[ScannerFinding, ...]
    missing_mandatory_handlers: tuple[str, ...]
    failed_scanner_ids: tuple[str, ...]
    snapshot_hash: str

    @property
    def missing_evidence_metrics(self) -> tuple[str, ...]:
        return _ordered_unique(
            metric for finding in self.findings for metric in finding.missing_evidence_metrics
        )

    @property
    def kill_condition_hits(self) -> tuple[str, ...]:
        return _ordered_unique(
            item for finding in self.findings for item in finding.kill_condition_hits
        )

    @property
    def requested_reinforcement_scanners(self) -> tuple[str, ...]:
        return _ordered_unique(
            item for finding in self.findings for item in finding.reinforcement_scanner_ids
        )

    @property
    def complete(self) -> bool:
        return not self.missing_mandatory_handlers and not self.failed_scanner_ids and not self.missing_evidence_metrics


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _finding_payload(finding: ScannerFinding) -> dict:
    payload = asdict(finding)
    payload["status"] = finding.status.value
    payload["affected_variables"] = [item.value for item in finding.affected_variables]
    return payload


def _execution_hash(
    *,
    target_id: str,
    run_id: str,
    mandatory: tuple[str, ...],
    reinforcement: tuple[str, ...],
    findings: tuple[ScannerFinding, ...],
    missing_handlers: tuple[str, ...],
    failed_scanners: tuple[str, ...],
) -> str:
    payload = {
        "target_id": target_id,
        "run_id": run_id,
        "mandatory_scanner_ids": sorted(mandatory),
        "reinforcement_scanner_ids": sorted(reinforcement),
        "findings": [
            _finding_payload(item) for item in sorted(findings, key=lambda value: value.scanner_id)
        ],
        "missing_mandatory_handlers": sorted(missing_handlers),
        "failed_scanner_ids": sorted(failed_scanners),
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def run_scanner_loadout(
    *,
    target_id: str,
    run_id: str,
    plan: ModuleRequirementPlan,
    ledger: EvidenceLedger,
    handler_specs: tuple[ScannerHandlerSpec, ...],
    reinforcement_scanner_ids: tuple[str, ...] = (),
) -> ScannerExecutionResult:
    """Execute the exact mandatory scanner loadout plus explicit LLM reinforcement scanners.

    Scanner handlers produce typed analytical findings only. They cannot emit compiled
    assumptions or valuation outputs. Missing mandatory implementations remain explicit.
    """
    plan.validate()
    if not target_id or not run_id:
        raise ValueError("scanner execution requires target_id and run_id")
    if len(reinforcement_scanner_ids) != len(set(reinforcement_scanner_ids)):
        raise ValueError("reinforcement scanner IDs must be unique")

    handler_map: dict[str, ScannerHandler] = {}
    for spec in handler_specs:
        spec.validate()
        if spec.scanner_id in handler_map:
            raise ValueError(f"duplicate scanner handler: {spec.scanner_id}")
        handler_map[spec.scanner_id] = spec.handler

    mandatory = tuple(dict.fromkeys(plan.mandatory_scanners))
    requested = tuple(dict.fromkeys((*mandatory, *reinforcement_scanner_ids)))
    active = ledger.active()
    active_ids = {item.id for item in active}
    segment_ids = tuple(segment.segment_id for segment in plan.segments)

    findings: list[ScannerFinding] = []
    missing_handlers: list[str] = []
    failed: list[str] = []

    for scanner_id in requested:
        handler = handler_map.get(scanner_id)
        if handler is None:
            if scanner_id in mandatory:
                missing_handlers.append(scanner_id)
            continue
        request = ScannerRequest(
            scanner_id=scanner_id,
            target_id=target_id,
            run_id=run_id,
            segment_ids=segment_ids,
            required_kpis=plan.required_kpis,
            required_evidence_metrics=plan.required_evidence,
            kill_conditions=plan.kill_conditions,
            active_evidence_ids=tuple(sorted(active_ids)),
        )
        request.validate()
        try:
            finding = handler(request, ledger)
            if finding.scanner_id != scanner_id:
                raise ValueError(
                    f"scanner handler {scanner_id} returned mismatched finding {finding.scanner_id}"
                )
            finding.validate(mandatory=scanner_id in mandatory, active_evidence_ids=active_ids)
        except Exception:
            failed.append(scanner_id)
            continue
        findings.append(finding)

    finding_ids = tuple(item.scanner_id for item in findings)
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("scanner execution produced duplicate findings")

    findings_tuple = tuple(findings)
    missing_tuple = tuple(sorted(missing_handlers))
    failed_tuple = tuple(sorted(failed))
    result_hash = _execution_hash(
        target_id=target_id,
        run_id=run_id,
        mandatory=mandatory,
        reinforcement=reinforcement_scanner_ids,
        findings=findings_tuple,
        missing_handlers=missing_tuple,
        failed_scanners=failed_tuple,
    )
    return ScannerExecutionResult(
        target_id=target_id,
        run_id=run_id,
        mandatory_scanner_ids=mandatory,
        reinforcement_scanner_ids=reinforcement_scanner_ids,
        findings=findings_tuple,
        missing_mandatory_handlers=missing_tuple,
        failed_scanner_ids=failed_tuple,
        snapshot_hash=result_hash,
    )


def rocket_insight_scan_adapter(
    *,
    handler_specs: tuple[ScannerHandlerSpec, ...],
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        plan = context.data.get("module_requirement_plan")
        ledger = context.data.get("evidence_ledger")
        reinforcement = context.data.get("scanner_reinforcement_ids", ())
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "target_id missing before Rocket Insight scanner dispatch",
                blocking=True,
            )
        if not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Module Requirement Plan missing before Rocket Insight scanner dispatch",
                blocking=True,
            )
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger missing before Rocket Insight scanner dispatch",
                blocking=True,
            )
        if not isinstance(reinforcement, tuple) or not all(
            isinstance(item, str) and item for item in reinforcement
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "scanner_reinforcement_ids must be a non-empty-string tuple",
                blocking=True,
            )
        try:
            result = run_scanner_loadout(
                target_id=target_id,
                run_id=context.run_id,
                plan=plan,
                ledger=ledger,
                handler_specs=handler_specs,
                reinforcement_scanner_ids=reinforcement,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Rocket Insight scanner dispatch failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {
            "scanner_execution_result": result,
            "scanner_findings": result.findings,
            "scanner_snapshot_hash": result.snapshot_hash,
            "scanner_missing_evidence_metrics": result.missing_evidence_metrics,
            "scanner_kill_condition_hits": result.kill_condition_hits,
            "scanner_reinforcement_requests": result.requested_reinforcement_scanners,
        }
        if result.missing_mandatory_handlers:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "mandatory scanner handlers not implemented: "
                + ", ".join(result.missing_mandatory_handlers),
                outputs,
                blocking=True,
            )
        if result.failed_scanner_ids:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "scanner handlers failed validation/execution: "
                + ", ".join(result.failed_scanner_ids),
                outputs,
                blocking=True,
            )
        if result.missing_evidence_metrics:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "scanner loadout requires additional evidence: "
                + ", ".join(result.missing_evidence_metrics),
                outputs,
                blocking=True,
            )
        if any(item.status is ScannerFindingStatus.WARNING for item in result.findings):
            return StageExecutionResult(
                StageStatus.WARNING,
                "mandatory Rocket Insight scanner loadout completed with warnings",
                outputs,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "mandatory Rocket Insight scanner loadout executed with typed traceable findings",
            outputs,
        )

    return run
