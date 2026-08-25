from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
import yaml

from valuation_engine.live_company_acceptance import validate_live_company_acceptance
from valuation_engine.orchestrator import load_stage_sequence


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "live_company_acceptance.yaml"
STAGES = load_stage_sequence(ROOT / "config" / "control_plane_stage_registry.yaml")
HASH_FIELDS = (
    "ledger_snapshot_hash",
    "assumption_set_hash",
    "valuation_hash",
    "audit_hash",
    "industry_snapshot_hash",
    "source_snapshot_hash",
)


def _stable_hash(payload):
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_fixture(path, payload):
    payload = dict(payload)
    payload["run_integrity_hash"] = _stable_hash(payload)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


def _success_fixture(company_id):
    proofs = {
        field: {"payload": {"company_id": company_id, "field": field, "value": "proof"}}
        for field in HASH_FIELDS
    }
    hashes = {field: _stable_hash(proofs[field]["payload"]) for field in HASH_FIELDS}
    return {
        "artifact_type": "serialized_controlled_run/v1",
        "company_id": company_id,
        "synthetic": False,
        "run_id": f"RUN-{company_id}",
        "execution_mode": "LIVE_PRIMARY",
        "stage_traces": [
            {"stage": stage, "status": "pass", "blocking": False}
            for stage in STAGES
        ],
        "blocked_reasons": [],
        "freeze_token": {"run_id": f"RUN-{company_id}", **hashes},
        "data_hashes": hashes,
        "hash_proofs": proofs,
        "source_documents": [
            {
                "source_ref": "fixture://primary-document",
                "document_hash": "a" * 64,
                "first_seen_at": "2026-08-25T10:00:00+09:00",
            }
        ],
    }


def _blocked_fixture(company_id):
    prefix = STAGES[:4]
    traces = [
        {"stage": stage, "status": "pass", "blocking": False}
        for stage in prefix[:-1]
    ]
    traces.append({"stage": prefix[-1], "status": "blocked", "blocking": True})
    return {
        "artifact_type": "serialized_controlled_run/v1",
        "company_id": company_id,
        "synthetic": False,
        "run_id": f"RUN-{company_id}-BLOCKED",
        "execution_mode": "LIVE_PRIMARY",
        "stage_traces": traces,
        "blocked_reasons": ["adversarial failure: schema drift"],
        "freeze_token": None,
        "adversarial_case": {
            "id": "schema-drift",
            "expected_block_stage": prefix[-1],
            "expected_reason_contains": "adversarial failure",
        },
    }


def _temp_root(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy(
        ROOT / "config" / "control_plane_stage_registry.yaml",
        tmp_path / "config" / "control_plane_stage_registry.yaml",
    )
    return tmp_path


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
        validate_live_company_acceptance(path, repo_root=ROOT)


def test_ready_company_requires_both_success_and_adversarial_artifacts(tmp_path):
    root = _temp_root(tmp_path)
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    row = payload["companies"]["OCI_HOLDINGS"]
    row["status"] = "READY"
    row.pop("blocker", None)
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="is missing"):
        validate_live_company_acceptance(path, repo_root=root)


def test_ready_company_validates_full_hash_chain_and_adversarial_block(tmp_path):
    root = _temp_root(tmp_path)
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    row = payload["companies"]["OCI_HOLDINGS"]
    row["status"] = "READY"
    row.pop("blocker", None)
    success = root / row["success_fixture_path"]
    blocked = root / row["adversarial_fixture_path"]
    row["success_fixture_sha256"] = _write_fixture(success, _success_fixture("OCI_HOLDINGS"))
    row["adversarial_fixture_sha256"] = _write_fixture(blocked, _blocked_fixture("OCI_HOLDINGS"))
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    summary = validate_live_company_acceptance(path, repo_root=root)
    assert summary.ready == ("OCI_HOLDINGS",)
    assert "OCI_HOLDINGS" not in summary.blocked


def test_tampered_success_artifact_fails_file_hash_before_promotion(tmp_path):
    root = _temp_root(tmp_path)
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    row = payload["companies"]["OCI_HOLDINGS"]
    row["status"] = "READY"
    row.pop("blocker", None)
    success = root / row["success_fixture_path"]
    blocked = root / row["adversarial_fixture_path"]
    row["success_fixture_sha256"] = _write_fixture(success, _success_fixture("OCI_HOLDINGS"))
    row["adversarial_fixture_sha256"] = _write_fixture(blocked, _blocked_fixture("OCI_HOLDINGS"))
    success.write_text(success.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="file SHA-256 mismatch"):
        validate_live_company_acceptance(path, repo_root=root)


def test_blocked_company_requires_explicit_blocker(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    payload["companies"]["BLOOM_ENERGY"]["blocker"] = ""
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="requires blocker"):
        validate_live_company_acceptance(path, repo_root=ROOT)
