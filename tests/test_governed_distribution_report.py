import json
from pathlib import Path
from decimal import Decimal

import pytest

from scripts.build_governed_distribution_report import build
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
    spec_path = tmp_path / "run" / "declarations" / "governed_distribution_spec.json"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spec_path, broker_path


def test_korean_air_current_merton_overlay_is_rejected_as_primary_value(tmp_path):
    with pytest.raises(GovernedDistributionError, match="promised claim amount"):
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


def test_explicitly_qualified_structural_fixture_builds_audited_bundle(tmp_path):
    spec_path, _ = _isolated_spec(tmp_path / "inputs")
    bundle = build(spec_path, tmp_path / "output")
    manifest = json.loads((bundle / "bundle_manifest.json").read_text(encoding="utf-8"))
    distribution = json.loads((bundle / "equity_value_distribution.json").read_text(encoding="utf-8"))
    entry = json.loads((bundle / "entry_price.json").read_text(encoding="utf-8"))
    broker = json.loads((bundle / "broker_comparison.json").read_text(encoding="utf-8"))
    audit = json.loads((bundle / "audit.json").read_text(encoding="utf-8"))
    report = (bundle / "final_report.md").read_text(encoding="utf-8")

    assert manifest["status"] == "AUDITED_FINAL"
    assert manifest["audit_passed"] is True
    assert manifest["distribution_hash"] == distribution["distribution_hash"] == entry["distribution_hash"]
    assert manifest["supersedes_artifact_id"] == "003490-20260913-DIST-8B3A2C1C58D8"
    assert int(Decimal(entry["entry_price"])) > 0
    assert Decimal(entry["realized_success_probability"]) >= Decimal(entry["target_success_probability"])
    assert distribution["authorization_status"] == (
        "CALIBRATED_EVENT_PROBABILITY_WITH_QUALIFIED_STRUCTURAL_VALUE"
    )
    assert "확률가중 평균가치" in report
    assert "구체 매수가" in report
    assert "하방 상태도" in report
    assert "## 증권사·시장 비교" in report
    assert "미래에셋증권" in report
    assert "하나증권" in report
    assert "LS증권" in report
    assert "공개 원문 검증 표본" in report
    assert "PBR 1.1x" in report
    assert report.index("## 핵심 가정과 위험") < report.index("## 증권사·시장 비교") < report.index("## 원문")
    assert "확률이 보정되지 않아" not in report
    assert "Σ[확률×max(0" not in report
    assert broker["sample"]["report_count"] == 3
    assert Decimal(broker["sample"]["median_target_price"]) == Decimal("37000")
    assert Decimal(broker["sample"]["min_target_price"]) == Decimal("33000")
    assert Decimal(broker["sample"]["max_target_price"]) == Decimal("41000")
    assert broker["intrinsic_distribution_unchanged"] is True
    assert audit["checks"]["broker_loaded_only_after_intrinsic_freeze"] is True
    assert audit["checks"]["broker_targets_excluded_from_intrinsic_inputs"] is True
    assert manifest["post_freeze_comparison_hash"] == broker["post_freeze_comparison_hash"]
    assert (bundle / "valuation_summary.svg").is_file()
    assert (bundle / "assumptions_risk_sources.svg").is_file()


def test_broker_targets_change_comparison_but_not_frozen_intrinsic_value(tmp_path):
    spec_path, broker_path = _isolated_spec(tmp_path)
    before = build(spec_path, tmp_path / "before")
    before_manifest = json.loads((before / "bundle_manifest.json").read_text(encoding="utf-8"))
    before_distribution = json.loads(
        (before / "equity_value_distribution.json").read_text(encoding="utf-8")
    )

    broker = json.loads(broker_path.read_text(encoding="utf-8"))
    broker["verified_reports"][0]["target_price_krw"] = 39000
    broker_path.write_text(json.dumps(broker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    after = build(spec_path, tmp_path / "after")
    after_manifest = json.loads((after / "bundle_manifest.json").read_text(encoding="utf-8"))
    after_distribution = json.loads(
        (after / "equity_value_distribution.json").read_text(encoding="utf-8")
    )

    assert after_distribution["distribution_hash"] == before_distribution["distribution_hash"]
    assert after_manifest["intrinsic_freeze_hash"] == before_manifest["intrinsic_freeze_hash"]
    assert after_manifest["post_freeze_comparison_hash"] != before_manifest["post_freeze_comparison_hash"]
    assert after_manifest["artifact_id"] != before_manifest["artifact_id"]


def test_broker_report_after_cutoff_fails_closed(tmp_path):
    spec_path, broker_path = _isolated_spec(tmp_path)
    broker = json.loads(broker_path.read_text(encoding="utf-8"))
    broker["verified_reports"][0]["report_date"] = "2026-09-14"
    broker_path.write_text(json.dumps(broker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="after the intrinsic cutoff"):
        build(spec_path, tmp_path / "future")
