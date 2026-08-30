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


def test_the_committed_daehan_run_refuses_to_flatten_its_consolidated_segments():
    """The prepared Daehan run remains useful as a fail-closed regression.

    Its consolidated filing discloses the 제강/압연 and 기타 divisions. Parent
    and subsidiary paragraphs separately call their steel processes a single
    division, but those entity-level statements cannot flatten the consolidated
    scope into the runbook's one ``core`` segment. Until multi-segment intent is
    declared, the industry snapshot must stop before intrinsic valuation.
    """
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "daehansteel-084010"
    )
    assert reached == ("COMPANY_RESOLUTION", "LOAD_COMPANY_STATE")
    assert stop_stage == "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT"
    assert "multiple operating segments" in stop_reason
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
        "**확률가중 기대값:** 주당 17,495원",
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
