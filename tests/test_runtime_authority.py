from __future__ import annotations

from valuation_engine.authority_orchestrator import authority_wrap_adapters
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext, StageExecutionResult
from valuation_engine.runtime_authority import (
    RuntimeActor,
    authority_snapshot,
    build_execution_attestation,
    llm_proposal_scope,
    make_stage_receipt,
    orchestrator_stage_scope,
)


def test_adapter_runs_inside_owning_orchestrator_stage_scope():
    seen = {}

    def adapter(_):
        snapshot = authority_snapshot()
        seen["actor"] = snapshot.actor
        seen["stage"] = snapshot.stage
        seen["run_id"] = snapshot.run_id
        return StageExecutionResult(StageStatus.PASS, "ok")

    wrapped = authority_wrap_adapters(
        run_id="RUN-1",
        adapters={"RESEARCHER_A": adapter},
    )["RESEARCHER_A"]
    result = wrapped(
        OrchestratorContext("RUN-1", ExecutionMode.LIVE_PRIMARY)
    )
    assert result.status is StageStatus.PASS
    assert seen == {
        "actor": RuntimeActor.ORCHESTRATOR,
        "stage": "RESEARCHER_A",
        "run_id": "RUN-1",
    }


def test_llm_scope_only_narrows_actor_and_restores_stage_owner():
    with orchestrator_stage_scope(run_id="RUN-2", stage="RESEARCHER_A"):
        assert authority_snapshot().actor is RuntimeActor.ORCHESTRATOR
        with llm_proposal_scope():
            snapshot = authority_snapshot()
            assert snapshot.actor is RuntimeActor.LLM
            assert snapshot.stage == "RESEARCHER_A"
            assert snapshot.run_id == "RUN-2"
        assert authority_snapshot().actor is RuntimeActor.ORCHESTRATOR


def test_execution_attestation_is_hash_bound_to_stage_receipts_and_freeze():
    receipts = (
        make_stage_receipt(
            run_id="RUN-3",
            stage="ROCKET_INSIGHT_SCAN",
            status="pass",
            output_keys=("scanner_findings",),
        ),
        make_stage_receipt(
            run_id="RUN-3",
            stage="FINAL_REPORT",
            status="pass",
            output_keys=("final_report",),
        ),
    )
    attestation = build_execution_attestation(
        run_id="RUN-3",
        execution_mode="live_primary",
        receipts=receipts,
        freeze_token_hash="FREEZE-HASH",
        final_stage="FINAL_REPORT",
    )
    attestation.validate()
    assert attestation.stage_receipt_hashes == tuple(
        item.receipt_hash for item in receipts
    )
