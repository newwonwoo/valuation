from types import SimpleNamespace

import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.decision_impact import ResearchEffort
from valuation_engine.funding import ClaimStage, FundingLadder, FundingLayer, FundingLink
from valuation_engine.funding_adapter import (
    FundedDemandState,
    FundingScanResult,
    live_upstream_funding_adapter,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.llm_adapters import researcher_a_adapter
from valuation_engine.llm_staff import IntelligenceProposal
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer, HypothesisRecord
from valuation_engine.scanner_runtime import (
    ScannerFinding,
    ScannerFindingStatus,
    live_rocket_insight_dispatch_adapter,
)


def evidence(evidence_id="E1", *, layer=EvidenceSourceLayer.REALIZED_OR_FILING):
    return EvidenceRecord(
        id=evidence_id,
        target="T1",
        metric="backlog",
        value=100,
        unit="KRW",
        source_layer=layer,
        effective_date="2026-06-30",
        observed_date="2026-08-01",
        source_name="filing",
        source_ref="filing://1",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def context(*, plan=None):
    return OrchestratorContext(
        run_id="R1",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        data={
            "company": "Example",
            "ticker": "000001",
            "target_id": "T1",
            "evidence_ledger": EvidenceLedger((evidence(), evidence("E2"))),
            "module_requirement_plan": plan or SimpleNamespace(segments=()),
            "mandatory_scanners": ("BACKLOG_QUALITY",),
            "active_research_units": ("BACKLOG_QUALITY",),
            "prior_hypotheses": (),
        },
        stage_traces=[],
        freeze_token=None,
    )


def test_live_scanner_dispatch_produces_findings_trace_and_effort():
    def runner(scan_context):
        assert scan_context.scanner_id == "BACKLOG_QUALITY"
        return ScannerFinding(
            scanner_id="BACKLOG_QUALITY",
            status=ScannerFindingStatus.PASS,
            summary="backlog is evidenced and needs conversion testing",
            evidence_ids=("E1",),
            hypothesis_candidates=("backlog_conversion",),
            economic_path_ids=("backlog_to_revenue",),
            effort=ResearchEffort(source_queries=2, documents_reviewed=1),
        )

    result = live_rocket_insight_dispatch_adapter(runners={"BACKLOG_QUALITY": runner})(context())
    assert result.status is StageStatus.PASS
    assert result.outputs["rocket_insight_execution_mode"] == "LIVE_DISPATCH"
    assert result.outputs["scanner_impact_traces"][0].module_id == "BACKLOG_QUALITY"
    assert result.outputs["scanner_research_effort"]["BACKLOG_QUALITY"].source_queries == 2


def test_missing_mandatory_scanner_runner_fails_closed():
    result = live_rocket_insight_dispatch_adapter(runners={})(context())
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking
    assert result.outputs["missing_scanner_runners"] == ("BACKLOG_QUALITY",)


def test_active_scanner_must_declare_downstream_path_or_context_only():
    def empty(_):
        return ScannerFinding(
            scanner_id="BACKLOG_QUALITY",
            status=ScannerFindingStatus.PASS,
            summary="looked around but produced no path",
            evidence_ids=("E1",),
        )

    result = live_rocket_insight_dispatch_adapter(runners={"BACKLOG_QUALITY": empty})(context())
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert "must connect" in result.rationale


def test_context_only_scanner_is_explicit_not_silently_useful():
    def context_only(_):
        return ScannerFinding(
            scanner_id="BACKLOG_QUALITY",
            status=ScannerFindingStatus.WARNING,
            summary="reviewed; no valuation-linked signal observed",
            evidence_ids=("E1",),
            context_only=True,
            effort=ResearchEffort(documents_reviewed=3),
        )

    result = live_rocket_insight_dispatch_adapter(runners={"BACKLOG_QUALITY": context_only})(context())
    assert result.status is StageStatus.WARNING
    trace = result.outputs["scanner_impact_traces"][0]
    assert trace.affected_decisions == ("research_context_only",)


def test_llm_staff_receives_scanner_findings():
    finding = ScannerFinding(
        scanner_id="BACKLOG_QUALITY",
        status=ScannerFindingStatus.PASS,
        summary="candidate",
        evidence_ids=("E1",),
        hypothesis_candidates=("H-CANDIDATE",),
    )
    ctx = context()
    ctx.data["scanner_findings"] = (finding,)

    def officer(staff):
        assert staff.scanner_findings == (finding,)
        return IntelligenceProposal(
            hypotheses=(
                HypothesisRecord(
                    id="H1",
                    statement="backlog conversion supports quantity",
                    causal_chain=("backlog", "quantity", "value"),
                    supporting_evidence_ids=("E1",),
                    kill_conditions=("conversion fails",),
                ),
            ),
            rationale="scanner finding informed the hypothesis",
        )

    result = researcher_a_adapter(officer=officer)(ctx)
    assert result.status is StageStatus.PASS
    assert result.outputs["hypotheses"][0].id == "H1"


def funding_plan():
    return SimpleNamespace(segments=(SimpleNamespace(funding_scans=("customer_advances_and_buyer_finance",)),))


def test_live_funding_scan_outputs_evidence_candidate_not_wacc_mutation():
    def scanner(scan_context):
        assert scan_context.required_scan_ids == ("customer_advances_and_buyer_finance",)
        ladder = FundingLadder(
            (
                FundingLink(
                    FundingLayer.PRODUCT_OR_PROJECT,
                    FundingLayer.BUYER_CASH_FLOW,
                    "binding order is supported by buyer cash flow",
                    ClaimStage.CONFIRMED_FACT,
                    0.9,
                    ("E1",),
                ),
                FundingLink(
                    FundingLayer.BUYER_CASH_FLOW,
                    FundingLayer.FINANCING_CHANNEL,
                    "customer advance reduces external financing requirement",
                    ClaimStage.FIRST_ORDER_MECHANISM,
                    0.8,
                    ("E2",),
                ),
            )
        )
        return FundingScanResult(
            state=FundedDemandState.FUNDED,
            summary="funding path is evidenced through buyer cash flow and financing channel",
            ladder=ladder,
            evidence_ids=("E1", "E2"),
            economic_path_ids=("buyer_funding_to_demand",),
            credit_improvement_evidence_ids=("E2",),
            effort=ResearchEffort(source_queries=1, documents_reviewed=2),
        )

    ctx = context(plan=funding_plan())
    result = live_upstream_funding_adapter(scanner=scanner)(ctx)
    assert result.status is StageStatus.PASS
    assert result.outputs["funded_demand_state"] == "FUNDED"
    assert result.outputs["funding_credit_improvement_evidence_ids"] == ("E2",)
    assert "wacc" not in result.outputs
    assert result.outputs["funding_impact_trace"].module_id == "UPSTREAM_FUNDING_SCAN"


def test_funding_scan_is_not_applicable_when_route_does_not_require_it():
    result = live_upstream_funding_adapter(scanner=lambda _: pytest.fail("scanner should not run"))(context())
    assert result.status is StageStatus.SKIPPED_NOT_APPLICABLE


def test_credit_improvement_candidate_cannot_rest_on_investment_hypothesis_only():
    def scanner(_):
        return FundingScanResult(
            state=FundedDemandState.CONDITIONAL,
            summary="weak credit theory",
            ladder=FundingLadder(
                (
                    FundingLink(
                        FundingLayer.PRODUCT_OR_PROJECT,
                        FundingLayer.BUYER_CASH_FLOW,
                        "hypothetical buyer benefit",
                        ClaimStage.INVESTMENT_HYPOTHESIS,
                        0.5,
                        (),
                    ),
                )
            ),
            evidence_ids=("E1",),
            credit_improvement_evidence_ids=("E1",),
        )

    result = live_upstream_funding_adapter(scanner=scanner)(context(plan=funding_plan()))
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert "lacks confirmed/first-order" in result.rationale
