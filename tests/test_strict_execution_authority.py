from __future__ import annotations

import pytest

from valuation_engine.authority_orchestrator import (
    AuthorityControlledResult,
    _authority_validate_stage_result,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.llm_staff import RedTeamProposal
from valuation_engine.orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageExecutionResult,
)
from valuation_engine.records import (
    CriticalIssue,
    EvidenceRecord,
    EvidenceSourceLayer,
)
from valuation_engine.recovery_authority import (
    deterministic_recovery_readjudication_adapter,
)
from valuation_engine.strict_live_runtime import (
    CANONICAL_ENTRYPOINT_ID,
    require_canonical_live_result,
)


def test_llm_stage_cannot_emit_deterministic_valuation_decision():
    result = _authority_validate_stage_result(
        stage="RESEARCHER_A",
        result=StageExecutionResult(
            StageStatus.PASS,
            "proposal",
            {"valuation_hash": "LLM-SHOULD-NOT-OWN-THIS"},
        ),
    )
    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "valuation_hash" in result.rationale


def test_pre_freeze_stage_cannot_emit_market_decision():
    result = _authority_validate_stage_result(
        stage="ROCKET_INSIGHT_SCAN",
        result=StageExecutionResult(
            StageStatus.PASS,
            "bad scanner output",
            {"current_market_price": 123},
        ),
    )
    assert result.status is StageStatus.BLOCKED
    assert result.blocking


def test_unattested_live_result_is_noncanonical():
    base = ControlledRunResult(
        run_id="RUN-NO-ATTESTATION",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(),
        data={"canonical_entrypoint_id": CANONICAL_ENTRYPOINT_ID},
        blocked_reasons=(),
        freeze_token=None,
    )
    with pytest.raises(PermissionError, match="attestation"):
        require_canonical_live_result(AuthorityControlledResult(base, (), None))


def _evidence() -> EvidenceRecord:
    return EvidenceRecord(
        id="E-RECOVERY-1",
        target="TEST",
        metric="qualification_passed",
        value="yes",
        unit="dimensionless",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-08-20",
        observed_date="2026-08-20",
        source_name="official filing",
        source_ref="https://example.com/filing",
        source_grade="A",
        confidence=1.0,
        segment="memory",
    )


def _original_red_team() -> RedTeamProposal:
    return RedTeamProposal(
        issues=(CriticalIssue("BLOCK-1", "qualification missing", True, False),),
        counter_thesis="qualification may fail",
    )


def _recovered_red_team() -> RedTeamProposal:
    return RedTeamProposal(
        issues=(CriticalIssue("BLOCK-1", "qualification missing", True, True),),
        counter_thesis="qualification evidence now exists",
    )


def test_recovery_resolved_flag_alone_is_not_authority():
    def inner(_):
        return StageExecutionResult(
            StageStatus.RECOVERED,
            "provider says resolved",
            {"recovered_red_team_proposal": _recovered_red_team()},
        )

    context = OrchestratorContext(
        "RUN-R1",
        ExecutionMode.LIVE_PRIMARY,
        {
            "red_team_proposal": _original_red_team(),
            "evidence_ledger": EvidenceLedger((_evidence(),)),
        },
    )
    result = deterministic_recovery_readjudication_adapter(inner)(context)
    assert result.status is StageStatus.BLOCKED
    assert "insufficient" in result.rationale


def test_recovery_requires_current_ledger_evidence_and_emits_receipt():
    def inner(_):
        return StageExecutionResult(
            StageStatus.RECOVERED,
            "provider proposes resolution",
            {
                "recovered_red_team_proposal": _recovered_red_team(),
                "recovery_resolution_evidence_ids": ("E-RECOVERY-1",),
            },
        )

    context = OrchestratorContext(
        "RUN-R2",
        ExecutionMode.LIVE_PRIMARY,
        {
            "red_team_proposal": _original_red_team(),
            "evidence_ledger": EvidenceLedger((_evidence(),)),
        },
    )
    result = deterministic_recovery_readjudication_adapter(inner)(context)
    assert result.status is StageStatus.RECOVERED
    assert not result.blocking
    receipt = result.outputs["recovery_resolution_receipt"]
    receipt.validate()
    assert receipt.original_blocker_ids == ("BLOCK-1",)
    assert receipt.resolution_evidence_ids == ("E-RECOVERY-1",)
