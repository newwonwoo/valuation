from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
import yaml

from valuation_engine.live_company_acceptance import validate_live_company_acceptance
from valuation_engine.live_company_artifact import recompute_artifact_hash_proof
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


def _refresh_runtime_hashes(fixture):
    proofs = fixture["hash_proofs"]
    for field in HASH_FIELDS:
        digest = recompute_artifact_hash_proof(proofs[field])
        fixture["data_hashes"][field] = digest
        fixture["freeze_token"][field] = digest
    run_id = fixture["run_id"]
    token_hash = sha256(
        "|".join(
            (run_id, *(fixture["freeze_token"][field] for field in HASH_FIELDS))
        ).encode("utf-8")
    ).hexdigest()
    fixture["freeze_token"]["token_hash"] = token_hash
    fixture["market_compare"]["freeze_token_id"] = token_hash
    return fixture


def _success_fixture(company_id):
    source_ref = "https://example.com/primary-document"
    source_revision = "a" * 64
    evidence_id = f"E:{company_id}"
    ledger_payload = [
        {
            "id": evidence_id,
            "source_ref": f"{source_ref}#locator",
            "status": "active",
            "supersedes_id": None,
            "source_revision": source_revision,
        }
    ]
    source_payload = {
        "target_id": company_id,
        "batches": [
            {
                "source_id": "PRIMARY",
                "checked_at": "2026-08-25T10:00:00+09:00",
                "source_fingerprint": source_revision,
                "document_ids": ["DOC-1"],
                "evidence_ids": [evidence_id],
            }
        ],
        "evidence": [dict(row) for row in ledger_payload],
    }
    proofs = {
        field: {"payload": {"company_id": company_id, "field": field, "value": "proof"}}
        for field in HASH_FIELDS
    }
    proofs["ledger_snapshot_hash"] = {"payload": ledger_payload}
    proofs["source_snapshot_hash"] = {"payload": source_payload}
    hashes = {field: recompute_artifact_hash_proof(proofs[field]) for field in HASH_FIELDS}
    run_id = f"RUN-{company_id}"
    token_hash = sha256(
        "|".join((run_id, *(hashes[field] for field in HASH_FIELDS))).encode("utf-8")
    ).hexdigest()
    return {
        "artifact_type": "serialized_controlled_run/v1",
        "company_id": company_id,
        "synthetic": False,
        "run_id": run_id,
        "execution_mode": "LIVE_PRIMARY",
        "stage_traces": [
            {"stage": stage, "status": "pass", "blocking": False}
            for stage in STAGES
        ],
        "blocked_reasons": [],
        "freeze_token": {"run_id": run_id, "token_hash": token_hash, **hashes},
        "data_hashes": hashes,
        "hash_proofs": proofs,
        "source_documents": [
            {
                "source_ref": source_ref,
                "document_hash": source_revision,
                "first_seen_at": "2026-08-25T10:00:00+09:00",
            }
        ],
        "active_evidence_ids": [evidence_id],
        "evidence_revision_bindings": [
            {
                "evidence_id": evidence_id,
                "source_ref": source_ref,
                "revision_hash": source_revision,
            }
        ],
        "market_compare": {
            "phase": "post_freeze",
            "freeze_token_id": token_hash,
            "payload": {"gap": "fixture-contract-only"},
        },
        "final_report": {"summary": "fixture-contract-only"},
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


def _ready_manifest(tmp_path, company_id="OCI_HOLDINGS"):
    root = _temp_root(tmp_path)
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for other_id, other_row in payload["companies"].items():
        if other_id == company_id:
            continue
        other_row["status"] = "BLOCKED_SOURCE_FIXTURE"
        other_row["success_fixture_sha256"] = ""
        other_row["adversarial_fixture_sha256"] = ""
        other_row["blocker"] = "isolated unit-test blocker"
    row = payload["companies"][company_id]
    row["status"] = "READY"
    row.pop("blocker", None)
    success = root / row["success_fixture_path"]
    blocked = root / row["adversarial_fixture_path"]
    return root, payload, row, success, blocked


def _validate_one_ready(root, payload, row, success, blocked, fixture):
    row["success_fixture_sha256"] = _write_fixture(success, fixture)
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return validate_live_company_acceptance(path, repo_root=root)


def test_current_company_acceptance_manifest_tracks_all_required_real_companies():
    summary = validate_live_company_acceptance(MANIFEST, repo_root=ROOT)
    assert summary.ready == (
        "OCI_HOLDINGS",
        "ORACLE",
        "BLOOM_ENERGY",
        "GE_VERNOVA",
    )
    assert summary.blocked == ()


def test_manifest_fails_if_a_required_company_disappears(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    payload["companies"].pop("ORACLE")
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="company set mismatch"):
        validate_live_company_acceptance(path, repo_root=ROOT)


def test_ready_company_requires_both_success_and_adversarial_artifacts(tmp_path):
    root, payload, _, _, _ = _ready_manifest(tmp_path)
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="is missing"):
        validate_live_company_acceptance(path, repo_root=root)


def test_ready_company_validates_full_hash_chain_and_adversarial_block(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    summary = _validate_one_ready(
        root, payload, row, success, blocked, _success_fixture("OCI_HOLDINGS")
    )
    assert summary.ready == ("OCI_HOLDINGS",)
    assert "OCI_HOLDINGS" not in summary.blocked


def test_tampered_success_artifact_fails_file_hash_before_promotion(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    row["success_fixture_sha256"] = _write_fixture(
        success, _success_fixture("OCI_HOLDINGS")
    )
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    success.write_text(success.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="file SHA-256 mismatch"):
        validate_live_company_acceptance(path, repo_root=root)


def test_tampered_freeze_token_hash_fails_even_when_market_reference_and_integrity_are_rewritten(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    fixture = _success_fixture("OCI_HOLDINGS")
    fixture["freeze_token"]["token_hash"] = "c" * 64
    fixture["market_compare"]["freeze_token_id"] = "c" * 64
    row["success_fixture_sha256"] = _write_fixture(success, fixture)
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Freeze token_hash mismatch"):
        validate_live_company_acceptance(path, repo_root=root)


def test_tampered_evidence_revision_binding_fails_even_with_rewritten_integrity_hash(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    fixture = _success_fixture("OCI_HOLDINGS")
    fixture["evidence_revision_bindings"][0]["revision_hash"] = "d" * 64
    row["success_fixture_sha256"] = _write_fixture(success, fixture)
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="binding does not match frozen source revision"):
        validate_live_company_acceptance(path, repo_root=root)


def test_artifact_cannot_omit_an_active_ledger_row_from_bindings(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    fixture = _success_fixture("OCI_HOLDINGS")
    second = {
        "id": "E:OCI_HOLDINGS:SECOND",
        "source_ref": "https://example.com/primary-document#second",
        "status": "active",
        "supersedes_id": None,
        "source_revision": "a" * 64,
    }
    fixture["hash_proofs"]["ledger_snapshot_hash"]["payload"].append(second)
    source_payload = fixture["hash_proofs"]["source_snapshot_hash"]["payload"]
    source_payload["evidence"].append(dict(second))
    source_payload["evidence"] = sorted(source_payload["evidence"], key=lambda item: item["id"])
    source_payload["batches"][0]["evidence_ids"].append(second["id"])
    _refresh_runtime_hashes(fixture)
    row["success_fixture_sha256"] = _write_fixture(success, fixture)
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="active Evidence IDs do not match frozen ledger proof"):
        validate_live_company_acceptance(path, repo_root=root)


def test_explicit_ledger_source_revision_overrides_tampered_binding_and_batch(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    fixture = _success_fixture("OCI_HOLDINGS")
    fixture["source_documents"][0]["document_hash"] = "c" * 64
    fixture["evidence_revision_bindings"][0]["revision_hash"] = "c" * 64
    fixture["hash_proofs"]["source_snapshot_hash"]["payload"]["batches"][0][
        "source_fingerprint"
    ] = "c" * 64
    _refresh_runtime_hashes(fixture)
    row["success_fixture_sha256"] = _write_fixture(success, fixture)
    row["adversarial_fixture_sha256"] = _write_fixture(
        blocked, _blocked_fixture("OCI_HOLDINGS")
    )
    path = root / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="binding does not match frozen source revision"):
        validate_live_company_acceptance(path, repo_root=root)


def test_superseded_active_status_row_is_not_derived_as_active(tmp_path):
    root, payload, row, success, blocked = _ready_manifest(tmp_path)
    fixture = _success_fixture("OCI_HOLDINGS")
    old = fixture["hash_proofs"]["ledger_snapshot_hash"]["payload"][0]
    new = {
        "id": "E:OCI_HOLDINGS:NEW",
        "source_ref": old["source_ref"],
        "status": "active",
        "supersedes_id": old["id"],
        "source_revision": "a" * 64,
    }
    fixture["hash_proofs"]["ledger_snapshot_hash"]["payload"].append(new)
    source_payload = fixture["hash_proofs"]["source_snapshot_hash"]["payload"]
    source_payload["evidence"].append(dict(new))
    source_payload["evidence"] = sorted(source_payload["evidence"], key=lambda item: item["id"])
    source_payload["batches"][0]["evidence_ids"].append(new["id"])
    fixture["active_evidence_ids"] = [new["id"]]
    fixture["evidence_revision_bindings"] = [
        {
            "evidence_id": new["id"],
            "source_ref": "https://example.com/primary-document",
            "revision_hash": "a" * 64,
        }
    ]
    _refresh_runtime_hashes(fixture)
    summary = _validate_one_ready(root, payload, row, success, blocked, fixture)
    assert summary.ready == ("OCI_HOLDINGS",)


def test_blocked_company_requires_explicit_blocker(tmp_path):
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    row = payload["companies"]["BLOOM_ENERGY"]
    row["status"] = "BLOCKED_SOURCE_FIXTURE"
    row["blocker"] = ""
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="requires blocker"):
        validate_live_company_acceptance(path, repo_root=ROOT)


def test_hash_proof_supports_runtime_utf8_preimages_and_legacy_json_payloads():
    preimage = "run\nledger\nassumption\nvaluation"
    assert recompute_artifact_hash_proof({"encoding": "utf8", "preimage": preimage}) == sha256(
        preimage.encode("utf-8")
    ).hexdigest()
    legacy = {"a": 1, "b": [2, 3]}
    assert recompute_artifact_hash_proof({"payload": legacy}) == _stable_hash(legacy)


def test_hash_proof_rejects_unknown_encoding():
    with pytest.raises(ValueError, match="unsupported hash proof encoding"):
        recompute_artifact_hash_proof({"encoding": "pickle", "payload": {}})
