from pathlib import Path

import pytest

import valuation_engine.live_company_capture as capture
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.live_company_artifact import SourceDocumentLineage
from valuation_engine.orchestrator import ControlledRunResult, StageTrace


def _request(tmp_path, *, mode="blocked"):
    return capture.LiveCompanyCaptureRequest(
        company_id="ORACLE",
        company_query="Oracle",
        jurisdiction="US",
        state_root=tmp_path / "state",
        run_id="RUN-CAPTURE",
        mode=mode,
        provider_factory_spec="private.bundle:build",
        source_documents=(
            SourceDocumentLineage(
                "https://www.sec.gov/Archives/edgar/data/1341439/report.htm",
                "a" * 64,
                "2026-08-25T10:00:00+00:00",
            ),
        ) if mode == "success" else (),
        adversarial_case_id="identity-mismatch" if mode == "blocked" else "",
        expected_reason_contains="identity mismatch" if mode == "blocked" else "",
    )


def test_success_capture_requires_source_lineage(tmp_path):
    request = capture.LiveCompanyCaptureRequest(
        company_id="ORACLE",
        company_query="Oracle",
        jurisdiction="US",
        state_root=tmp_path,
        run_id="RUN",
        mode="success",
        provider_factory_spec="x:y",
    )
    with pytest.raises(ValueError, match="source document lineage"):
        request.validate()


def test_capture_wires_operator_factory_to_blocked_artifact(monkeypatch, tmp_path):
    sentinel_factory = object()
    sentinel_config = object()
    result = ControlledRunResult(
        run_id="RUN-CAPTURE",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "COMPANY_RESOLUTION",
                StageStatus.BLOCKED,
                "identity mismatch",
                True,
            ),
        ),
        data={},
        blocked_reasons=("identity mismatch",),
        freeze_token=None,
    )
    monkeypatch.setattr(capture, "resolve_provider_factory_spec", lambda spec: spec)
    monkeypatch.setattr(capture, "load_live_runtime_config_factory", lambda spec: sentinel_factory)
    monkeypatch.setattr(capture, "build_live_runtime_config", lambda request, factory: sentinel_config)
    monkeypatch.setattr(capture, "run_prism", lambda config: result)

    artifact = capture.capture_live_company_fixture(_request(tmp_path))
    assert artifact["company_id"] == "ORACLE"
    assert artifact["execution_mode"] == "LIVE_PRIMARY"
    assert artifact["adversarial_case"]["id"] == "identity-mismatch"


def test_success_capture_delegates_actual_result_and_lineage(monkeypatch, tmp_path):
    request = _request(tmp_path, mode="success")
    sentinel_result = object()
    seen = {}
    monkeypatch.setattr(capture, "resolve_provider_factory_spec", lambda spec: spec)
    monkeypatch.setattr(capture, "load_live_runtime_config_factory", lambda spec: object())
    monkeypatch.setattr(capture, "build_live_runtime_config", lambda request, factory: object())
    monkeypatch.setattr(capture, "run_prism", lambda config: sentinel_result)

    def serialize(result, *, company_id, source_documents):
        seen.update(result=result, company_id=company_id, source_documents=source_documents)
        return {"artifact_type": "serialized_controlled_run/v1", "company_id": company_id}

    monkeypatch.setattr(capture, "serialize_live_company_success", serialize)
    artifact = capture.capture_live_company_fixture(request)
    assert artifact["company_id"] == "ORACLE"
    assert seen["result"] is sentinel_result
    assert seen["source_documents"] == request.source_documents


def test_source_lineage_json_and_writer_are_deterministic(tmp_path):
    lineage_path = tmp_path / "lineage.json"
    lineage_path.write_text(
        '[{"source_ref":"https://example.com/doc","document_hash":"' + "b" * 64 + '","first_seen_at":"2026-08-25T10:00:00+00:00"}]',
        encoding="utf-8",
    )
    documents = capture.load_source_document_lineage(lineage_path)
    assert documents[0].source_ref == "https://example.com/doc"

    output = tmp_path / "artifact.json"
    digest1 = capture.write_live_company_fixture({"b": 2, "a": 1}, output)
    assert output.read_text(encoding="utf-8") == '{"a":1,"b":2}'
    with pytest.raises(FileExistsError):
        capture.write_live_company_fixture({"a": 1}, output)
    digest2 = capture.write_live_company_fixture({"a": 1}, output, overwrite=True)
    assert digest1 != digest2


def test_source_lineage_json_rejects_duplicate_refs(tmp_path):
    path = tmp_path / "lineage.json"
    path.write_text(
        '[{"source_ref":"https://example.com/doc","document_hash":"' + "a" * 64 + '","first_seen_at":"2026-08-25T10:00:00+00:00"},'
        '{"source_ref":"https://example.com/doc","document_hash":"' + "b" * 64 + '","first_seen_at":"2026-08-25T11:00:00+00:00"}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate source_ref"):
        capture.load_source_document_lineage(path)
