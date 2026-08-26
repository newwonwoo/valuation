from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .control_plane import StageStatus
from .decision_impact import ModuleImpactTrace, ResearchEffort
from .funding import ClaimStage, FundingLadder
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceSourceLayer


class FundedDemandState(str, Enum):
    FUNDED = "FUNDED"
    CONDITIONAL = "CONDITIONAL"
    UNFUNDED = "UNFUNDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FundingScanContext:
    company: str
    ticker: str
    target_id: str
    required_scan_ids: tuple[str, ...]
    ledger: EvidenceLedger
    module_requirement_plan: object


@dataclass(frozen=True)
class FundingScanResult:
    state: FundedDemandState
    summary: str
    ladder: FundingLadder
    evidence_ids: tuple[str, ...]
    financing_constraints: tuple[str, ...] = ()
    verification_requests: tuple[str, ...] = ()
    economic_path_ids: tuple[str, ...] = ()
    credit_improvement_evidence_ids: tuple[str, ...] = ()
    effort: ResearchEffort = ResearchEffort()

    def validate(self, ledger: EvidenceLedger) -> None:
        if not self.summary:
            raise ValueError("funding scan requires summary")
        self.ladder.validate()
        if not self.evidence_ids:
            raise ValueError("funding scan requires Evidence IDs")
        for evidence_id in self.evidence_ids:
            ledger.get(evidence_id)
        known = set(self.evidence_ids)
        for link in self.ladder.links:
            for evidence_id in link.evidence_ids:
                ledger.get(evidence_id)
                if evidence_id not in known:
                    raise ValueError("funding ladder link references Evidence outside FundingScanResult")
        for evidence_id in self.credit_improvement_evidence_ids:
            ledger.get(evidence_id)
            if evidence_id not in known:
                raise ValueError("credit-improvement evidence must be included in funding evidence_ids")
        # A credit/WACC candidate needs more than an investment hypothesis. This does not lower
        # WACC; it merely makes the evidence visible to the independent WACC stage.
        if self.credit_improvement_evidence_ids and not any(
            link.claim_stage in {ClaimStage.CONFIRMED_FACT, ClaimStage.FIRST_ORDER_MECHANISM}
            and set(link.evidence_ids).intersection(self.credit_improvement_evidence_ids)
            for link in self.ladder.links
        ):
            raise ValueError("credit-improvement candidate lacks confirmed/first-order funding evidence")

    def impact_trace(self) -> ModuleImpactTrace:
        decisions = ["funded_demand_assessment"]
        if self.verification_requests:
            decisions.append("funding_verification_request")
        if self.credit_improvement_evidence_ids:
            decisions.append("credit_improvement_candidate")
        return ModuleImpactTrace(
            module_id="UPSTREAM_FUNDING_SCAN",
            evidence_ids=self.evidence_ids,
            mechanism_ids=tuple(
                f"funding:{link.lower_layer.name.lower()}->{link.upper_layer.name.lower()}"
                for link in self.ladder.links
            ),
            affected_decisions=tuple(decisions),
            economic_path_ids=self.economic_path_ids,
        )


FundingScanner = Callable[[FundingScanContext], FundingScanResult]


def live_upstream_funding_adapter(*, scanner: FundingScanner) -> StageAdapter:
    """Execute a route-required funding scan without letting it directly mutate WACC/value."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        company = context.data.get("company")
        ticker = context.data.get("ticker")
        target_id = context.data.get("target_id")
        ledger = context.data.get("evidence_ledger")
        plan = context.data.get("module_requirement_plan")
        if not all(isinstance(value, str) and value for value in (company, ticker, target_id)):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "company/ticker/target_id missing before funding scan", blocking=True)
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger missing before funding scan", blocking=True)
        if any(item.source_layer is EvidenceSourceLayer.MARKET_COMPARISON for item in ledger.active()):
            return StageExecutionResult(StageStatus.BLOCKED, "funding scan contains target-equity market Evidence", blocking=True)

        segments = getattr(plan, "segments", ())
        required_scan_ids = tuple(
            dict.fromkeys(
                scan
                for segment in segments
                for scan in getattr(segment, "funding_scans", ())
                if scan
            )
        )
        if not required_scan_ids:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected Industry DNA does not require a dedicated upstream funding scan",
                {"upstream_funding_scan_state": "NOT_APPLICABLE"},
            )

        scan_context = FundingScanContext(
            company=company,
            ticker=ticker,
            target_id=target_id,
            required_scan_ids=required_scan_ids,
            ledger=ledger,
            module_requirement_plan=plan,
        )
        try:
            result = scanner(scan_context)
            result.validate(ledger)
            trace = result.impact_trace()
            trace.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live upstream funding scan failed: {type(exc).__name__}: {exc}",
                {"required_funding_scans": required_scan_ids},
                blocking=True,
            )

        status = StageStatus.WARNING if result.state in {FundedDemandState.CONDITIONAL, FundedDemandState.UNKNOWN} else StageStatus.PASS
        if result.state is FundedDemandState.UNFUNDED:
            status = StageStatus.WARNING
        return StageExecutionResult(
            status,
            "live upstream funding scan completed; result is evidence/hypothesis input only and does not directly change WACC",
            {
                "funding_scan_result": result,
                "funding_ladder": result.ladder,
                "funded_demand_state": result.state.value,
                "funding_verification_requests": result.verification_requests,
                "funding_credit_improvement_evidence_ids": result.credit_improvement_evidence_ids,
                "funding_impact_trace": trace,
                "funding_research_effort": {"UPSTREAM_FUNDING_SCAN": result.effort},
            },
        )

    return run
