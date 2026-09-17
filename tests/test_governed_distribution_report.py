import json
from hashlib import sha256
from pathlib import Path
from decimal import Decimal

import pytest

from scripts.build_governed_distribution_report import build, _source_valuation_snapshot
from valuation_engine.governed_event_distribution import GovernedDistributionError
from valuation_engine.probability_ambiguity import (
    AmbiguityValueStatus,
    ProbabilityVector,
    SignedOutcomeValue,
    calculate_ambiguity_expected_value_range,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "runs" / "korean-air-003490" / "declarations" / "governed_distribution_spec.json"
RUN_DIR = SPEC.parent.parent


def _isolated_spec(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    broker_source = (RUN_DIR / spec["source_broker_comparison"]).resolve()
    broker_path = tmp_path / "broker_comparison.json"
    broker_path.write_text(broker_source.read_text(encoding="utf-8"), encoding="utf-8")
    for key in (
        "source_valuation_snapshot",
        "source_financing_spec",
        "source_risk_pack",
        "source_market_observation",
    ):
        spec[key] = str((RUN_DIR / spec[key]).resolve())
    spec["source_broker_comparison"] = str(broker_path)
    # Synthetic happy-path qualification for reporter plumbing only.  The
    # production Korean Air input intentionally lacks these qualifications and
    # must fail closed because it aggregates current carrying claims across
    # multiple maturities.
    spec["structural_model_qualification"] = {
        "claim_basis": "PROMISED_AT_HORIZON",
        "asset_basis": "MARKET_CALIBRATED",
        "volatility_basis": "CALIBRATED_ASSET_RETURNS",
        "maturity_basis": "SINGLE_MATURITY",
        "evidence_path_ids": ["test-only:qualified-structural-inputs"],
        "permitted_role": "PRIMARY_VALUE",
    }
    spec["probability_basis"] = "CALIBRATED_EVENT_PROBABILITY"
    spec["probability_authorization"]["status"] = (
        "CALIBRATED_EVENT_PROBABILITY"
    )
    spec["probability_authorization"]["not_claimed"] = (
        "TEST_FIXTURE_NOT_LIVE_COMPANY_VALIDATION"
    )
    spec["probability_authorization"]["basis"] = (
        "Test-only calibrated probability fixture for report plumbing."
    )
    spec["entry_policy"]["policy_version"] = "three_year_return_quantile/v1"
    spec["entry_policy"]["quantile_role"] = "PRIMARY_POLICY"
    spec["entry_policy"]["probability_success_claim_authorized"] = True
    snapshot = json.loads(Path(spec["source_valuation_snapshot"]).read_text())
    source = tmp_path / "source"
    source.mkdir()
    source_files = {
        "valuation.json": {
            "valuation_hash": snapshot["source_valuation_hash"],
            "equity_aggregation": {"scenario_values": [
                {"scenario_id": row["scenario_id"], "equity_value": {
                    "amount": row["equity_value_KRW"], "unit": "KRW", "as_of": spec["as_of"]}}
                for row in snapshot["scenario_values"]]},
        },
        "audit.json": {"findings": [{"check": "test-only", "passed": True, "blocking": True}]},
        "manifest.json": {"status": "COMPLETED", "audit_passed": True,
                          "run_id": snapshot["source_run_id"], "ticker": spec["ticker"]},
        "freeze_token.json": {"run_id": snapshot["source_run_id"],
                              "valuation_hash": snapshot["source_valuation_hash"],
                              "audit_hash": snapshot["source_audit_hash"]},
        "compiled_assumptions.json": {"target_id": spec["target_id"]},
    }
    for name, payload in source_files.items():
        (source / name).write_text(json.dumps(payload))
    manifest = {
        "schema_version": "kr-live-report-bundle/v1",
        "artifact_id": snapshot["source_artifact_id"], "run_id": snapshot["source_run_id"],
        "as_of": spec["as_of"], "ticker": spec["ticker"],
        "valuation_hash": snapshot["source_valuation_hash"], "audit_hash": snapshot["source_audit_hash"],
        "files": [{"filename": name, "sha256": sha256((source / name).read_bytes()).hexdigest()}
                  for name in source_files],
    }
    manifest_path = source / "report_bundle_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    spec["source_bundle_manifest"] = str(manifest_path)
    spec["source_bundle_manifest_sha256"] = sha256(manifest_path.read_bytes()).hexdigest()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))
    spec["source_valuation_snapshot"] = str(snapshot_path)
    spec_path = tmp_path / "run" / "declarations" / "governed_distribution_spec.json"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spec_path, broker_path


def test_korean_air_current_merton_overlay_is_rejected_as_primary_value(tmp_path):
    with pytest.raises(ValueError, match="source bundle manifest"):
        build(SPEC, tmp_path)
    assert not tuple(tmp_path.iterdir())


def test_korean_air_signed_scenarios_produce_an_ambiguity_value_range_not_a_floor():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    snapshot = json.loads(
        (RUN_DIR / spec["source_valuation_snapshot"]).read_text(encoding="utf-8")
    )
    source_values = {
        row["scenario_id"]: Decimal(row["equity_value_KRW"])
        for row in snapshot["scenario_values"]
    }
    shares = Decimal(spec["diluted_shares"])
    branch_ids = tuple(row["branch_id"] for row in spec["branches"])
    outcomes = tuple(
        SignedOutcomeValue(
            outcome_id=row["branch_id"],
            present_value=source_values[row["source_scenario"]] / shares,
            evidence_path_ids=(snapshot["source_valuation_hash"],),
        )
        for row in spec["branches"]
    )
    vectors = tuple(
        ProbabilityVector(
            vector_id=row["label"],
            weights=tuple(
                (branch_id, Decimal(weight))
                for branch_id, weight in zip(branch_ids, row["probabilities"])
            ),
            evidence_path_ids=tuple(row["evidence_path_ids"]),
        )
        for row in spec["probability_authorization"]["sensitivity_sets"]
    )
    result = calculate_ambiguity_expected_value_range(
        outcomes=outcomes,
        probability_vectors=vectors,
        values_authorized=True,
        value_set_hash=snapshot["source_valuation_hash"],
    )
    assert result.status is AmbiguityValueStatus.AVAILABLE
    assert result.minimum_expected_value == Decimal(
        "1142.150768093133620112913164"
    )
    assert result.maximum_expected_value == Decimal(
        "7357.091858922905761460014388"
    )
    assert outcomes[0].present_value < 0
    assert not result.calibrated_probability_claim_authorized


def test_qualified_fixture_remains_uncertified_and_never_promotes_latest(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path / "inputs")
    run = spec_path.parent.parent
    output = run / "out"
    output.mkdir()
    latest = output / "003490_LATEST_DISTRIBUTIONAL_REPORT.json"
    latest.write_text("last-good")
    spec = json.loads(spec_path.read_text())
    spec["source_market_observation"] = "/missing/market.json"
    spec["source_broker_comparison"] = "/missing/broker.json"
    spec_path.write_text(json.dumps(spec))
    bundle = build(spec_path, output)
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    audit = json.loads((bundle / "audit.json").read_text())
    assert manifest["status"] == "DIAGNOSTIC_ONLY"
    assert manifest["audit_passed"] is False
    assert audit["passed"] is False
    assert audit["diagnostic_checks_passed"] is True
    assert audit["canonical_audit_status"] == "NOT_RUN"
    assert "intrinsic_freeze_hash" not in manifest
    assert "supersedes_artifact_id" not in manifest
    assert latest.read_text() == "last-good"
    assert not (bundle / "final_report.md").exists()
    assert not (bundle / "broker_comparison.json").exists()
    for receipt in manifest["files"]:
        assert sha256((bundle / receipt["filename"]).read_bytes()).hexdigest() == receipt["sha256"]


def test_source_snapshot_cannot_replace_verified_scenario_values(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    snapshot_path = Path(spec["source_valuation_snapshot"])
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["scenario_values"][0]["equity_value_KRW"] = "9999999999999999"
    snapshot_path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match="scenario values"):
        build(spec_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("filename", ["valuation.json", "audit.json", "freeze_token.json", "manifest.json"])
def test_source_artifact_tampering_is_rejected(tmp_path, filename):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    source = Path(spec["source_bundle_manifest"]).parent
    (source / filename).write_text("{}")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        build(spec_path, tmp_path / "out")


@pytest.mark.parametrize("field,value", [("target_id", "OTHER"), ("as_of", "2026-09-12"),
                                          ("source_audit_hash", "fake"), ("source_valuation_hash", "fake")])
def test_snapshot_identity_and_receipts_are_bound(tmp_path, field, value):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    snapshot_path = Path(spec["source_valuation_snapshot"])
    snapshot = json.loads(snapshot_path.read_text())
    snapshot[field] = value
    snapshot_path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match="mismatch"):
        build(spec_path, tmp_path / "out")


def test_generic_diagnostic_uses_input_branches_company_and_policy(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["company"] = "시험 운송사"
    for row, label in zip(spec["branches"], ("Weak", "Normal", "Strong")):
        row["branch_id"] = label
    spec["entry_policy"]["horizon_years"] = 4
    spec["entry_policy"]["required_annual_return"] = "0.15"
    spec["annual_asset_volatility"] = "0.18"
    spec_path.write_text(json.dumps(spec))
    bundle = build(spec_path, tmp_path / "out")
    report = (bundle / "diagnostic_report.md").read_text()
    assert report.startswith("# 시험 운송사")
    assert "Weak" in report and "Normal" in report and "Strong" in report
    assert "4년 · 연 15.0%" in report and "18.0%" in report
    assert "현재가에서는 신규매수 보류" not in report
    assert "통합·회복 실패" not in report


def test_qualification_gate_is_preserved_after_source_verification(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    original = json.loads(SPEC.read_text())
    spec["structural_model_qualification"] = original["structural_model_qualification"]
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(GovernedDistributionError, match="promised claim amount"):
        build(spec_path, tmp_path / "out")
