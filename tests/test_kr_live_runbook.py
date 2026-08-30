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


def test_the_committed_shinhanalpha_run_replays_to_the_attested_nav_envelope():
    """The second committed run crosses industry, fiscal calendar and output
    kind at once: a March-FYE K-REIT (신한알파리츠, 293940) on asset_yield_nav/
    nav — an equity-output method, so the plan binds no EV-to-equity
    adjustment — with no calibration block, so the expected value stays
    honestly unproduced while the scenario envelope completes 33 stages."""
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "shinhanalpha-293940"
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)
    assert not result.data.get("probability_weighting_allowed")

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 4,993원",
        "**기준 시나리오:** 내재가치 주당 9,986원",
        "**상방 시나리오:** 내재가치 주당 11,167원",
        "**확률가중 기대값:** 미산출",
        "**증권사 목표가:** 확보되지 않았습니다.",
        "**현재가:** 5,510원 (2026-08-28)",
    ):
        assert line in report, line


def test_the_committed_daehan_run_replays_to_the_attested_dcf_expected_value():
    """The third committed run opens the declared-risk-pack chain on a real
    company: 대한제강 (084010) on commodity_price_taker/
    midcycle_price_volume_dcf. The pack's L1→L4 peer regression betas are
    reproducible from the committed fchart series (scripts/
    compute_peer_betas.py), the WACC comes from the declared pack, and the
    expected value binds the committed KR steel cohort refitted without the
    target (83 rows / 11 companies)."""
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "daehansteel-084010"
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)
    assert result.data["probability_weighting_allowed"] is True

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 8,184원",
        "**기준 시나리오:** 내재가치 주당 26,292원",
        "**상방 시나리오:** 내재가치 주당 43,975원",
        "**확률가중 기대값:** 주당 26,712원",
        "**증권사 목표가:** 확보되지 않았습니다.",
        "**현재가:** 8,420원 (2026-08-28)",
    ):
        assert line in report, line


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
