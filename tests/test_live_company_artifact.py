import importlib.util
from pathlib import Path

import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evidence_collection import EvidenceCollectionBatch, PrimaryEvidenceCollectionResult
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.live_company_artifact import (
    SourceDocumentLineage,
    _build_evidence_revision_bindings,
    serialize_live_company_blocked,
    serialize_live_company_success,
)
from valuation_engine.orchestrator import ControlledRunResult, StageTrace
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


ROOT = Path(__file__).resolve().parents[1]


def test_source_document_lineage_rejects_fixture_scheme():
    with pytest.raises(ValueError, match=r"absolute HTTP\(S\)"):
        SourceDocumentLineage(
            "fixture://filing",
            "a" * 64,
            "2026-08-25T10:00:00+09:00",
        ).validate()


def test_blocked_artifact_is_serialized_from_actual_controlled_result():
    result = ControlledRunResult(
        run_id="RUN-BLOCKED",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "COMPANY_RESOLUTION",
                StageStatus.BLOCKED,
                "adversarial identity mismatch",
                True,
            ),
        ),
        data={},
        blocked_reasons=("adversarial identity mismatch",),
        freeze_token=None,
    )
    artifact = serialize_live_company_blocked(
        result,
        company_id="ORACLE",
        adversarial_case_id="identity-mismatch",
        expected_reason_contains="identity mismatch",
    )
    assert artifact["synthetic"] is False
    assert artifact["stage_traces"][0]["stage"] == "COMPANY_RESOLUTION"
    assert artifact["adversarial_case"]["expected_block_stage"] == "COMPANY_RESOLUTION"
    assert len(artifact["run_integrity_hash"]) == 64


def test_generic_primary_evidence_uses_collected_batch_fingerprint_as_revision():
    record = EvidenceRecord(
        id="E1",
        target="T",
        metric="revenue",
        value=100,
        unit="USD",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-25",
        source_name="official API",
        source_ref="https://example.com/filing#revenue",
        source_grade="A",
        confidence=1.0,
    )
    ledger = EvidenceLedger()
    ledger.append(record)
    revision = "a" * 64
    batch = EvidenceCollectionBatch(
        source_id="OFFICIAL",
        checked_at="2026-08-25T10:00:00+09:00",
        records=(record,),
        source_fingerprint=revision,
    )
    collection = PrimaryEvidenceCollectionResult(
        ledger=ledger,
        batches=(batch,),
        required_metrics=("revenue",),
        covered_metrics=("revenue",),
        missing_metrics=(),
        source_snapshot_hash="b" * 64,
    )
    bindings = _build_evidence_revision_bindings(
        ledger,
        collection,
        (
            SourceDocumentLineage(
                "https://example.com/filing",
                revision,
                "2026-08-25T10:00:00+09:00",
            ),
        ),
    )
    assert bindings == [
        {
            "evidence_id": "E1",
            "source_ref": "https://example.com/filing",
            "revision_hash": revision,
        }
    ]


def test_generic_primary_evidence_rejects_arbitrary_document_hash():
    record = EvidenceRecord(
        id="E1",
        target="T",
        metric="revenue",
        value=100,
        unit="USD",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-25",
        source_name="official API",
        source_ref="https://example.com/filing#revenue",
        source_grade="A",
        confidence=1.0,
    )
    ledger = EvidenceLedger()
    ledger.append(record)
    batch = EvidenceCollectionBatch(
        source_id="OFFICIAL",
        checked_at="2026-08-25T10:00:00+09:00",
        records=(record,),
        source_fingerprint="a" * 64,
    )
    collection = PrimaryEvidenceCollectionResult(
        ledger=ledger,
        batches=(batch,),
        required_metrics=("revenue",),
        covered_metrics=("revenue",),
        missing_metrics=(),
        source_snapshot_hash="b" * 64,
    )
    with pytest.raises(ValueError, match="source revision does not match source document hash"):
        _build_evidence_revision_bindings(
            ledger,
            collection,
            (
                SourceDocumentLineage(
                    "https://example.com/filing",
                    "c" * 64,
                    "2026-08-25T10:00:00+09:00",
                ),
            ),
        )


def test_success_producer_refuses_existing_fixture_backed_full_live_run(tmp_path):
    path = ROOT / "tests" / "test_full_live_primary_runtime.py"
    spec = importlib.util.spec_from_file_location("_full_live_fixture_for_artifact_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.run_prism(module.runtime_config(tmp_path))
    assert result.completed

    with pytest.raises(ValueError, match=r"not backed by absolute HTTP\(S\) provenance"):
        serialize_live_company_success(
            result,
            company_id="OCI_HOLDINGS",
            source_documents=(
                SourceDocumentLineage(
                    "https://example.com/fake-primary",
                    "a" * 64,
                    "2026-08-25T10:00:00+09:00",
                ),
            ),
        )
