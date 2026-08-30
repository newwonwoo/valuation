"""The chat last-mile: launch a run, hand back the engine's artifact verbatim.

A conversational front end ("ㅇㅇ 분석해줘") turns a natural-language request
into ``분석시작 <회사>`` and runs the canonical pipeline. The danger of that
last mile is the same one the whole engine spent 33 stages containing: the
conversational LLM re-stating the numbers and, by the mechanisms in
``docs/LLM_CONTAINMENT_THREAT_MODEL.md``, mis-transcribing one.

This module makes the rule "never paraphrase the numbers" *enforceable* rather
than merely documented. A dispatch returns a :class:`ReportHandoff` carrying the
engine's report text and its SHA-256 fingerprint. A chat layer that presents the
report must present it verbatim; ``verify_report_presentation`` re-fingerprints
whatever the chat layer is about to send and refuses any drift — a single
altered digit changes the hash. The conversational model may add its own framing
*around* the fenced artifact, but the artifact itself is byte-checked.

The dispatcher holds no authority: it does not choose the number, it does not
read the filing. It parses the company out of a request, runs the engine, and
returns the sealed artifact. Everything that decides value happened inside the
attested run.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from .cli_runtime import (
    LiveCLIError,
    LiveRuntimeConfigFactory,
    render_controlled_run,
)
from .orchestrator import ControlledRunResult
from .strict_cli_runtime import execute_live_analysis


_ANALYSIS_PREFIX = "분석시작"

#: Verbs a request may wrap the company in. Order-independent; the company is
#: whatever remains once these and the command word are stripped.
_REQUEST_NOISE = (
    "분석해줘", "분석 해줘", "분석해", "밸류에이션", "밸류에이션해줘",
    "적정주가", "적정가", "평가해줘", "봐줘", "좀", "해줘", "분석",
    "종목", "주식", "가치평가", "가치 평가",
)

#: A 6-digit KRX code is the least ambiguous target and is preferred when present.
_TICKER = re.compile(r"(?<!\d)(\d{6})(?!\d)")


class ChatDispatchError(ValueError):
    """Raised when a chat request cannot be turned into a run command."""


def extract_company(request: str) -> str:
    """Pull the company name or 6-digit code out of a free-form request.

    A 6-digit ticker anywhere wins. Otherwise the request is stripped of the
    known request verbs and the residue is the company. An empty residue is an
    error — the dispatcher never guesses a company.
    """
    text = str(request or "").strip()
    if not text:
        raise ChatDispatchError("빈 요청입니다")
    ticker = _TICKER.search(text)
    if ticker is not None:
        return ticker.group(1)
    residue = text
    if residue.startswith(_ANALYSIS_PREFIX):
        residue = residue[len(_ANALYSIS_PREFIX):]
    for noise in sorted(_REQUEST_NOISE, key=len, reverse=True):
        residue = residue.replace(noise, " ")
    residue = " ".join(residue.split()).strip(" .,!?~")
    if not residue:
        raise ChatDispatchError(
            f"요청에서 종목을 찾지 못했습니다: {request!r}"
        )
    return residue


def to_analysis_command(request: str) -> str:
    """Natural-language request → the canonical ``분석시작 <회사>`` command."""
    return f"{_ANALYSIS_PREFIX} {extract_company(request)}"


@dataclass(frozen=True)
class ReportHandoff:
    """The sealed artifact a chat layer must present without alteration."""

    command: str
    company: str
    blocked: bool
    report_text: str
    report_sha256: str

    def presentation_block(self) -> str:
        """The exact text a chat layer should fence and present verbatim."""
        return self.report_text

    def fenced(self) -> str:
        """Report wrapped in a fenced block with its fingerprint, for chat UIs."""
        return (
            f"```\n{self.report_text}```\n"
            f"<!-- report_sha256={self.report_sha256} -->"
        )


def _fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def dispatch_analysis(
    request: str,
    *,
    state_root: str | Path,
    provider_factory: LiveRuntimeConfigFactory,
    run_id: str | None = None,
    jurisdiction: str | None = None,
) -> ReportHandoff:
    """Run the canonical pipeline for a chat request; seal the artifact.

    The engine's own ``render_controlled_run`` output is the artifact — the same
    text the CLI prints, blocked runs included (a blocked run hands back its
    block codes, never a number). Its SHA-256 is the seal the chat layer is held
    to.
    """
    command = to_analysis_command(request)
    company = extract_company(request)
    result = execute_live_analysis(
        command,
        state_root=state_root,
        provider_factory=provider_factory,
        run_id=run_id,
        jurisdiction=jurisdiction,
    )
    if not isinstance(result, ControlledRunResult):
        raise ChatDispatchError("engine did not return a ControlledRunResult")
    report_text = render_controlled_run(result)
    return ReportHandoff(
        command=command,
        company=company,
        blocked=bool(result.blocked_reasons),
        report_text=report_text,
        report_sha256=_fingerprint(report_text),
    )


def verify_report_presentation(handoff: ReportHandoff, presented_text: str) -> None:
    """Refuse a chat presentation that altered the sealed artifact.

    ``presented_text`` is what the chat layer is about to send for the report
    body. It must equal the sealed report byte for byte — the conversational
    model may frame the artifact but may not re-transcribe a single character of
    it. A mismatch means a number could have changed; that is a hard error.
    """
    if _fingerprint(presented_text) != handoff.report_sha256:
        raise ChatDispatchError(
            "chat presentation does not match the sealed engine report "
            "(the artifact must be presented verbatim; numbers are never "
            "paraphrased). Present handoff.presentation_block() unchanged."
        )
