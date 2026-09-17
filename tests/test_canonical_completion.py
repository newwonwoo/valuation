from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from valuation_engine.canonical_completion import (
    BUNDLE_MANIFEST_NAME,
    BUNDLE_MANIFEST_SCHEMA,
    CANONICAL_ENTRYPOINT_ID,
    CompletionProofError,
    LATEST_MANIFEST_SCHEMA,
    expected_stage_sequence,
    validate_completion_bundle,
)
from valuation_engine.runtime_authority import (
    build_execution_attestation,
    make_stage_receipt,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bundle(tmp_path: Path, stages=("A", "B")) -> tuple[Path, Path]:
    out = tmp_path / "out"
    bundle = out / "bundles" / "bundle"
    bundle.mkdir(parents=True)
    run_id = "RUN-1"
    ticker = "000001"
    as_of = "2026-09-16"
    valuation_hash = "a" * 64
    audit_hash = "b" * 64
    freeze_hash = "c" * 64
    run_input_hash = "d" * 64
    stage_registry_hash = "e" * 64
    trace = [
        {
            "stage": stage,
            "status": "pass",
            "rationale": "stage completed",
            "blocking": False,
            "output_keys": [],
        }
        for stage in stages
    ]
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "ticker": ticker,
                "status": "COMPLETED",
                "audit_passed": True,
                "valuation_hash": valuation_hash,
            }
        ),
        encoding="utf-8",
    )
    (bundle / "control_plane_trace.json").write_text(
        json.dumps(trace), encoding="utf-8"
    )
    (bundle / "audit.json").write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "check": "test",
                        "passed": True,
                        "blocking": True,
                        "detail": "ok",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (bundle / "freeze_token.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "valuation_hash": valuation_hash,
                "audit_hash": audit_hash,
                "token_hash": freeze_hash,
            }
        ),
        encoding="utf-8",
    )
    attestation = build_execution_attestation(
        run_id=run_id,
        execution_mode="live_primary",
        receipts=tuple(
            make_stage_receipt(
                run_id=run_id,
                stage=stage["stage"],
                status=stage["status"],
                output_keys=(),
            )
            for stage in trace
        ),
        freeze_token_hash=freeze_hash,
        final_stage=stages[-1],
    )
    (bundle / "execution_attestation.json").write_text(
        json.dumps(asdict(attestation)), encoding="utf-8"
    )
    (bundle / "final_report.md").write_text("# audit\n", encoding="utf-8")
    (bundle / "000001_투자보고서.md").write_text("# investor\n", encoding="utf-8")

    receipts = [
        {"filename": path.relative_to(bundle).as_posix(), "sha256": _sha(path)}
        for path in sorted(bundle.iterdir())
        if path.name != BUNDLE_MANIFEST_NAME
    ]
    tree_hash = hashlib.sha256(
        json.dumps(
            receipts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    bundle_manifest = {
        "schema_version": BUNDLE_MANIFEST_SCHEMA,
        "artifact_id": "000001-20260916-TEST",
        "as_of": as_of,
        "run_id": run_id,
        "ticker": ticker,
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_hash,
        "stage_registry_sha256": stage_registry_hash,
        "freeze_token_hash": freeze_hash,
        "execution_attestation_hash": attestation.attestation_hash,
        "canonical_entrypoint_id": CANONICAL_ENTRYPOINT_ID,
        "bundle_tree_sha256": tree_hash,
        "report_sha256": _sha(bundle / "000001_투자보고서.md"),
        "report_filename": "000001_투자보고서.md",
        "files": receipts,
    }
    manifest_path = bundle / BUNDLE_MANIFEST_NAME
    manifest_path.write_text(json.dumps(bundle_manifest), encoding="utf-8")
    latest = {
        "schema_version": LATEST_MANIFEST_SCHEMA,
        "artifact_id": bundle_manifest["artifact_id"],
        "as_of": as_of,
        "run_id": run_id,
        "ticker": ticker,
        "bundle_directory": "bundles/bundle",
        "bundle_manifest": "bundles/bundle/report_bundle_manifest.json",
        "bundle_manifest_sha256": _sha(manifest_path),
        "report_filename": "bundles/bundle/000001_투자보고서.md",
        "report_sha256": bundle_manifest["report_sha256"],
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_hash,
        "stage_registry_sha256": stage_registry_hash,
        "execution_attestation_hash": attestation.attestation_hash,
        "bundle_tree_sha256": tree_hash,
        "canonical_entrypoint_id": CANONICAL_ENTRYPOINT_ID,
    }
    latest_path = out / "000001_LATEST_REPORT.json"
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    return bundle, latest_path


def test_completion_proof_requires_the_full_trace_and_hash_lineage(tmp_path):
    bundle, latest = _write_bundle(tmp_path)
    proof = validate_completion_bundle(
        bundle,
        stage_registry_path=tmp_path / "unused.yaml",
        expected_stages=("A", "B"),
        latest_manifest_path=latest,
    )
    assert proof.stage_count == 2
    assert proof.artifact_id == "000001-20260916-TEST"
    assert proof.bundle_tree_sha256
    assert proof.run_input_sha256 == "d" * 64
    assert proof.stage_registry_sha256 == "e" * 64


def test_production_validation_requires_all_33_registered_stages(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("phases:\n  only: [A]\n", encoding="utf-8")
    bundle, _ = _write_bundle(tmp_path)
    with pytest.raises(CompletionProofError, match="33 stages"):
        validate_completion_bundle(bundle, stage_registry_path=registry)


def test_incomplete_stage_cannot_be_published(tmp_path):
    bundle, _ = _write_bundle(tmp_path)
    trace_path = bundle / "control_plane_trace.json"
    trace = json.loads(trace_path.read_text())
    trace[1]["status"] = "blocked"
    trace_path.write_text(json.dumps(trace))
    with pytest.raises(CompletionProofError, match="not complete"):
        validate_completion_bundle(
            bundle, stage_registry_path=tmp_path / "unused.yaml", expected_stages=("A", "B")
        )


def test_mutated_report_is_detected_by_receipt_hash(tmp_path):
    bundle, _ = _write_bundle(tmp_path)
    (bundle / "000001_투자보고서.md").write_text("# changed\n")
    with pytest.raises(CompletionProofError, match="hash mismatch"):
        validate_completion_bundle(
            bundle, stage_registry_path=tmp_path / "unused.yaml", expected_stages=("A", "B")
        )


def test_unlisted_bundle_file_is_detected(tmp_path):
    bundle, _ = _write_bundle(tmp_path)
    (bundle / "unexpected.txt").write_text("not in manifest")
    with pytest.raises(CompletionProofError, match="exact files"):
        validate_completion_bundle(
            bundle, stage_registry_path=tmp_path / "unused.yaml", expected_stages=("A", "B")
        )


def test_registry_phase_order_is_the_production_stage_contract(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "phases:\n  first: [A]\n  second: [B]\n", encoding="utf-8"
    )
    bundle, _ = _write_bundle(tmp_path)
    proof = validate_completion_bundle(
        bundle, stage_registry_path=registry, expected_stages=expected_stage_sequence(registry)
    )
    assert proof.stage_count == 2


def test_legacy_bundle_schema_is_rejected(tmp_path):
    bundle, _ = _write_bundle(tmp_path)
    path = bundle / BUNDLE_MANIFEST_NAME
    payload = json.loads(path.read_text())
    payload["schema_version"] = "kr-live-report-bundle/v1"
    path.write_text(json.dumps(payload))
    with pytest.raises(CompletionProofError, match="v2"):
        validate_completion_bundle(
            bundle, stage_registry_path=tmp_path / "unused.yaml", expected_stages=("A", "B")
        )
