from valuation_engine.context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
    ContextStrengthReasoningPriority,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.llm_adapters import researcher_a_adapter
from valuation_engine.llm_staff import IntelligenceProposal
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import (
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def evidence() -> EvidenceRecord:
    return EvidenceRecord(
        id="E1",
        target="T",
        metric="installed_network",
        value=1,
        unit="count",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref="source#E1",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def hypothesis() -> HypothesisRecord:
    return HypothesisRecord(
        id="H1",
        statement=(
            "A changing operating environment may make the installed network "
            "more strategically valuable"
        ),
        causal_chain=(
            "external change",
            "emergent need",
            "installed network",
            "value capture",
        ),
        supporting_evidence_ids=("E1",),
        kill_conditions=("the network cannot serve the emergent need",),
    )


def linkage() -> ContextStrengthLinkage:
    return ContextStrengthLinkage(
        id="CSL-1",
        external_change=(
            "Mission-critical connectivity demand is expanding beyond "
            "consumer broadband"
        ),
        emergent_need=(
            "Customers need resilient coverage when terrestrial or high-bandwidth "
            "networks are unavailable"
        ),
        company_strength=(
            "The company already operates a globally deployed narrowband network"
        ),
        linkage_thesis=(
            "Competition in broadband can increase the strategic value of the "
            "existing resilient narrowband network rather than make it obsolete"
        ),
        market_blind_spot=(
            "The company is categorized as a legacy low-speed provider, so investors "
            "compare bandwidth instead of coverage resilience"
        ),
        value_capture_path=(
            "Higher mission-critical adoption can improve utilization, contract "
            "duration and pricing power"
        ),
        causal_chain=(
            "mission-critical connectivity demand expands",
            "resilient global coverage becomes scarce",
            "the installed narrowband network already supplies that coverage",
            "customers adopt longer and higher-value contracts",
            "utilization, cash flow and strategic value become observable",
        ),
        supporting_evidence_ids=("E1",),
        hypothesis_ids=("H1",),
        recognition_triggers=(
            "a disclosed mission-critical contract or strategic partnership",
        ),
        kill_conditions=(
            "the installed network cannot meet the required reliability or economics",
        ),
        next_checks=(
            "verify contract mix, capacity rights and incremental service margins",
        ),
        confidence=0.7,
    )


def run_researcher(officer):
    return run_controlled_workflow(
        run_id="CONTEXT-STRENGTH",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("RESEARCHER_A",),
        adapters={"RESEARCHER_A": researcher_a_adapter(officer=officer)},
        required_stages=("RESEARCHER_A",),
        initial_data={
            "company": "Example",
            "ticker": "EXM",
            "evidence_ledger": EvidenceLedger((evidence(),)),
            "prior_hypotheses": (),
            "module_requirement_plan": object(),
        },
    )


def not_applicable_proposal() -> IntelligenceProposal:
    return IntelligenceProposal(
        hypotheses=(hypothesis(),),
        rationale="synthetic deterministic valuation fixture",
        context_strength_linkage_decision=ContextStrengthLinkageDecision(
            not_applicable_reason=(
                "This synthetic run validates deterministic valuation plumbing and "
                "contains no external-change investment claim to test."
            ),
        ),
    )


def test_canonical_researcher_receives_primary_reasoning_doctrine():
    def officer(context):
        doctrine = context.context_strength_linkage_doctrine
        assert doctrine.priority is ContextStrengthReasoningPriority.PRIMARY_GATE
        assert doctrine.reasoning_sequence[0].startswith("Start outside the company")
        assert doctrine.reasoning_sequence[-1].startswith("Only after the linkage")
        assert any(
            "keyword overlap" in shortcut
            for shortcut in doctrine.prohibited_shortcuts
        )
        assert "cannot commit assumptions" in doctrine.valuation_boundary
        return not_applicable_proposal()

    result = run_researcher(officer)

    assert result.blocked_reasons == ()


def test_canonical_researcher_blocks_hypotheses_without_linkage_decision():
    result = run_researcher(
        lambda _: IntelligenceProposal(
            hypotheses=(hypothesis(),),
            rationale="a valuation hypothesis without the required insight layer",
        )
    )

    assert result.blocked_reasons
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "linkage decision" in result.stage_traces[-1].rationale


def test_explicit_not_applicable_decision_is_auditable_and_passes():
    result = run_researcher(lambda _: not_applicable_proposal())

    assert result.blocked_reasons == ()
    assert result.data["context_strength_linkage_status"] == "NOT_APPLICABLE"
    assert result.data[
        "context_strength_linkage_not_applicable_reason"
    ] == (
        "This synthetic run validates deterministic valuation plumbing and contains "
        "no external-change investment claim to test."
    )


def test_non_obvious_linkage_is_bound_to_evidence_and_hypothesis():
    result = run_researcher(
        lambda _: IntelligenceProposal(
            hypotheses=(hypothesis(),),
            rationale=(
                "environmental change is linked to an existing company strength "
                "before numerical valuation"
            ),
            context_strength_linkage_decision=ContextStrengthLinkageDecision(
                linkages=(linkage(),),
            ),
        )
    )

    assert result.blocked_reasons == ()
    assert result.data["context_strength_linkage_status"] == "APPLICABLE"
    assert result.data["context_strength_linkages"] == (linkage(),)


def test_shallow_linkage_without_full_causal_path_is_blocked():
    bad = ContextStrengthLinkage(
        **{
            **linkage().__dict__,
            "causal_chain": (
                "AI demand rises",
                "the company benefits",
            ),
        }
    )
    result = run_researcher(
        lambda _: IntelligenceProposal(
            hypotheses=(hypothesis(),),
            rationale="shallow theme matching attempt",
            context_strength_linkage_decision=ContextStrengthLinkageDecision(
                linkages=(bad,),
            ),
        )
    )

    assert result.blocked_reasons
    assert "causal_chain" in result.stage_traces[-1].rationale


def test_linkage_cannot_reference_unknown_hypothesis():
    bad = ContextStrengthLinkage(
        **{
            **linkage().__dict__,
            "hypothesis_ids": ("H-UNKNOWN",),
        }
    )
    result = run_researcher(
        lambda _: IntelligenceProposal(
            hypotheses=(hypothesis(),),
            rationale="unknown hypothesis binding attempt",
            context_strength_linkage_decision=ContextStrengthLinkageDecision(
                linkages=(bad,),
            ),
        )
    )

    assert result.blocked_reasons
    assert "unknown hypotheses" in result.stage_traces[-1].rationale
