from __future__ import annotations

from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .llm_staff import (
    BridgeAnalyst,
    IntelligenceOfficer,
    LLMStaffContext,
    RedTeamOfficer,
    materialize_bridge_bundle,
    merge_hypothesis_context,
    run_bridge_analyst,
    run_intelligence_officer,
    run_red_team,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceSourceLayer, HypothesisRecord


def _staff_context(context: OrchestratorContext) -> LLMStaffContext:
    company = context.data.get("company")
    ticker = context.data.get("ticker")
    ledger = context.data.get("evidence_ledger")
    if not isinstance(company, str) or not company:
        raise ValueError("company missing for LLM Staff")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("ticker missing for LLM Staff")
    if not isinstance(ledger, EvidenceLedger):
        raise ValueError("EvidenceLedger missing for LLM Staff")
    if any(
        item.source_layer is EvidenceSourceLayer.MARKET_COMPARISON
        for item in ledger.active()
    ):
        raise PermissionError(
            "pre-freeze LLM Staff context contains market-comparison Evidence"
        )
    prior = context.data.get("prior_hypotheses", ())
    if not isinstance(prior, tuple) or not all(
        isinstance(item, HypothesisRecord) for item in prior
    ):
        raise ValueError("prior_hypotheses must be a typed tuple")
    scanner_findings = context.data.get("scanner_findings", ())
    if not isinstance(scanner_findings, tuple):
        raise ValueError("scanner_findings must be a tuple")
    capacity = context.data.get("capacity_commitment_assessment")
    if capacity is not None and not isinstance(
        capacity, CapacityCommitmentAssessment
    ):
        raise ValueError(
            "capacity_commitment_assessment must be typed when present"
        )
    return LLMStaffContext(
        company=company,
        ticker=ticker,
        ledger=ledger,
        prior_hypotheses=prior,
        module_requirement_plan=context.data.get("module_requirement_plan"),
        scanner_findings=scanner_findings,
        funding_scan_result=context.data.get("funding_scan_result"),
        capacity_commitment_assessment=capacity,
    )


def researcher_a_adapter(*, officer: IntelligenceOfficer) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            staff = _staff_context(context)
            proposal = run_intelligence_officer(staff, officer)
            active_hypotheses = merge_hypothesis_context(
                staff.prior_hypotheses,
                proposal.hypotheses,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"LLM Intelligence Officer contract failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "LLM Intelligence Officer produced typed hypotheses/evidence requests without committing assumptions",
            {
                "intelligence_proposal": proposal,
                "hypotheses": active_hypotheses,
                "llm_requested_evidence": proposal.requested_evidence,
                "scanner_reinforcements": proposal.scanner_reinforcements,
            },
        )

    return run


def blind_red_team_adapter(*, officer: RedTeamOfficer) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            staff = _staff_context(context)
            hypotheses = context.data.get("hypotheses", ())
            if not isinstance(hypotheses, tuple) or not all(
                isinstance(item, HypothesisRecord) for item in hypotheses
            ):
                raise ValueError(
                    "Researcher hypotheses missing before Blind Red Team"
                )
            proposal = run_red_team(staff, hypotheses, officer)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Blind Red Team contract failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        unresolved = tuple(
            item
            for item in proposal.issues
            if item.blocking and not item.resolved
        )
        outputs = {
            "red_team_proposal": proposal,
            "red_team_counter_thesis": proposal.counter_thesis,
            "red_team_requested_evidence": proposal.requested_evidence,
        }
        if unresolved:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Blind Red Team found unresolved blocking issues: "
                + ", ".join(item.id for item in unresolved),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Blind Red Team completed with no unresolved blocker",
            outputs,
        )

    return run


def evidence_to_assumption_bridge_adapter(
    *,
    analyst: BridgeAnalyst,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            staff = _staff_context(context)
            hypotheses = context.data.get("hypotheses", ())
            if not isinstance(hypotheses, tuple) or not all(
                isinstance(item, HypothesisRecord) for item in hypotheses
            ):
                raise ValueError(
                    "Researcher hypotheses missing before Bridge analysis"
                )
            red_team = context.data.get("red_team_proposal")
            if red_team is None:
                raise ValueError("Red Team proposal missing before Bridge analysis")
            bundle = run_bridge_analyst(
                staff,
                hypotheses,
                red_team,
                analyst,
            )
            bridges, specs, input_map = materialize_bridge_bundle(bundle)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"LLM Bridge Analyst contract failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        if not bridges:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Bridge Analyst produced no assumption candidates",
                {"bridge_proposal_bundle": bundle},
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "LLM Bridge proposals validated and converted to compiler requests; no assumptions committed",
            {
                "bridge_proposal_bundle": bundle,
                "bridges": bridges,
                "assumption_specs": specs,
                "bridge_input_map": input_map,
            },
        )

    return run
