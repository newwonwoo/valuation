from __future__ import annotations

from dataclasses import dataclass

import valuation_engine.distributional_finalize as finalizer
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import ControlledRunResult, StageTrace
from valuation_engine.records import RunManifest, RunStatus
from valuation_engine.state import StateStore


@dataclass(frozen=True)
class _Envelope:
    def validate(self) -> None:
        return None


@dataclass(frozen=True)
class _DistributionalResult:
    envelope: _Envelope = _Envelope()


def test_distributional_finalizer_preserves_typed_result_and_finalizes_saved_bundle(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        finalizer,
        "DistributionalPrimaryValuationResult",
        _DistributionalResult,
    )
    store = StateStore(tmp_path)
    manifest = RunManifest(
        run_id="DIST-FINAL",
        ticker="000001",
        company="테스트",
        started_at="2026-09-17T00:00:00+09:00",
        finished_at="2026-09-17T00:01:00+09:00",
        status=RunStatus.COMPLETED,
        round_count=1,
        audit_passed=True,
    )
    run_dir = store.save_run(
        manifest,
        {
            "final_report.md": "old report\n",
            "control_plane_trace.json": [],
        },
    )
    result = ControlledRunResult(
        run_id="DIST-FINAL",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace("SAVE_STATE", StageStatus.PASS, "saved", False),
            StageTrace("FINAL_REPORT", StageStatus.PASS, "final", False),
        ),
        data={
            "ticker": "000001",
            "distributional_primary_result": _DistributionalResult(),
            "saved_report_markdown": "distributional report\n",
            "final_report": "distributional report\n",
        },
        blocked_reasons=(),
        freeze_token=None,
    )

    finalized = finalizer.finalize_distributional_live_primary_run_artifacts(
        result,
        state_root=tmp_path,
    )

    assert finalized.data["distributional_primary_result"] == _DistributionalResult()
    assert finalized.data["final_report"] == "distributional report\n"
    assert (run_dir / "final_report.md").read_text(encoding="utf-8") == "distributional report\n"
    trace = (run_dir / "control_plane_trace.json").read_text(encoding="utf-8")
    assert "FINAL_REPORT" in trace
