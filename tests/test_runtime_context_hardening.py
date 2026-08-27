from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import StageExecutionResult, run_controlled_workflow
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def _run(adapter, *, initial_data=None):
    return run_controlled_workflow(
        run_id="R-HARDEN",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("TEST_STAGE",),
        adapters={"TEST_STAGE": adapter},
        required_stages=("TEST_STAGE",),
        initial_data=initial_data,
    )


def _evidence(evidence_id="E1"):
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="revenue",
        value=1,
        unit="KRW",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-25",
        source_name="filing",
        source_ref="fixture://filing",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def test_stage_cannot_assign_top_level_context_data():
    def adapter(context):
        context.data["injected"] = 1
        return StageExecutionResult(StageStatus.PASS, "should not complete")

    result = _run(adapter, initial_data={"original": 1})
    assert result.blocked_reasons
    assert "MappingProxyType" not in result.blocked_reasons[0]
    assert "injected" not in result.data
    assert result.data == {"original": 1}


def test_nested_builtin_mutation_is_isolated_and_reported():
    def adapter(context):
        context.data["bag"]["count"] = 99
        return StageExecutionResult(StageStatus.PASS, "mutated")

    result = _run(adapter, initial_data={"bag": {"count": 1}})
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "upstream_data=bag" in result.stage_traces[-1].rationale
    assert result.data["bag"] == {"count": 1}


def test_evidence_ledger_is_sealed_during_downstream_stage():
    ledger = EvidenceLedger((_evidence(),))

    def adapter(context):
        context.data["evidence_ledger"].append(_evidence("E2"))
        return StageExecutionResult(StageStatus.PASS, "mutated")

    result = _run(adapter, initial_data={"evidence_ledger": ledger})
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "read-only during downstream stage execution" in result.stage_traces[-1].rationale
    assert tuple(item.id for item in ledger.records()) == ("E1",)
    assert not ledger.runtime_readonly


def test_nested_evidence_ledger_is_recursively_sealed_without_corrupting_caller_state():
    ledger = EvidenceLedger((_evidence(),))

    def adapter(context):
        context.data["bundle"]["items"][0]["ledger"].append(_evidence("E2"))
        return StageExecutionResult(StageStatus.PASS, "mutated")

    result = _run(
        adapter,
        initial_data={"bundle": {"items": [{"ledger": ledger}]}},
    )
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "read-only during downstream stage execution" in result.stage_traces[-1].rationale
    assert tuple(item.id for item in ledger.records()) == ("E1",)
    assert not ledger.runtime_readonly


def test_stage_control_field_mutation_is_discarded_and_blocked():
    def adapter(context):
        context.stage_traces.append("tampered")
        context.freeze_token = "forged"
        return StageExecutionResult(StageStatus.PASS, "mutated")

    result = _run(adapter)
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "control=stage_traces,freeze_token" in result.stage_traces[-1].rationale
    assert result.freeze_token is None


def test_adapter_exception_secrets_are_redacted_from_trace_and_blocker():
    def adapter(_):
        raise RuntimeError(
            "request failed api_key=SUPERSECRET Authorization: Bearer abc.def password=hunter2"
        )

    result = _run(adapter)
    rendered = " | ".join(
        [*result.blocked_reasons, *(trace.rationale for trace in result.stage_traces)]
    )
    assert "SUPERSECRET" not in rendered
    assert "abc.def" not in rendered
    assert "hunter2" not in rendered
    assert "[REDACTED]" in rendered


def test_basic_authorization_and_quoted_mapping_credentials_are_fully_redacted():
    def adapter(_):
        raise RuntimeError(
            "Authorization: Basic dXNlcjpwYXNz {'api_key': 'SUPERSECRET', 'secret': 'SECOND'}"
        )

    result = _run(adapter)
    rendered = " | ".join(
        [*result.blocked_reasons, *(trace.rationale for trace in result.stage_traces)]
    )
    assert "dXNlcjpwYXNz" not in rendered
    assert "SUPERSECRET" not in rendered
    assert "SECOND" not in rendered
    assert rendered.count("[REDACTED]") >= 3


def test_quoted_authorization_key_is_redacted_for_supported_schemes():
    markers = (
        ("Basic", "BASIC_VISIBLE_MARKER"),
        ("Bearer", "BEARER_VISIBLE_MARKER"),
        ("Token", "TOKEN_VISIBLE_MARKER"),
        ("Digest", "DIGEST_VISIBLE_MARKER"),
        ("ApiKey", "APIKEY_VISIBLE_MARKER"),
    )

    def adapter(_):
        rendered_headers = " ".join(
            f'{{"Authorization": "{scheme} {marker}"}}'
            for scheme, marker in markers
        )
        raise RuntimeError(f"request failed headers={rendered_headers}")

    result = _run(adapter)
    rendered = " | ".join(
        [*result.blocked_reasons, *(trace.rationale for trace in result.stage_traces)]
    )
    for _, marker in markers:
        assert marker not in rendered
    assert rendered.count("[REDACTED]") >= len(markers)


def test_wrong_adapter_return_type_blocks_cleanly():
    result = _run(lambda _: {"status": "pass"})
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "expected StageExecutionResult" in result.stage_traces[-1].rationale
