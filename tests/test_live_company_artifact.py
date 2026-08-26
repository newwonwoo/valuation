import importlib.util
from pathlib import Path

import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.live_company_artifact import (
    SourceDocumentLineage,
    serialize_live_company_blocked,
    serialize_live_company_success,
)
from valuation_engine.orchestrator import ControlledRunResult, StageTrace


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
