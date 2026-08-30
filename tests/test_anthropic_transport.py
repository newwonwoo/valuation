"""The live staff transport: deployment glue, tested offline.

The transport is the only piece of the LLM staff outside the engine, so the
tests pin its contract without any network or vendor SDK: the builder reads
its credential from the environment only (and never leaks it), the request is
a well-formed Messages call, and every failure mode — HTTP error, malformed
payload, empty text — fails loudly as a TransportError instead of returning
something a staff seat might mistake for a proposal.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from anthropic_transport import AnthropicMessagesTransport, build  # noqa: E402
from valuation_engine.llm_transport import ProposalTransport, TransportError  # noqa: E402


def _ok_body(text: str) -> bytes:
    return json.dumps(
        {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    ).encode()


def _transport(status=200, body=None, **kwargs):
    seen: dict = {}

    def post(url, payload, headers):
        seen.update(url=url, payload=json.loads(payload), headers=headers)
        return status, body if body is not None else _ok_body("{\"ok\": true}")

    return AnthropicMessagesTransport(api_key="k-secret", post=post, **kwargs), seen


def test_the_transport_satisfies_the_engine_protocol():
    transport, _ = _transport()
    assert isinstance(transport, ProposalTransport)


def test_a_completion_is_a_well_formed_messages_call():
    transport, seen = _transport(model="claude-sonnet-5")
    text = transport.complete(role="bridge_analyst", prompt="PROMPT")
    assert text == '{"ok": true}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "k-secret"
    assert seen["headers"]["anthropic-version"]
    assert seen["payload"]["model"] == "claude-sonnet-5"
    assert seen["payload"]["temperature"] == 0
    assert seen["payload"]["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert seen["payload"]["metadata"]["user_id"] == "prism-staff:bridge_analyst"
    # The seat contract rides along as the system prompt.
    assert "proposal" in seen["payload"]["system"]
    assert transport.calls == [("bridge_analyst", len("PROMPT"))]


def test_an_http_error_is_a_loud_transport_error_without_the_key():
    transport, _ = _transport(status=529, body=b'{"error": "overloaded"}')
    with pytest.raises(TransportError, match="HTTP 529") as excinfo:
        transport.complete(role="red_team_officer", prompt="x")
    assert "k-secret" not in str(excinfo.value)


def test_a_malformed_payload_and_empty_text_both_refuse():
    malformed, _ = _transport(body=b"not json")
    with pytest.raises(TransportError, match="not a Messages payload"):
        malformed.complete(role="bridge_analyst", prompt="x")
    empty, _ = _transport(body=_ok_body("   "))
    with pytest.raises(TransportError, match="no text"):
        empty.complete(role="bridge_analyst", prompt="x")


def test_the_builder_reads_the_environment_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(TransportError, match="ANTHROPIC_API_KEY"):
        build()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-env")
    monkeypatch.setenv("VALUATION_LLM_MODEL", "claude-fable-5")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com/")
    transport = build()
    assert transport.api_key == "k-env"
    assert transport.model == "claude-fable-5"
    assert transport.base_url == "https://proxy.example.com/"


def test_the_runbook_staff_transport_delegates_only_fileless_roles(
    tmp_path, monkeypatch
):
    """Hybrid seats: a declared file always wins; a missing file goes to the
    live transport only when VALUATION_LLM_TRANSPORT is configured."""
    from run_kr_live import _StaffTransport, RunbookError

    staff = tmp_path / "staff"
    staff.mkdir()
    (staff / "bridge_analyst.json").write_text('{"drafts": []}', encoding="utf-8")

    monkeypatch.delenv("VALUATION_LLM_TRANSPORT", raising=False)
    transport = _StaffTransport(staff)
    assert json.loads(transport.complete(role="bridge_analyst", prompt="p"))
    with pytest.raises(RunbookError, match="intelligence_officer"):
        transport.complete(role="intelligence_officer", prompt="p")

    # Point the env contract at the scripted stand-in used by the CLI tests.
    monkeypatch.setenv(
        "VALUATION_LLM_TRANSPORT", "tests.test_anthropic_transport:build_scripted"
    )
    hybrid = _StaffTransport(staff)
    assert hybrid.complete(role="bridge_analyst", prompt="p") == '{"drafts": []}'
    assert hybrid.complete(role="intelligence_officer", prompt="p") == "LIVE-ANSWER"


def build_scripted():
    from valuation_engine.llm_transport import ScriptedTransport

    return ScriptedTransport({"intelligence_officer": ("LIVE-ANSWER",) * 8})
