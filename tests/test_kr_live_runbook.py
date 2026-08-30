"""The committed KISCO run directory replays to the same attested report.

runs/kisco-104700 is the runbook's living example: real public-DART raw
payloads, the operator's declarations, the staff seats' proposals and the
calibration binding, together. Replaying it through the runbook runner is a
full-pipeline live regression — 33 stages, the frozen scenario values, the
calibrated probability weighting and the expected value must all reproduce.
If engine behavior changes any of these numbers, this test is where the
change must be seen and owned.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_kr_live import execute_run  # noqa: E402


def test_the_committed_kisco_run_replays_to_the_attested_expected_value():
    reached, stop_stage, stop_reason, result = execute_run(ROOT / "runs" / "kisco-104700")
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)

    assert result.data["probability_weighting_allowed"] is True
    snapshot = result.data["continuous_probability_calibration_snapshot"]
    probabilities = dict(snapshot.probabilities)
    assert float(probabilities["Base"]) > 0.5

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 14,115원",
        "**기준 시나리오:** 내재가치 주당 17,339원",
        "**상방 시나리오:** 내재가치 주당 21,248원",
        "**확률가중 기대값:** 주당 17,495원",
        "**증권사 목표가:** 확보되지 않았습니다.",
        "**현재가:** 10,120원 (2026-08-28)",
    ):
        assert line in report, line
