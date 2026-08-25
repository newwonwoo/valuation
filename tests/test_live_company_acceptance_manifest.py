from pathlib import Path

import pytest
import yaml

from valuation_engine.live_company_acceptance import validate_live_company_acceptance


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "live_company_acceptance.yaml"


def test_current_company_acceptance_manifest_tracks_all_required_real_companies():
    summary = validate_live_company_acceptance(MANIFEST, repo_root=ROOT)
    assert summary.ready == ()
    assert summary.blocked == (
        "OCI_HOLDINGS",
        "ORACLE",
        "BLOOM_ENERGY",
        "GE_VERNOVA",
    )


def test_manifest_fails_if_a_required_company_disappears(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    payload["companies"].pop("ORACLE")
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="company set mismatch"):
        validate_live_company_acceptance(path, repo_root=tmp_path)


def test_ready_company_requires_real_non_synthetic_fixture(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    row = payload["companies"]["OCI_HOLDINGS"]
    row["status"] = "READY"
    row.pop("blocker", None)
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="fixture is missing"):
        validate_live_company_acceptance(path, repo_root=tmp_path)


def test_blocked_company_requires_explicit_blocker(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    payload["companies"]["BLOOM_ENERGY"]["blocker"] = ""
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="requires blocker"):
        validate_live_company_acceptance(path, repo_root=tmp_path)
