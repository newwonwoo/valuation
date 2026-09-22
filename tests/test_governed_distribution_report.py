import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from scripts.build_governed_distribution_report import build
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
    source_keys = (
        "source_model",
        "source_financing_spec",
        "source_risk_pack",
        "source_risk_result",
        "source_valuation_snapshot",
        "source_bundle_manifest",
        "source_primary_cashflow",
        "source_public_filing_facts",
        "source_market_observation",
    )
    for key in source_keys:
        spec[key] = str((RUN_DIR / spec[key]).resolve())
    spec["source_broker_comparison"] = str(broker_path)
    spec_path = tmp_path / "run" / "declarations" / "governed_distribution_spec.json"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return spec_path, broker_path


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
    assert result.minimum_expected_value == Decimal("1142.150768093133620112913164")
    assert result.maximum_expected_value == Decimal("7357.091858922905761460014388")
    assert outcomes[0].present_value < 0
    assert not result.calibrated_probability_claim_authorized


def test_korean_air_dated_payoff_route_builds_audited_bundle(tmp_path):
    bundle = build(SPEC, tmp_path)
    manifest = json.loads((bundle / "bundle_manifest.json").read_text(encoding="utf-8"))
    distribution = json.loads(
        (bundle / "equity_value_distribution.json").read_text(encoding="utf-8")
    )
    entry = json.loads((bundle / "entry_price.json").read_text(encoding="utf-8"))
    payoff_cases = json.loads(
        (bundle / "payoff_model_cases.json").read_text(encoding="utf-8")
    )
    broker = json.loads((bundle / "broker_comparison.json").read_text(encoding="utf-8"))
    audit = json.loads((bundle / "audit.json").read_text(encoding="utf-8"))
    report = (bundle / "final_report.md").read_text(encoding="utf-8")
    analysis = (bundle / "dated_payoff_analysis.md").read_text(encoding="utf-8")

    assert manifest["status"] == "AUDITED_FINAL"
    assert manifest["audit_passed"] is True
    assert manifest["canonical_entrypoint_id"] == "prism_strict_live_primary/v1"
    for receipt in (
        "valuation_hash",
        "audit_hash",
        "freeze_token_hash",
        "execution_attestation_hash",
        "distribution_hash",
        "route_authorization_hash",
        "entry_calculation_hash",
    ):
        assert manifest[receipt]
    assert manifest["supersedes_artifact_id"] == "003490-20260913-DIST-4664627231DA"
    minimum = Decimal(distribution["fair_value_interval_per_share"]["minimum"])
    maximum = Decimal(distribution["fair_value_interval_per_share"]["maximum"])
    assert minimum > 0
    assert maximum > minimum
    assert Decimal(entry["entry_price"]) > 0
    assert entry["point_target_authorized"] is False
    assert entry["success_probability_claim_authorized"] is False
    assert len(payoff_cases["rows"]) == 9
    assert any(row["distressed"] for row in payoff_cases["rows"])
    assert all(row["dated_cash_flows_per_share"] for row in payoff_cases["rows"])
    assert "가치평가 범위" in report
    assert "신규매수 보류" in report
    assert "보수적 진입 상한" in report
    assert "단일 확률가중 목표가: 미산출" in report
    assert "보정 성공확률: 미산출" in report
    assert "## 3. 가치평가와 민감도" in report
    assert "## 5. 위험과 판단 변경 조건" in report
    assert "## 6. 증권사·시장 비교" in report
    assert "하나증권" in report
    assert "LS증권" in report
    for forbidden in (
        "driver_distributional_apv",
        "capacity_yield_levered",
        "consolidated_operating_profit",
        "인공지능 인사이트",
        "valuation_hash",
        "근거 ID",
    ):
        assert forbidden not in report
    assert "미래에셋증권" in analysis
    assert "현재 장부부채를 5년 만기 행사가격처럼" in analysis
    assert report.index("## 3. 가치평가와 민감도") < report.index("## 6. 증권사·시장 비교") < report.index("## 7. 원문 자료")
    assert "확률가중 평균가치" not in report
    assert "수익 달성확률 75%" in analysis
    assert broker["sample"]["report_count"] == 3
    assert Decimal(broker["sample"]["median_target_price"]) == Decimal("37000")
    assert broker["intrinsic_distribution_unchanged"] is True
    assert audit["checks"]["canonical_live_primary_completed"] is True
    assert audit["checks"]["canonical_distributional_audit_passed"] is True
    assert audit["checks"]["canonical_freeze_token_present"] is True
    assert audit["checks"]["canonical_execution_attestation_present"] is True
    assert audit["checks"]["dated_payoff_diagnostic_replays"] is True
    assert audit["checks"]["source_bundle_manifest_and_artifacts_replay"] is True
    assert audit["checks"]["broker_loaded_only_after_intrinsic_freeze"] is True
    assert audit["checks"]["single_point_target_forbidden"] is True
    for artifact in (
        "canonical_valuation.json",
        "canonical_audit.json",
        "freeze_token.json",
        "execution_attestation.json",
        "canonical_stage_trace.json",
        "investor_report_profile.json",
        "distributional_summary.svg",
        "distributional_assumptions.svg",
    ):
        assert (bundle / artifact).is_file()


def test_broker_targets_change_comparison_but_not_frozen_intrinsic_value(tmp_path):
    spec_path, broker_path = _isolated_spec(tmp_path)
    before = build(spec_path, tmp_path / "before")
    before_manifest = json.loads(
        (before / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    before_distribution = json.loads(
        (before / "equity_value_distribution.json").read_text(encoding="utf-8")
    )

    broker = json.loads(broker_path.read_text(encoding="utf-8"))
    broker["verified_reports"][0]["target_price_krw"] = 39000
    broker_path.write_text(
        json.dumps(broker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    after = build(spec_path, tmp_path / "after")
    after_manifest = json.loads(
        (after / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    after_distribution = json.loads(
        (after / "equity_value_distribution.json").read_text(encoding="utf-8")
    )

    assert after_distribution["intrinsic_freeze_hash"] == before_distribution["intrinsic_freeze_hash"]
    assert after_manifest["intrinsic_freeze_hash"] == before_manifest["intrinsic_freeze_hash"]
    assert after_manifest["valuation_hash"] == before_manifest["valuation_hash"]
    assert after_manifest["audit_hash"] == before_manifest["audit_hash"]
    assert after_manifest["distribution_hash"] == before_manifest["distribution_hash"]
    assert after_manifest["post_freeze_comparison_hash"] != before_manifest["post_freeze_comparison_hash"]
    assert after_manifest["artifact_id"] != before_manifest["artifact_id"]


def test_broker_report_after_cutoff_fails_closed(tmp_path):
    spec_path, broker_path = _isolated_spec(tmp_path)
    broker = json.loads(broker_path.read_text(encoding="utf-8"))
    broker["verified_reports"][0]["report_date"] = "2026-09-14"
    broker_path.write_text(
        json.dumps(broker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="after the intrinsic cutoff"):
        build(spec_path, tmp_path / "future")


def test_source_snapshot_cannot_replace_verified_scenario_values(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = json.loads(Path(spec["source_valuation_snapshot"]).read_text(encoding="utf-8"))
    snapshot["scenario_values"][0]["equity_value_KRW"] = "9999999999999999"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    spec["source_valuation_snapshot"] = str(snapshot_path)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(ValueError, match="scenario values"):
        build(spec_path, tmp_path / "tampered-snapshot")


def test_source_bundle_manifest_hash_is_independently_pinned(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["source_bundle_manifest_sha256"] = "0" * 64
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        build(spec_path, tmp_path / "tampered-manifest")


def test_source_bundle_artifact_tampering_is_rejected(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    source_manifest = Path(spec["source_bundle_manifest"])
    copied_bundle = tmp_path / "source-bundle"
    shutil.copytree(source_manifest.parent, copied_bundle)
    copied_manifest = copied_bundle / source_manifest.name
    (copied_bundle / "valuation.json").write_text("{}", encoding="utf-8")
    spec["source_bundle_manifest"] = str(copied_manifest)
    spec["source_bundle_manifest_sha256"] = sha256(copied_manifest.read_bytes()).hexdigest()
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(ValueError, match="source artifact hash mismatch"):
        build(spec_path, tmp_path / "tampered-artifact")
