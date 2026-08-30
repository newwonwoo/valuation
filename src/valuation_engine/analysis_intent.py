from __future__ import annotations

import re


_CANONICAL_PREFIX = "분석시작"
_TRAILING_PUNCTUATION = "?!.,。！？"
_INTENT_ONLY = frozenset(
    {
        "분석시작",
        "분석 시작",
        "분석 시작해",
        "분석",
        "분석해",
        "분석해줘",
        "분석해 줘",
        "분석좀해줘",
        "분석 좀 해줘",
        "밸류에이션",
        "밸류에이션해",
        "밸류에이션좀해줘",
        "가치평가",
        "가치 평가",
        "가치평가해",
        "가치평가좀해줘",
        "적정주가",
        "적정가치",
        "프리즘",
        "돌려",
        "돌려줘",
        "돌려봐",
        "돌려 봐",
    }
)
_PREFIX_PATTERNS = (
    re.compile(r"^분석\s*시작\s+(?P<company>.+)$"),
    re.compile(r"^(?:프리즘(?:으로)?|밸류에이션|가치\s*평가)\s+(?P<company>.+)$"),
)
_SUFFIX_PATTERNS = (
    re.compile(r"^(?P<company>.+?)\s*프리즘(?:으로)?(?:\s*좀\s*)?(?:\s*(?:분석(?:\s*좀\s*)?(?:해\s*줘)?|시작(?:해\s*줘|해)?|돌려\s*(?:봐|줘)))?$"),
    re.compile(r"^(?P<company>.+?)\s*분석\s*시작(?:해\s*줘|해)?$"),
    re.compile(r"^(?P<company>.+?)\s*(?:좀\s*)?(?:밸류에이션|가치\s*평가)(?:\s*좀\s*)?(?:\s*(?:해\s*줘|해\s*봐|해|시작(?:해\s*줘|해)?))?$"),
    re.compile(r"^(?P<company>.+?)\s*(?:적정주가|적정가치)(?:\s*(?:구해\s*줘|봐\s*줘|분석))?$"),
    re.compile(r"^(?P<company>.+?)\s*(?:한번\s*|한\s*번\s*)?(?:좀\s*)?돌려(?:\s*(?:봐|줘))?$"),
    re.compile(r"^(?P<company>.+?)\s*(?:좀\s*)?분석(?:\s*좀\s*)?(?:해\s*줘|해\s*봐|해)?$"),
)


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().rstrip(_TRAILING_PUNCTUATION).split())


def _normalize_company(value: str) -> str:
    company = _normalize_text(value)
    if len(company) > 1 and company[-1:] in {"을", "를"}:
        company = company[:-1].rstrip()
    return company


def canonicalize_analysis_command(command: str) -> str | None:
    """Return canonical ``분석시작 <기업>`` for explicit valuation intent.

    This gateway is deliberately deterministic. It recognizes a bounded set of
    stock-analysis phrasings, including compact Korean input without spaces, but
    it never decides whether the extracted text is actually a listed company;
    canonical COMPANY_RESOLUTION owns that decision. Non-analysis input returns
    ``None`` so YAML/regression entrypoints remain unaffected. Intent without a
    company canonicalizes to ``분석시작`` so the live parser can fail closed with
    COMPANY_REQUIRED.
    """

    text = _normalize_text(command)
    if not text:
        return None
    if text in _INTENT_ONLY:
        return _CANONICAL_PREFIX

    for pattern in _PREFIX_PATTERNS + _SUFFIX_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        company = _normalize_company(match.group("company"))
        if not company:
            return _CANONICAL_PREFIX
        return f"{_CANONICAL_PREFIX} {company}"
    return None


def is_analysis_intent(command: str) -> bool:
    return canonicalize_analysis_command(command) is not None
