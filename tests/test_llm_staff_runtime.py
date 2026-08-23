from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.llm_adapters import (
    blind_red_team_adapter,
    evidence_to_assumption_bridge_adapter,
    researcher_a_adapter,
)
from valuation_engine.llm_staff import (
    BridgeDraft,
    BridgeProposalBundle,
    IntelligenceProposal,
    RedTeamProposal,
)
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def evidence(*, layer=EvidenceSourceLayer.REALIZED_OR_FILING):
    return EvidenceRecord(
        id="E1",
        target="T",
        metric="margin",
        value=0.2,
        unit="ratio",
        source_layer=layer,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref="source#E1",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def researcher(context):
    assert context.prior_hypotheses == ()
    return IntelligenceProposal(
        hypotheses=(
            HypothesisRecord(
                id="H1",
                statement="margin remains structurally supported",
                causal_chain=("filing evidence", "margin", "intrinsic value"),
                supporting_evidence_ids=("E1",),
                kill_conditions=("margin reverses",),
            ),
        ),
        requested_evidence=("next quarterly filing",),
        scanner_reinforcements=("CAPACITY_RAMP",),
        rationale="primary evidence supports a margin hypothesis",
    )


def red_team(context, hypotheses):
    assert hypotheses[0].id == "H1"
    return RedTeamProposal(issues=(), counter_thesis="margin may normalize faster than expected")


def bridge_analyst(context, hypotheses, red_team_output):
    return BridgeProposalBundle(
        drafts=(
            BridgeDraft(
                assumption_key="margin",
                scenario_id="Base",
                bridge=BridgeRecord(
                    id="B1",
                    evidence_ids=("E1",),
                    hypothesis_id="H1",
                    affected_variable=AffectedVariable.MARGIN,
                    direction=Direction.UNCHANGED,
                    old_value=0.2,
                    new_value=0.2,
                    unit="ratio",
                    rationale="identity observation proposal",
                    confidence=0.8,
                    kill_condition="margin reverses",
                    verification_event="next filing",
                    economic_path_id="PATH:MARGIN",
                ),
                canonical_unit="ratio",
                transform_id="identity_observation",
                min_value="0",
                max_value="1",
            ),
        ),
        rationale="translate validated hypothesis into a compiler request",
    )


def test_llm_staff_runs_as_typed_proposal_pipeline_without_committing():
    result = run_controlled_workflow(
        run_id="LLM-STAFF",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("RESEARCHER_A", "BLIND_RED_TEAM_B", "EVIDENCE_TO_ASSUMPTION_BRIDGE"),
        adapters={
            "RESEARCHER_A": researcher_a_adapter(officer=researcher),
            "BLIND_RED_TEAM_B": blind_red_team_adapter(officer=red_team),
            "EVIDENCE_TO_ASSUMPTION_BRIDGE": evidence_to_assumption_bridge_adapter(analyst=bridge_analyst),
        },
        required_stages=("RESEARCHER_A", "BLIND_RED_TEAM_B", "EVIDENCE_TO_ASSUMPTION_BRIDGE"),
        initial_data={
            "company": "Example",
            "ticker": "EXM",
            "evidence_ledger": EvidenceLedger((evidence(),)),
            "prior_hypotheses": (),
        },
    )
    assert result.blocked_reasons == ()
    assert all(trace.status is StageStatus.PASS for trace in result.stage_traces)
    assert result.data["hypotheses"][0].id == "H1"
    assert result.data["bridges"][0].id == "B1"
    assert result.data["assumption_specs"][0].bridge_id == "B1"
    assert result.data["bridge_input_map"] == {"B1": ("E1",)}
    assert "compiled_assumption_set" not in result.data


def test_unregistered_transform_from_llm_is_blocked_before_compiler():
    def bad_bridge(context, hypotheses, red_team_output):
        good = bridge_analyst(context, hypotheses, red_team_output).drafts[0]
        return BridgeProposalBundle(
            drafts=(
                BridgeDraft(
                    assumption_key=good.assumption_key,
                    scenario_id=good.scenario_id,
                    bridge=good.bridge,
                    canonical_unit=good.canonical_unit,
                    transform_id="llm_python_expression",
                ),
            ),
            rationale="bad transform attempt",
        )

    result = run_controlled_workflow(
        run_id="LLM-BLOCK",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("RESEARCHER_A", "BLIND_RED_TEAM_B", "EVIDENCE_TO_ASSUMPTION_BRIDGE"),
        adapters={
            "RESEARCHER_A": researcher_a_adapter(officer=researcher),
            "BLIND_RED_TEAM_B": blind_red_team_adapter(officer=red_team),
            "EVIDENCE_TO_ASSUMPTION_BRIDGE": evidence_to_assumption_bridge_adapter(analyst=bad_bridge),
        },
        required_stages=("RESEARCHER_A", "BLIND_RED_TEAM_B", "EVIDENCE_TO_ASSUMPTION_BRIDGE"),
        initial_data={
            "company": "Example",
            "ticker": "EXM",
            "evidence_ledger": EvidenceLedger((evidence(),)),
            "prior_hypotheses": (),
        },
    )
    assert result.blocked_reasons
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "unregistered transform" in result.stage_traces[-1].rationale


def test_blind_red_team_rejects_market_comparison_evidence_context():
    result = run_controlled_workflow(
        run_id="LLM-BLIND",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("RESEARCHER_A", "BLIND_RED_TEAM_B"),
        adapters={
            "RESEARCHER_A": researcher_a_adapter(officer=researcher),
            "BLIND_RED_TEAM_B": blind_red_team_adapter(officer=red_team),
        },
        required_stages=("RESEARCHER_A", "BLIND_RED_TEAM_B"),
        initial_data={
            "company": "Example",
            "ticker": "EXM",
            "evidence_ledger": EvidenceLedger((evidence(layer=EvidenceSourceLayer.MARKET_COMPARISON),)),
            "prior_hypotheses": (),
        },
    )
    # Researcher can see only what caller supplied here; Blind Red Team independently enforces its lock.
    assert result.blocked_reasons
    assert result.stage_traces[-1].stage == "BLIND_RED_TEAM_B"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "market-comparison" in result.stage_traces[-1].rationale
