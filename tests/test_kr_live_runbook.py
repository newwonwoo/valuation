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
from hashlib import sha256
import importlib
from pathlib import Path
import shutil
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_kr_live  # noqa: E402
from run_kr_live import (  # noqa: E402
    execute_run,
    publish_report_bundle,
    reuse_published_report_bundle,
)
from valuation_engine.workflow import market_loader_from_config


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

def test_the_committed_daehan_run_replays_as_a_three_segment_sotp(tmp_path):
    """The third committed run is now the first true sum-of-the-parts: the
    IFRS 8 note names 제강/운송/기타, declarations/segments.yaml types each
    one (steel DCF at 0.8603 ownership, transport spread-DCF, leasing NAV),
    and every component carries its own key namespace and economic paths —
    wacc:...:steel is not wacc:...:transport."""
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "daehansteel-084010",
        state_root=str(tmp_path / "state"),
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

    visual_root = Path(result.data["saved_run_dir"])
    assumption_svg = (visual_root / result.data["saved_report_visuals"][1]).read_text(
        encoding="utf-8"
    )
    assert "귀속" in assumption_svg
    assert "86.0300%" in assumption_svg
    assert "100.0000%" in assumption_svg


def test_the_committed_celltrion_run_values_two_segments_and_reconciles_the_residual(
    tmp_path,
):
    """The fourth committed run is the one where the KSIC only proposes.

    Code 21 covers both a clinical-stage biotech and a commercial
    pharmaceutical manufacturer, so the route offers both archetypes and the
    segment's own filed revenue and operating income refute the pipeline
    premise — leaving capacity_manufacturing and, with it, a plan that never
    asks a 4조-revenue manufacturer for a trial registry. The IFRS 8 note names
    two reportable businesses plus its own residual row; the residual is
    matched, reconciled and left unvalued, and 제4·5공장 is disclosed, typed
    and kept out of Core because the filing evidences no site control for it.
    """
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "celltrion-068270",
        state_root=str(tmp_path / "state"),
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == len(result.stage_traces)

    aggregation = result.data["generic_valuation_result"].equity_aggregation
    base = next(
        item for item in aggregation.scenario_values if item.scenario_id == "Base"
    )
    by_asset = {item.asset_id: item for item in base.components}
    assert set(by_asset) == {"biologics", "chemical"}
    assert by_asset["chemical"].ownership_ratio == Decimal("0.5499")

    plan = result.data["module_requirement_plan"]
    biologics = next(
        item for item in plan.segments if item.segment_id == "biologics"
    )
    required = set(biologics.required_evidence)
    assert "nameplate_capacity" in required
    assert not required & {"trial_registry", "cash_runway", "stage"}

    commitment = result.data["capacity_commitment_assessment"]
    assert commitment.core_inclusion_required_projects == ()
    assert commitment.recovery_required_segments == ()

    report = result.data["final_report"]
    for line in (
        "**하방 시나리오:** 내재가치 주당 50,123원",
        "**기준 시나리오:** 내재가치 주당 128,758원",
        "**상방 시나리오:** 내재가치 주당 245,531원",
        "**현재가:** 188,100원 (2026-09-04)",
    ):
        assert line in report, line


def test_the_committed_koreazinc_run_preserves_llm_bound_ifrs8_refusal():
    """The Korea Zinc run proves the irregular-note boundary: an LLM-reviewed
    extraction is bound to the immutable filing member, while deterministic
    code verifies the disclosed labels and filed totals before valuation.

    The filing aggregates waste processing, minerals, renewables and battery
    materials in Other without activity weights. The declaration preserves that
    unresolved judgment and routing stops after the authoritative note bijection.
    """
    reached, stop_stage, stop_reason, result = execute_run(
        ROOT / "runs" / "koreazinc-010130"
    )
    assert stop_stage == "SEGMENT_DECOMPOSITION"
    assert len(reached) == 4
    assert "UNRESOLVED_HETEROGENEOUS" in stop_reason
    assert "authoritative IFRS 8 bijection" in stop_reason
    assert "refusing to assign one KSIC or value" in stop_reason
    assert not result.completed


def test_koreazinc_market_quote_is_bound_to_issuer_price_ticker_and_timestamp(tmp_path):
    path = ROOT / "runs" / "koreazinc-010130" / "declarations" / "market.yaml"
    market = market_loader_from_config(path)()
    assert market.price == 1222000
    assert market.as_of == "2026-09-04"
    assert "koreazinc.co.kr" in market.source_ref

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["market_comparison"]["price"] = 1223000
    tampered = tmp_path / "market.yaml"
    tampered.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ticker/price/timestamp binding mismatch"):
        market_loader_from_config(tampered)()

    source = payload["market_comparison"]["source_record"].replace(
        "1,222,000원", "1,223,000원"
    )
    payload["market_comparison"].update(
        price=1223000,
        source_record=source,
        source_record_sha256=sha256(source.encode("utf-8")).hexdigest(),
    )
    self_authenticated = tmp_path / "self_authenticated_market.yaml"
    self_authenticated.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not independently registered in code"):
        market_loader_from_config(self_authenticated)()

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["market_comparison"].pop("source_contract")
    payload["market_comparison"]["price"] = 1223000
    payload["market_comparison"]["as_of"] = "2026-09-05"
    missing_contract = tmp_path / "missing_contract_market.yaml"
    missing_contract.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="registered issuer quote requires"):
        market_loader_from_config(missing_contract)()


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


def test_missing_staff_proposal_is_a_typed_transport_failure(tmp_path, monkeypatch):
    from valuation_engine.llm_transport import TransportError

    monkeypatch.delenv("VALUATION_LLM_TRANSPORT", raising=False)
    transport = run_kr_live._StaffTransport(tmp_path)
    with pytest.raises(TransportError, match="no staff proposal file"):
        transport.complete(role="filing_table_reader", prompt="read")
