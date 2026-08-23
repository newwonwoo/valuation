from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from .control_plane import StageStatus
from .decision_impact import ModuleImpactTrace, ResearchEffort
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceSourceLayer


class ScannerFindingStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ScannerContext:
    scanner_id: str
    company: str
    ticker: str
    target_id: str
    ledger: EvidenceLedger
    module_requirement_plan: object


@dataclass(frozen=True)
class ScannerFinding:
    scanner_id: str
    status: ScannerFindingStatus
    summary: str
    evidence_ids: tuple[str, ...] = ()
    mechanism_ids: tuple[str, ...] = ()
    hypothesis_candidates: tuple[str, ...] = ()
    verification_requests: tuple[str, ...] = ()
    economic_path_ids: tuple[str, ...] = ()
    final_output_refs: tuple[str, ...] = ()
    context_only: bool = False
    effort: ResearchEffort = ResearchEffort()

    def validate(self, ledger: EvidenceLedger) -> None:
        if not self.scanner_id or not self.summary:
            raise ValueError("scanner finding requires scanner_id and summary")
        for evidence_id in self.evidence_ids:
            ledger.get(evidence_id)
        if self.status is ScannerFindingStatus.NOT_APPLICABLE and any(
            (
                self.evidence_ids,
                self.hypothesis_candidates,
                self.verification_requests,
                self.economic_path_ids,
                self.final_output_refs,
            )
        ):
            raise ValueError("NOT_APPLICABLE scanner cannot report active impact outputs")
        connected = any(
            (
                self.hypothesis_candidates,
                self.verification_requests,
                self.economic_path_ids,
                self.final_output_refs,
            )
        )
        if self.status is not ScannerFindingStatus.NOT_APPLICABLE and not connected and not self.context_only:
            raise ValueError(
                "active scanner must connect to a hypothesis, verification request, economic path, final output, or explicitly declare context_only"
            )

    def impact_trace(self) -> ModuleImpactTrace:
        decisions: list[str] = []
        if self.hypothesis_candidates:
            decisions.append("hypothesis_candidate")
        if self.verification_requests:
            decisions.append("verification_request")
        if self.context_only:
            decisions.append("research_context_only")
        return ModuleImpactTrace(
            module_id=self.scanner_id,
            evidence_ids=self.evidence_ids,
            mechanism_ids=self.mechanism_ids,
            affected_decisions=tuple(decisions),
            economic_path_ids=self.economic_path_ids,
            final_output_refs=self.final_output_refs,
        )


ScannerRunner = Callable[[ScannerContext], ScannerFinding]


def live_rocket_insight_dispatch_adapter(
    *,
    runners: Mapping[str, ScannerRunner],
) -> StageAdapter:
    """Dispatch the canonical scanner loadout against the current pre-freeze EvidenceLedger.

    Mandatory scanner IDs come from Module Requirement Plan. Additional scanner runners are
    executed only when the adaptive loadout activates them. A runner may interpret Evidence and
    propose hypotheses/verification paths, but it cannot create Compiled Assumptions or access
    target-market Evidence through this context.
    """

    def run(context: OrchestratorContext) -> StageExecutionResult:
        company = context.data.get("company")
        ticker = context.data.get("ticker")
        target_id = context.data.get("target_id")
        ledger = context.data.get("evidence_ledger")
        plan = context.data.get("module_requirement_plan")
        mandatory = context.data.get("mandatory_scanners", ())
        active = context.data.get("active_research_units", mandatory)
        if not all(isinstance(value, str) and value for value in (company, ticker, target_id)):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "company/ticker/target_id missing before scanner dispatch", blocking=True)
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger missing before scanner dispatch", blocking=True)
        if any(item.source_layer is EvidenceSourceLayer.MARKET_COMPARISON for item in ledger.active()):
            return StageExecutionResult(StageStatus.BLOCKED, "Rocket Insight scanner context contains target-market Evidence", blocking=True)
        if not isinstance(mandatory, tuple) or not all(isinstance(item, str) and item for item in mandatory):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "typed mandatory scanner loadout missing", blocking=True)
        if not isinstance(active, tuple) or not all(isinstance(item, str) and item for item in active):
            return StageExecutionResult(StageStatus.BLOCKED, "active_research_units must be a string tuple", blocking=True)

        mandatory_set = set(mandatory)
        planned = list(mandatory)
        for scanner_id in active:
            if scanner_id in runners and scanner_id not in planned:
                planned.append(scanner_id)

        missing_mandatory = tuple(scanner_id for scanner_id in mandatory if scanner_id not in runners)
        if missing_mandatory:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "mandatory live scanner runner(s) missing: " + ", ".join(missing_mandatory),
                {"missing_scanner_runners": missing_mandatory},
                blocking=True,
            )

        findings: list[ScannerFinding] = []
        traces: list[ModuleImpactTrace] = []
        effort: dict[str, ResearchEffort] = {}
        warnings: list[str] = []
        for scanner_id in planned:
            runner = runners.get(scanner_id)
            if runner is None:
                # Non-mandatory adaptive units without a scanner runner remain explicit but do not
                # block the valuation; they are not silently treated as researched.
                warnings.append(f"optional scanner runner unavailable: {scanner_id}")
                continue
            scanner_context = ScannerContext(
                scanner_id=scanner_id,
                company=company,
                ticker=ticker,
                target_id=target_id,
                ledger=ledger,
                module_requirement_plan=plan,
            )
            try:
                finding = runner(scanner_context)
                if finding.scanner_id != scanner_id:
                    raise ValueError(
                        f"scanner runner identity mismatch: expected {scanner_id}, got {finding.scanner_id}"
                    )
                finding.validate(ledger)
                trace = finding.impact_trace()
                if finding.status is not ScannerFindingStatus.NOT_APPLICABLE:
                    trace.validate()
            except Exception as exc:
                if scanner_id in mandatory_set:
                    return StageExecutionResult(
                        StageStatus.RECOVERY_REQUIRED,
                        f"mandatory scanner {scanner_id} failed: {type(exc).__name__}: {exc}",
                        {"scanner_findings": tuple(findings)},
                        blocking=True,
                    )
                warnings.append(f"optional scanner {scanner_id} failed: {type(exc).__name__}: {exc}")
                continue
            findings.append(finding)
            effort[scanner_id] = finding.effort
            if finding.status is not ScannerFindingStatus.NOT_APPLICABLE:
                traces.append(trace)
            if finding.status is ScannerFindingStatus.WARNING:
                warnings.append(f"{scanner_id}: {finding.summary}")

        if not findings:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "scanner dispatch produced no findings",
                blocking=True,
            )
        status = StageStatus.WARNING if warnings else StageStatus.PASS
        return StageExecutionResult(
            status,
            "live Rocket Insight scanner dispatch completed" + (" with warnings" if warnings else ""),
            {
                "scanner_findings": tuple(findings),
                "scanner_impact_traces": tuple(traces),
                "scanner_research_effort": effort,
                "rocket_insight_execution_mode": "LIVE_DISPATCH",
                "rocket_insight_warnings": tuple(warnings),
            },
        )

    return run
