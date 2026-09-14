import json
from pathlib import Path
from decimal import Decimal

import pytest

from scripts.build_governed_distribution_report import build


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "runs" / "korean-air-003490" / "declarations" / "governed_distribution_spec.json"
RUN_DIR = SPEC.parent.parent


def _isolated_spec(tmp_path: Path) -> tuple[Path, Path]:
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
    spec_path = tmp_path / "run" / "declarations" / "governed_distribution_spec.json"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spec_path, broker_path


def test_korean_air_governed_distribution_builds_audited_final_bundle(tmp_path):
    bundle = build(SPEC, tmp_path)
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
    assert distribution["authorization_status"] == "GOVERNED_EVENT_PRIOR_WITH_AUDITED_SCENARIO_VALUE_BRIDGE"
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
