"""The chat last mile: verbatim handoff, enforceable by fingerprint."""

from __future__ import annotations

import tempfile

import pytest

from valuation_engine.chat_dispatch import (
    ChatDispatchError,
    ReportHandoff,
    dispatch_analysis,
    extract_company,
    to_analysis_command,
    verify_report_presentation,
)
from valuation_engine.cold_start_probe import (
    PROBE_COMPANY_NAME,
    _staff_scripts,
    probe_network,
    probe_runtime_spec,
)
from valuation_engine.generic_live_providers import build_generic_kr_runtime_factory
from valuation_engine.llm_transport import ScriptedTransport


# ------------------------------------------------------------ request parsing


@pytest.mark.parametrize(
    "company_request, expected",
    [
        ("삼성전자 분석해줘", "삼성전자"),
        ("000660 적정주가 봐줘", "000660"),
        ("한빛제강 밸류에이션해줘", "한빛제강"),
        ("SK하이닉스좀 분석", "SK하이닉스"),
        ("분석시작 현대차", "현대차"),
        ("종목 005930 가치평가", "005930"),
    ],
)
def test_extract_company(company_request, expected):
    assert extract_company(company_request) == expected


def test_to_analysis_command():
    assert to_analysis_command("삼성전자 분석해줘") == "분석시작 삼성전자"


def test_an_empty_request_is_refused():
    with pytest.raises(ChatDispatchError):
        extract_company("분석해줘 좀")


# --------------------------------------------------------------- real dispatch


def _factory():
    def factory(request):
        return build_generic_kr_runtime_factory(
            network=probe_network(),
            transport=ScriptedTransport(_staff_scripts()),
            spec=probe_runtime_spec(),
        )(request)

    return factory


def _handoff() -> ReportHandoff:
    with tempfile.TemporaryDirectory() as root:
        return dispatch_analysis(
            f"{PROBE_COMPANY_NAME} 분석해줘",
            state_root=root,
            provider_factory=_factory(),
            run_id="CHAT-DISPATCH-TEST",
            jurisdiction="KR",
        )


def test_dispatch_returns_the_sealed_engine_report():
    handoff = _handoff()
    assert handoff.command == f"분석시작 {PROBE_COMPANY_NAME}"
    assert handoff.company == PROBE_COMPANY_NAME
    assert not handoff.blocked
    assert handoff.report_text.strip()
    assert len(handoff.report_sha256) == 64
    # The value the run produced is present in the artifact.
    assert "41,789" in handoff.report_text or "41789" in handoff.report_text


def test_verbatim_presentation_passes():
    handoff = _handoff()
    verify_report_presentation(handoff, handoff.presentation_block())


def test_a_single_altered_digit_is_caught():
    handoff = _handoff()
    tampered = handoff.report_text.replace("41,789", "51,789", 1)
    if tampered == handoff.report_text:  # formatting without comma
        tampered = handoff.report_text.replace("41789", "51789", 1)
    assert tampered != handoff.report_text
    with pytest.raises(ChatDispatchError, match="verbatim"):
        verify_report_presentation(handoff, tampered)


def test_added_framing_around_the_body_is_also_a_mismatch():
    """The body itself is byte-checked; a chat layer frames AROUND the fenced
    block, it does not edit inside it."""
    handoff = _handoff()
    with pytest.raises(ChatDispatchError):
        verify_report_presentation(
            handoff, "요약: 좋은 회사입니다.\n" + handoff.report_text
        )


def test_the_fenced_form_carries_the_fingerprint():
    handoff = _handoff()
    fenced = handoff.fenced()
    assert handoff.report_sha256 in fenced
    assert handoff.report_text.rstrip() in fenced
