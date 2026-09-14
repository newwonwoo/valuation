import json
from pathlib import Path
from decimal import Decimal

from scripts.build_governed_distribution_report import build


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "runs" / "korean-air-003490" / "declarations" / "governed_distribution_spec.json"


def test_korean_air_governed_distribution_builds_audited_final_bundle(tmp_path):
    bundle = build(SPEC, tmp_path)
    manifest = json.loads((bundle / "bundle_manifest.json").read_text(encoding="utf-8"))
    distribution = json.loads((bundle / "equity_value_distribution.json").read_text(encoding="utf-8"))
    entry = json.loads((bundle / "entry_price.json").read_text(encoding="utf-8"))
    report = (bundle / "final_report.md").read_text(encoding="utf-8")

    assert manifest["status"] == "AUDITED_FINAL"
    assert manifest["audit_passed"] is True
    assert manifest["distribution_hash"] == distribution["distribution_hash"] == entry["distribution_hash"]
    assert manifest["supersedes_artifact_id"] == "003490-20260913-TP20813-47FF05D6D4DC"
    assert int(Decimal(entry["entry_price"])) > 0
    assert Decimal(entry["realized_success_probability"]) >= Decimal(entry["target_success_probability"])
    assert distribution["authorization_status"] == "GOVERNED_EVENT_PRIOR_WITH_AUDITED_SCENARIO_VALUE_BRIDGE"
    assert "확률가중 평균가치" in report
    assert "구체 매수가" in report
    assert "하방 상태도" in report
    assert "확률이 보정되지 않아" not in report
    assert "Σ[확률×max(0" not in report
    assert (bundle / "valuation_summary.svg").is_file()
    assert (bundle / "assumptions_risk_sources.svg").is_file()
