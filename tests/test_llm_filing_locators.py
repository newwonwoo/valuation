"""The model points, the extractor reads — and a lying model loses the round.

The filing here uses a NONSTANDARD layout the static patterns miss (backlog
disclosed as "수주잔액 합계 1조 800억... " style prose table), so every
observation in these tests exists only because the locator path worked. The
fabrication tests are the point: a value not in the document, a quote from
nowhere, a relabeled span, an invented unit — each dies in the deterministic
verifier, and the batch degrades to a named gap instead of carrying a lie.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from valuation_engine.dart_documents import parse_opendart_original_document_archive
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.kr_filing_kpi_collector import (
    FilingKPIPattern,
    load_filing_kpi_patterns,
    request_scoped_filing_kpi_collector,
)
from valuation_engine.kr_opendart_provider import OpenDartNetwork
from valuation_engine.llm_filing_locators import (
    ROLE_FILING_LOCATOR,
    propose_and_verify_filing_kpis,
)
from valuation_engine.llm_transport import ScriptedTransport
from valuation_engine.runtime_authority import RuntimeActor, current_actor


AS_OF = "2026-08-27"
RCEPT = "20260318000888"
CORP = "00999902"
TARGET = f"KR:DART:{CORP}"

# Nonstandard: the anchor and value are separated by prose the static
# 0-60 char gap pattern cannot cross, and the unit sits in a header.
BODY = """
<BODY>
<P>II. 사업의 내용</P>
<P>당사의 수주 현황은 다음과 같습니다. 보고기간말 현재 당사가 수행 중인
계약의 수주잔액은 원화 기준으로 합계 1,080,000 백만원이며, 전기말 대비
증가하였습니다.</P>
<P>참고: 전기말 수주잔액은 900,000 백만원이었습니다.</P>
</BODY>
"""


def _filing(body: str = BODY):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"{RCEPT}.xml", body)
    return parse_opendart_original_document_archive(
        buffer.getvalue(), rcept_no=RCEPT, checked_at=date(2026, 8, 27),
        source_ref="https://opendart.fss.or.kr/api/document.xml?rcept_no=" + RCEPT,
    )


def _tasks(metrics=("backlog",)):
    patterns = {p.metric: p for p in load_filing_kpi_patterns()}
    return tuple(patterns[m].locator_task() for m in metrics)


GOOD_QUOTE = "수주잔액은 원화 기준으로 합계 1,080,000 백만원"


def _proposal(quote=GOOD_QUOTE, value_text="1,080,000", unit="백만원", metric="backlog"):
    return json.dumps({
        "locators": [{
            "metric": metric,
            "member_path": f"{RCEPT}.xml",
            "quote": quote,
            "value_text": value_text,
            "unit_token": unit,
        }],
        "not_found": [],
    })


def _run(*responses):
    return propose_and_verify_filing_kpis(
        transport=ScriptedTransport({ROLE_FILING_LOCATOR: tuple(responses)}),
        filing=_filing(),
        tasks=_tasks(),
        segment="core",
        effective_date="2025-12-31",
    )


def test_a_verified_locator_extracts_through_the_same_machinery():
    observations = _run(_proposal())
    assert len(observations) == 1
    obs = observations[0]
    assert obs.metric == "backlog"
    assert str(obs.measure.amount) == "1080000"
    assert obs.measure.unit == "KRW_million"
    assert "LLM locator (verified)" in obs.locator_label
    assert obs.member_content_hash and obs.text_start >= 0  # 정적 경로와 동일한 영수증


def test_a_fabricated_value_has_no_quote_and_dies():
    fake = _proposal(
        quote="수주잔액은 원화 기준으로 합계 9,999,999 백만원",
        value_text="9,999,999",
    )
    assert _run(fake, fake) == ()


def test_a_relabeled_span_without_the_anchor_dies():
    # Real text, real number — but no backlog anchor in the quote.
    fake = _proposal(quote="합계 1,080,000 백만원", value_text="1,080,000")
    assert _run(fake, fake) == ()


def test_an_invented_unit_token_dies():
    fake = _proposal(unit="조원")
    assert _run(fake, fake) == ()


def test_a_non_unique_quote_must_be_extended():
    # "백만원" alone appears twice; a sloppy short quote is rejected.
    fake = _proposal(quote="수주잔액", value_text="수주잔액")
    assert _run(fake, fake) == ()


def test_the_repair_loop_feeds_the_error_back_then_succeeds():
    transport = ScriptedTransport(
        {ROLE_FILING_LOCATOR: (_proposal(unit="조원"), _proposal())}
    )
    observations = propose_and_verify_filing_kpis(
        transport=transport, filing=_filing(), tasks=_tasks(),
        segment="core", effective_date="2025-12-31",
    )
    assert len(observations) == 1
    assert "rejected" in transport.calls[1][1]


def test_the_analyst_runs_under_llm_proposal_scope():
    seen = {}

    class Probe:
        def complete(self, *, role, prompt):
            seen["actor"] = current_actor()
            return _proposal()

    propose_and_verify_filing_kpis(
        transport=Probe(), filing=_filing(), tasks=_tasks(),
        segment="core", effective_date="2025-12-31",
    )
    assert seen["actor"] is RuntimeActor.LLM


def test_not_found_is_an_acceptable_honest_answer():
    honest = json.dumps({"locators": [], "not_found": ["backlog"]})
    assert _run(honest) == ()


# ------------------------------------------------------------ collector 통합


def _collector_network(body: str = BODY) -> OpenDartNetwork:
    rows = [{"rcept_no": RCEPT, "report_nm": "사업보고서 (2025.12)",
             "rcept_dt": "20260318", "corp_code": CORP, "stock_code": "900991"}]

    def fetch_text(url):
        assert "list.json" in url
        return json.dumps({"status": "000", "list": rows})

    def fetch_bytes(url):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr(f"{RCEPT}.xml", body)
        return buffer.getvalue()

    return OpenDartNetwork(fetch_text=fetch_text, fetch_bytes=fetch_bytes, api_key="K")


def test_collector_falls_back_to_the_locator_path_for_static_misses():
    collector = request_scoped_filing_kpi_collector(
        _collector_network(),
        as_of=AS_OF,
        segment_id="core",
        patterns=load_filing_kpi_patterns(),
        transport=ScriptedTransport({ROLE_FILING_LOCATOR: (_proposal(),)}),
    )
    batch = collector(
        EvidenceCollectionRequest(target_id=TARGET, required_metrics=("backlog",))
    )
    record = batch.records[0]
    assert record.metric == "backlog"
    assert record.value == 1080000
    assert "LLM locator (verified)" in record.notes
    assert "member_sha256=" in record.source_ref


def test_without_a_transport_the_static_miss_stays_a_named_gap():
    collector = request_scoped_filing_kpi_collector(
        _collector_network(),
        as_of=AS_OF,
        segment_id="core",
        patterns=load_filing_kpi_patterns(),
    )
    batch = collector(
        EvidenceCollectionRequest(target_id=TARGET, required_metrics=("backlog",))
    )
    assert not batch.records


# ------------------------------------------------- 엉뚱한 열 (wrong-column) 강화
#
# A quote can satisfy the anchor, occur once, and carry no disqualifying word
# yet still be a bare, unlabelled cell in a multi-period table. The opt-in
# ``require_current_period_marker`` forces the quote to name its period.

# One table, two anchor-bearing cells: the current one labelled, the other bare.
# Neither carries a *disqualifying* word, so today both would pass the verifier.
MULTI_COLUMN_BODY = """
<BODY>
<P>II. 사업의 내용</P>
<P>수주 현황</P>
<P>당기말 수주잔고 1,080,000 백만원</P>
<P>수주잔고 900,000 백만원</P>
</BODY>
"""

CURRENT_QUOTE = "당기말 수주잔고 1,080,000 백만원"
BARE_QUOTE = "수주잔고 900,000 백만원"


def _backlog_task(*, require_current_period: bool):
    base = {p.metric: p for p in load_filing_kpi_patterns()}["backlog"].locator_task()
    return (dataclasses.replace(
        base, require_current_period_marker=require_current_period
    ),)


def _run_multi(*responses, require_current_period, effective_date="2025-12-31"):
    return propose_and_verify_filing_kpis(
        transport=ScriptedTransport({ROLE_FILING_LOCATOR: tuple(responses)}),
        filing=_filing(MULTI_COLUMN_BODY),
        tasks=_backlog_task(require_current_period=require_current_period),
        segment="core",
        effective_date=effective_date,
    )


def test_bare_cell_passes_when_the_marker_is_not_required():
    # Default (opt-out): a legitimate scalar disclosure with no period word is
    # still evidence — the guard must not turn ordinary figures into gaps.
    obs = _run_multi(
        _proposal(quote=BARE_QUOTE, value_text="900,000"),
        require_current_period=False,
    )
    assert len(obs) == 1
    assert str(obs[0].measure.amount) == "900000"


def test_bare_cell_becomes_a_gap_when_the_marker_is_required():
    # Same bare cell, flag on: the model pointed at a column that does not state
    # its period, so it cannot be assumed current. Repair also fails → gap.
    bare = _proposal(quote=BARE_QUOTE, value_text="900,000")
    assert _run_multi(bare, bare, require_current_period=True) == ()


def test_current_period_labelled_cell_passes_when_the_marker_is_required():
    obs = _run_multi(
        _proposal(quote=CURRENT_QUOTE, value_text="1,080,000"),
        require_current_period=True,
    )
    assert len(obs) == 1
    assert str(obs[0].measure.amount) == "1080000"


def test_the_fiscal_year_string_satisfies_the_marker_requirement():
    # A value cell tagged with the reporting year is anchored to the period as
    # surely as a "당기" word — no explicit affirming term needed.
    body = """
<BODY>
<P>II. 사업의 내용</P>
<P>2025년 수주잔고 1,080,000 백만원</P>
</BODY>
"""
    obs = propose_and_verify_filing_kpis(
        transport=ScriptedTransport({ROLE_FILING_LOCATOR: (
            _proposal(quote="2025년 수주잔고 1,080,000 백만원", value_text="1,080,000"),
        )}),
        filing=_filing(body),
        tasks=_backlog_task(require_current_period=True),
        segment="core",
        effective_date="2025-12-31",
    )
    assert len(obs) == 1
    assert str(obs[0].measure.amount) == "1080000"


def test_the_wrong_fiscal_year_does_not_satisfy_the_marker_requirement():
    # The year must be the fiscal period's own; a stale year in the quote is not
    # a current-period marker.
    stale = _proposal(quote="2024년 수주잔고 1,080,000 백만원", value_text="1,080,000")
    body = """
<BODY>
<P>II. 사업의 내용</P>
<P>2024년 수주잔고 1,080,000 백만원</P>
</BODY>
"""
    result = propose_and_verify_filing_kpis(
        transport=ScriptedTransport({ROLE_FILING_LOCATOR: (stale, stale)}),
        filing=_filing(body),
        tasks=_backlog_task(require_current_period=True),
        segment="core",
        effective_date="2025-12-31",
    )
    assert result == ()


def test_the_config_flag_threads_into_the_locator_task():
    # The collector-side pattern carries the opt-in and hands it to the task.
    pattern = FilingKPIPattern(
        metric="backlog",
        locator_label="수주잔고",
        value_pattern=r"수주잔고[^0-9]{0,20}(?P<value>[0-9,]+)\s*(?P<unit>백만원)",
        canonical_unit="KRW_million",
        source_unit_map=(("백만원", "KRW_million"),),
        anchor_terms=("수주잔고",),
        require_current_period=True,
    )
    assert pattern.locator_task().require_current_period_marker is True
    # Default stays off.
    assert FilingKPIPattern(
        metric="backlog",
        locator_label="수주잔고",
        value_pattern=r"수주잔고[^0-9]{0,20}(?P<value>[0-9,]+)\s*(?P<unit>백만원)",
        canonical_unit="KRW_million",
        source_unit_map=(("백만원", "KRW_million"),),
        anchor_terms=("수주잔고",),
    ).locator_task().require_current_period_marker is False
