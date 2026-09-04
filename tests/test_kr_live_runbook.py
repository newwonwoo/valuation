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

import json
from decimal import Decimal
import importlib
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_kr_live  # noqa: E402
from run_kr_live import (  # noqa: E402
    execute_run,
    publish_report_bundle,
    reuse_published_report_bundle,
)


def test_the_committed_shinhanalpha_run_replays_to_the_attested_nav_envelope():
    """The second committed run crosses industry, fiscal calendar and output
    kind at once: a March-FYE K-REIT (신한알파리츠, 293940) on asset_yield_nav/
    nav — an equity-output method, so the plan binds no EV-to-equity
    adjustment — and its expected value binds a REIT cohort of its own: 57
    half-year observations from 7 listed K-REITs on the same semiannual
    reporting cadence as the target, the target itself excluded."""
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "shinhanalpha-293940"
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)
    assert result.data["probability_weighting_allowed"] is True

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 4,993원",
        "**기준 시나리오:** 내재가치 주당 9,986원",
        "**상방 시나리오:** 내재가치 주당 11,167원",
        "**확률가중 기대값:** 주당 9,504원",
        "**증권사 목표가:** 확보되지 않았습니다.",
        "**현재가:** 5,510원 (2026-08-28)",
    ):
        assert line in report, line


def test_the_committed_daehan_run_replays_as_a_three_segment_sotp():
    """The third committed run is now the first true sum-of-the-parts: the
    IFRS 8 note names 제강/운송/기타, declarations/segments.yaml types each
    one (steel DCF at 0.8603 ownership, transport spread-DCF, leasing NAV),
    and every component carries its own key namespace and economic paths —
    wacc:...:steel is not wacc:...:transport."""
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "daehansteel-084010"
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)
    assert result.data["probability_weighting_allowed"] is True

    aggregation = result.data["generic_valuation_result"].equity_aggregation
    base = next(
        item
        for item in aggregation.scenario_values
        if item.scenario_id == "Base"
    )
    by_asset = {item.asset_id: item for item in base.components}
    assert set(by_asset) == {"steel", "transport", "other"}
    assert by_asset["steel"].ownership_ratio == Decimal("0.8603")
    assert by_asset["other"].attributable_equity_value.amount == Decimal(
        "45700000000.00"
    )
    assert "path:transport_fcff_year_1" in by_asset["transport"].economic_path_ids
    assert not any(
        "steel" in path for path in by_asset["transport"].economic_path_ids
    )

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 10,284원",
        "**기준 시나리오:** 내재가치 주당 28,392원",
        "**상방 시나리오:** 내재가치 주당 46,076원",
        "**확률가중 기대값:** 주당 28,344원",
        "**현재가:** 8,420원 (2026-08-28)",
    ):
        assert line in report, line


def test_the_committed_koreazinc_run_replays_llm_bound_ifrs8_sotp():
    """The Korea Zinc run proves the irregular-note boundary: an LLM-reviewed
    extraction is bound to the immutable filing member, while deterministic
    code verifies the disclosed labels and filed totals before valuation.

    KSIC 24213 is not a registered steel calibration cohort, so the run must
    preserve the three-scenario envelope without fabricating an expected value.
    """
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "koreazinc-010130"
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces) == 33
    assert result.data["probability_weighting_allowed"] is False
    assert result.data["generic_valuation_result"].expected_value_per_share is None

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 299,725원",
        "**기준 시나리오:** 내재가치 주당 675,184원",
        "**상방 시나리오:** 내재가치 주당 1,107,219원",
        "**확률가중 기대값:** 미산출",
        "**현재가:** 1,223,000원 (2026-09-04)",
        "**기준 가정:** 제조 EBITDA 21,441억원 × 7.5배",
        "공통 지배주주 귀속률 97.8364%",
        "산식 [(부문 EBITDA×배수 합)+EV→지분 조정]×97.8364%÷20,393,232주",
        "**계산 확인:** 차단 점검 21/21개 통과 · 비차단 확인 필요 3건",
        "REFERENCE_ONLY",
    ):
        assert line in report, line


def test_a_multi_segment_filing_without_a_declaration_still_fails_closed(tmp_path):
    """Removing segments.yaml must put the refusal back: the screen stops the
    run at the industry snapshot and names the declaration to write."""
    run_copy = tmp_path / "daehan-undeclared"
    shutil.copytree(
        ROOT / "runs" / "daehansteel-084010",
        run_copy,
        ignore=shutil.ignore_patterns("out"),
    )
    (run_copy / "declarations" / "segments.yaml").unlink()
    # The copy lives outside runs/, so the run.yaml's relative calibration
    # paths cannot resolve; the refusal under test fires long before
    # calibration, so the block comes off the copy.
    run_yaml = run_copy / "run.yaml"
    text = run_yaml.read_text(encoding="utf-8")
    run_yaml.write_text(text.split("calibration:")[0], encoding="utf-8")
    reached, stop_stage, stop_reason, result = execute_run(run_copy)
    assert stop_stage == "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT"
    assert "declare the reportable segments" in stop_reason
    assert not result.completed


def test_the_committed_kisco_run_replays_to_the_attested_expected_value(
    tmp_path, monkeypatch
):
    run_dir = ROOT / "runs" / "kisco-104700"
    reached, stop_stage, stop_reason, result = execute_run(
        run_dir, state_root=str(tmp_path / "state")
    )
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
        "**확률가중 기대값:** 주당 16,876원",
        "**증권사 목표가:** 확보되지 않았습니다.",
        "**현재가:** 10,120원 (2026-08-28)",
    ):
        assert line in report, line

    published = publish_report_bundle(
        run_dir, result, output_dir=tmp_path / "published"
    )
    latest = json.loads(
        Path(published["latest_manifest_path"]).read_text(encoding="utf-8")
    )
    bundle = tmp_path / "published" / latest["bundle_directory"]
    bundle_manifest = json.loads(
        (tmp_path / "published" / latest["bundle_manifest"]).read_text(
            encoding="utf-8"
        )
    )
    assert latest["artifact_id"] in Path(
        published["versioned_report_path"]
    ).read_text(encoding="utf-8")
    assert latest["report_filename"].endswith(".md")
    assert (bundle / "control_plane_trace.json").is_file()
    assert (bundle / "audit.json").is_file()
    assert (bundle / "execution_attestation.json").is_file()
    assert len(result.data["saved_report_visuals"]) == 2
    assert all((bundle / name).is_file() for name in result.data["saved_report_visuals"])
    assert bundle_manifest["artifact_id"] == latest["artifact_id"]
    assert bundle_manifest["valuation_hash"] == result.data["valuation_hash"]
    assert latest["run_input_sha256"] == run_kr_live._run_input_sha256(run_dir)

    alias = tmp_path / "second-invocation-report.md"
    reused = reuse_published_report_bundle(
        run_dir,
        output_dir=tmp_path / "published",
        report_alias=alias,
    )
    assert reused is not None
    assert reused["artifact_id"] == published["artifact_id"]
    assert reused["versioned_report_path"] == published["versioned_report_path"]
    assert alias.read_text(encoding="utf-8") == report

    changed_run = tmp_path / "changed-run"
    shutil.copytree(run_dir, changed_run, ignore=shutil.ignore_patterns("out"))
    underwriting = changed_run / "declarations" / "underwriting.yaml"
    underwriting.write_text(
        underwriting.read_text(encoding="utf-8").replace(
            "    value: 60\n", "    value: 61\n", 1
        ),
        encoding="utf-8",
    )
    assert run_kr_live._run_input_sha256(changed_run) != latest["run_input_sha256"]
    assert reuse_published_report_bundle(
        changed_run, output_dir=tmp_path / "published"
    ) is None

    transport_module = tmp_path / "live_hash_transport.py"
    transport_module.write_text("def build():\n    return object()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    monkeypatch.setenv("VALUATION_LLM_TRANSPORT", "live_hash_transport:build")
    transport_hash = run_kr_live._run_input_sha256(run_dir)
    monkeypatch.setenv("VALUATION_LLM_MODEL", "changed-model")
    assert run_kr_live._run_input_sha256(run_dir) != transport_hash
    monkeypatch.setenv("VALUATION_LLM_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.test")
    assert run_kr_live._run_input_sha256(run_dir) != transport_hash
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    monkeypatch.setenv("VALUATION_LLM_MAX_TOKENS", "8192")
    assert run_kr_live._run_input_sha256(run_dir) != transport_hash
    monkeypatch.setenv("VALUATION_LLM_MAX_TOKENS", "")
    transport_module.write_text("def build():\n    return None\n", encoding="utf-8")
    assert run_kr_live._run_input_sha256(run_dir) != transport_hash

    monkeypatch.delenv("VALUATION_LLM_TRANSPORT")

    def unexpected_execute(*args, **kwargs):
        raise AssertionError("a verified published run must not execute again")

    monkeypatch.setattr(run_kr_live, "execute_run", unexpected_execute)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_kr_live.py",
            str(run_dir),
            "--report-out",
            str(tmp_path / "main-second-invocation.md"),
        ],
    )
    # Point main's fixed <run_dir>/out location at the already verified test
    # publication without mutating the committed run directory.
    monkeypatch.setattr(
        run_kr_live,
        "reuse_published_report_bundle",
        lambda *args, **kwargs: reused,
    )
    assert run_kr_live.main() == 0
