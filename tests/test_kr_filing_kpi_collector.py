"""The filing-KPI collector: dart_kpi's island gets an entrance.

The synthetic filing below is format-realistic (수주상황/생산설비 tables in the
statutory layout) for a company that exists nowhere in this repository. Every
extracted number must carry the full receipt — rcept_no, member SHA-256,
normalized-text span — and every miss must surface as a named coverage gap,
never a guess.
"""

from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.kr_filing_kpi_collector import (
    COLLECTOR_ID,
    FilingKPICollectorError,
    filing_kpi_collector_provider,
    load_filing_kpi_patterns,
    request_scoped_filing_kpi_collector,
)
from valuation_engine.kr_opendart_provider import OpenDartNetwork
from valuation_engine.llm_transport import ScriptedTransport, TransportError
from valuation_engine.proposal_parsing import ProposalParseError


AS_OF = "2026-08-27"
CORP = "00999902"
TARGET = f"KR:DART:{CORP}"
RCEPT = "20260318000888"

FILING_BODY = """
<BODY>
<P>II. 사업의 내용</P>
<P>4. 수주상황 (단위 표기는 각 행에 기재)</P>
<TABLE>
<TR><TD>구분</TD><TD>금액</TD></TR>
<TR><TD>당기 수주총액</TD><TD>620,000 백만원</TD></TR>
<TR><TD>당기말 수주잔고</TD><TD>1,080,000 백만원</TD></TR>
</TABLE>
<P>3. 생산 및 설비</P>
<TABLE>
<TR><TD>생산능력</TD><TD>5,400,000 백만원</TD></TR>
<TR><TD>생산실적</TD><TD>4,860,000 백만원</TD></TR>
<TR><TD>평균가동률</TD><TD>90.0 %</TD></TR>
</TABLE>
</BODY>
"""


def _document_zip(body: str = FILING_BODY) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"{RCEPT}.xml", body)
    return buffer.getvalue()


FILING_ROWS = [
    {"rcept_no": RCEPT, "report_nm": "사업보고서 (2025.12)",
     "rcept_dt": "20260318", "corp_code": CORP, "stock_code": "900991"},
]


def _network(body: str = FILING_BODY, rows=None) -> OpenDartNetwork:
    def fetch_text(url: str) -> str:
        assert "list.json" in url
        return json.dumps({"status": "000", "list": rows or FILING_ROWS})

    def fetch_bytes(url: str) -> bytes:
        assert "document.xml" in url and RCEPT in url
        return _document_zip(body)

    return OpenDartNetwork(fetch_text=fetch_text, fetch_bytes=fetch_bytes, api_key="K")


def _collect(metrics: tuple[str, ...], body: str = FILING_BODY):
    collector = request_scoped_filing_kpi_collector(
        _network(body),
        as_of=AS_OF,
        segment_id="core",
        patterns=load_filing_kpi_patterns(),
    )
    return collector(EvidenceCollectionRequest(target_id=TARGET, required_metrics=metrics))


def test_operating_kpis_extract_from_the_statutory_tables():
    batch = _collect(("orders", "backlog", "production", "utilization"))
    by_metric = {record.metric: record for record in batch.records}
    assert set(by_metric) == {"orders", "backlog", "production", "utilization"}
    assert float(by_metric["backlog"].value) == 1080000.0
    assert by_metric["backlog"].unit == "KRW_million"
    assert float(by_metric["utilization"].value) == pytest.approx(0.9)
    assert by_metric["utilization"].unit == "ratio"
    # Fiscal period from the report title, not from the receipt date.
    assert by_metric["orders"].effective_date == "2025-12-31"


def test_every_record_carries_the_full_extraction_receipt():
    batch = _collect(("backlog",))
    record = batch.records[0]
    assert RCEPT in record.id
    assert "member_sha256=" in record.source_ref
    assert "normalized_span=" in record.source_ref
    assert "수주잔고" in record.notes or "backlog" in record.notes
    assert batch.source_fingerprint  # filing manifest hash
    assert batch.document_ids == (f"DART_{RCEPT}",)


def test_an_undisclosed_metric_is_a_named_gap_not_a_guess():
    body = FILING_BODY.replace("수주잔고", "별도표기없음")
    batch = _collect(("orders", "backlog"), body=body)
    metrics = {record.metric for record in batch.records}
    assert metrics == {"orders"}  # backlog omitted; coverage names it downstream


def test_an_ambiguous_disclosure_fails_closed_into_a_gap():
    body = FILING_BODY + "\n<P>수주잔고 999,999 백만원</P>"
    batch = _collect(("backlog",), body=body)
    assert not batch.records  # two matches -> neither is chosen


def test_a_prior_period_static_match_is_not_stamped_as_current():
    body = FILING_BODY.replace(
        "당기말 수주잔고", "전기말 수주잔고"
    )
    batch = _collect(("backlog",), body=body)
    assert not batch.records


def test_an_unmarked_static_match_is_not_assumed_to_be_current():
    body = FILING_BODY.replace("당기말 수주잔고", "수주잔고")
    batch = _collect(("backlog",), body=body)
    assert not batch.records


def test_a_metric_outside_declared_capability_is_refused():
    with pytest.raises(FilingKPICollectorError, match="outside its declared"):
        _collect(("orders", "free_cash_flow"))


def test_no_periodic_filing_is_an_error_not_an_empty_batch():
    rows = [{"rcept_no": "20260701000303", "report_nm": "주요사항보고서",
             "rcept_dt": "20260701", "corp_code": CORP, "stock_code": "900991"}]
    collector = request_scoped_filing_kpi_collector(
        _network(rows=rows),
        as_of=AS_OF,
        segment_id="core",
        patterns=load_filing_kpi_patterns(),
    )
    with pytest.raises(FilingKPICollectorError, match="no periodic DART filing"):
        collector(EvidenceCollectionRequest(target_id=TARGET, required_metrics=("orders",)))


def test_the_provider_declares_exactly_the_configured_metrics():
    provider = filing_kpi_collector_provider(
        _network(), as_of=AS_OF, segment_id="core"
    )
    provider.validate()
    assert provider.capability.collector_id == COLLECTOR_ID
    assert set(provider.capability.supported_metrics) == {
        "orders", "backlog", "nameplate_capacity", "capacity", "production",
        "utilization", "realized_price", "contract_liabilities", "lead_time",
    }


@pytest.mark.parametrize("transport,receipts,available", [
    (None, None, False),
    (ScriptedTransport({}), None, False),
    (ScriptedTransport({"filing_table_reader": ()}), None, False),
    (ScriptedTransport({"filing_table_reader": ("answer",)}), None, True),
    (None, {"input_price": "receipt-to-be-validated"}, True),
])
def test_table_only_capability_requires_reader_or_receipt(transport, receipts, available):
    provider = filing_kpi_collector_provider(
        _network(), as_of=AS_OF, segment_id="core", transport=transport,
        table_cell_receipts=receipts,
    )
    assert ("input_price" in provider.capability.supported_metrics) == available
    assert "utilization" in provider.capability.supported_metrics


def test_configured_reader_exhaustion_is_not_a_capability_downgrade():
    transport = ScriptedTransport({"filing_table_reader": ("answer",)})
    transport.complete(role="filing_table_reader", prompt="")
    provider = filing_kpi_collector_provider(
        _network(), as_of=AS_OF, segment_id="core", transport=transport,
    )
    assert "input_price" in provider.capability.supported_metrics
    with pytest.raises(FilingKPICollectorError, match="READER_UNAVAILABLE"):
        provider.collector(EvidenceCollectionRequest(
            target_id=TARGET, required_metrics=("input_price",),
        ))


def test_pattern_config_rejects_a_bad_regex_at_load_time(tmp_path):
    bad = tmp_path / "patterns.yaml"
    bad.write_text(
        "patterns:\n  orders:\n    locator_label: x\n"
        "    value_pattern: '수주총액 (?P<value>[0-9]+)'\n"  # missing unit group
        "    canonical_unit: KRW_million\n"
        "    source_unit_map: {백만원: KRW_million}\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="unit"):
        load_filing_kpi_patterns(bad)


# --- The third pass: a coordinate reading, from the production collector -----
#
# The two tests below are the wiring's only honest proof. A reader that exists
# but is never reached from `request_scoped_filing_kpi_collector` collects
# nothing, however well it reads in its own unit tests.

# 제품별 가격변동추이: the heading and the unit sit *outside* the <TABLE>, which
# is how issuers actually write it, and why the static pattern — which needs
# "판매단가 … 원/톤" adjacent in one span — cannot read it.
PRICE_TABLE_BODY = FILING_BODY.replace(
    "</BODY>",
    """
<P>다. 제품별 구체적인 가격변동추이</P>
<P>(단위: 원/톤)</P>
<TABLE>
<TR><TD>품목</TD><TD>2024년</TD><TD>당기</TD></TR>
<TR><TD>후판</TD><TD>1,010,000</TD><TD>1,046,000</TD></TR>
</TABLE>
</BODY>""",
)

MEMBER_PATH = f"{RCEPT}.xml"

TABLE_CELL_ANSWER = json.dumps(
    {
        "cells": [
            {
                "metric": "realized_price",
                "member_path": MEMBER_PATH,
                "table_index": 2,
                "row_path": ["후판"],
                "column_path": ["당기"],
                "unit_token": "원/톤",
                # The proposal names where the filing writes the unit, per
                # docs/LLM_READING_HANDOFF_DESIGN.md §3.2 — here a one-line
                # paragraph above the table, which is how issuers write it.
                "unit_source": {"quote": "(단위: 원/톤)"},
                # …and where it states the period, per the same section.
                "period_source": {"cell": [2, 0, 2]},
            }
        ]
    }
)

NO_LOCATOR_ANSWER = json.dumps({"locators": [], "not_found": ["realized_price"]})


class _Transport:
    """Answers the locator seat with nothing and the reader seat with a cell."""

    def __init__(self, *, table_answer: str = TABLE_CELL_ANSWER):
        self.roles: list[str] = []
        self._table_answer = table_answer

    def complete(self, *, role: str, prompt: str) -> str:
        self.roles.append(role)
        if role == "filing_locator_analyst":
            return NO_LOCATOR_ANSWER
        if role == "filing_table_reader":
            return self._table_answer
        raise AssertionError(f"unscripted role: {role}")


def _collect_with(transport, metrics=("realized_price",), body=PRICE_TABLE_BODY, receipts=None):
    collector = request_scoped_filing_kpi_collector(
        _network(body),
        as_of=AS_OF,
        segment_id="core",
        patterns=load_filing_kpi_patterns(),
        transport=transport,
        table_cell_receipts=receipts,
    )
    return collector(
        EvidenceCollectionRequest(target_id=TARGET, required_metrics=metrics)
    )


def test_a_metric_both_earlier_passes_miss_is_read_by_coordinate():
    transport = _Transport()
    batch = _collect_with(transport)
    assert transport.roles == ["filing_locator_analyst", "filing_table_reader"]
    record = batch.records[0]
    assert record.metric == "realized_price"
    assert float(record.value) == 1046000.0
    assert record.unit == "KRW_per_ton"
    # The receipt names the cell, so a reviewer can reopen it in the filing.
    assert "member_sha256=" in record.source_ref
    assert RCEPT in record.id


def test_table_reader_only_transport_skips_unconfigured_locator():
    transport = ScriptedTransport({"filing_table_reader": (TABLE_CELL_ANSWER,)})
    record = _collect_with(transport).records[0]
    assert record.metric == "realized_price"
    assert [role for role, _ in transport.calls] == ["filing_table_reader"]


def test_locator_only_transport_does_not_invoke_unconfigured_reader():
    transport = ScriptedTransport({"filing_locator_analyst": (NO_LOCATOR_ANSWER,)})
    assert _collect_with(transport).records == ()
    assert [role for role, _ in transport.calls] == ["filing_locator_analyst"]


def test_configured_locator_failure_still_blocks_before_table_reader():
    transport = ScriptedTransport({"filing_locator_analyst": ("used",),
                                   "filing_table_reader": (TABLE_CELL_ANSWER,)})
    transport.complete(role="filing_locator_analyst", prompt="")
    with pytest.raises(TransportError):
        _collect_with(transport)
    assert all(role == "filing_locator_analyst" for role, _ in transport.calls)


def test_table_only_input_price_is_collectable_and_replayable():
    answer = json.loads(TABLE_CELL_ANSWER)
    answer["cells"][0]["metric"] = "input_price"
    body = PRICE_TABLE_BODY.replace("제품별 구체적인 가격변동추이", "원재료 매입단가")
    transport = _Transport(table_answer=json.dumps(answer))
    record = _collect_with(transport, metrics=("input_price",), body=body).records[0]
    assert transport.roles == ["filing_table_reader"]
    assert record.metric == "input_price"
    assert float(record.value) == 1046000.0
    receipt = record.notes.split("; table_cell_receipt=", 1)[1]
    replay = _collect_with(None, metrics=("input_price",), body=body,
                           receipts={"input_price": receipt}).records[0]
    assert replay == record


@pytest.mark.parametrize("cell,caption", [
    ("85.3", "가동률 (단위: %)"),
    ("85.3%", "가동률 (단위: %)"),
    ("85.3 %", "가동률 (단위: %)"),
    ("85.3%", "가동률"),
])
def test_table_utilization_has_the_same_ratio_contract_as_static(cell, caption):
    body = f"""<BODY><P>{caption}</P><TABLE>
    <TR><TD>사업장</TD><TD>당기</TD></TR>
    <TR><TD>제1공장</TD><TD>{cell}</TD></TR></TABLE></BODY>"""
    answer = {"cells": [{"metric": "utilization", "member_path": MEMBER_PATH,
                         "table_index": 0, "row_path": ["제1공장"],
                         "column_path": ["당기"], "unit_token": "%",
                         "unit_source": ({"quote": caption} if "단위" in caption
                                         else {"cell": [0, 1, 1]}),
                         "period_source": {"cell": [0, 0, 1]}}]}
    class RatioTransport(_Transport):
        def complete(self, *, role, prompt):
            if role == "filing_locator_analyst":
                self.roles.append(role)
                return json.dumps({"locators": [], "not_found": ["utilization"]})
            return super().complete(role=role, prompt=prompt)
    transport = RatioTransport(table_answer=json.dumps(answer))
    record = _collect_with(transport, metrics=("utilization",), body=body).records[0]
    assert record.unit == "ratio"
    assert float(record.value) == pytest.approx(0.853)
    receipt = record.notes.split("; table_cell_receipt=", 1)[1]
    replay = _collect_with(None, metrics=("utilization",), body=body,
                           receipts={"utilization": receipt}).records[0]
    assert replay == record


def test_a_metric_an_earlier_pass_already_found_is_not_asked_about_again():
    """The coordinate pass is a last resort, not a second opinion."""
    transport = _Transport()
    batch = _collect_with(transport, metrics=("orders", "backlog"))
    assert transport.roles == []  # nothing was missed; no seat was asked
    assert {record.metric for record in batch.records} == {"orders", "backlog"}


def test_a_refused_coordinate_blocks_instead_of_becoming_absence():
    """Rejected prior-year coordinates do not establish non-disclosure."""
    prior = json.loads(TABLE_CELL_ANSWER)
    prior["cells"][0]["column_path"] = ["2024년"]
    # The period is read where the proposal says the filing states it, so a
    # prior-year reading has to name the prior-year header — and that is what
    # the chronology contract refuses.
    prior["cells"][0]["period_source"] = {"cell": [2, 0, 1]}
    with pytest.raises(ProposalParseError, match="PROPOSAL_REJECTED"):
        _collect_with(_Transport(table_answer=json.dumps(prior)))


@pytest.mark.parametrize("failure", [
    TransportError("no staff file"),
    TimeoutError("reader timeout"),
    ConnectionError("reader disconnected"),
])
def test_reader_unavailability_blocks_instead_of_returning_partial_evidence(failure):

    class _LocatorOnly:
        def complete(self, *, role: str, prompt: str) -> str:
            if role == "filing_locator_analyst":
                return NO_LOCATOR_ANSWER
            raise failure

    with pytest.raises(FilingKPICollectorError, match="READER_UNAVAILABLE") as caught:
        _collect_with(_LocatorOnly(), metrics=("orders", "realized_price"))
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("failure", [
    RuntimeError("unexpected reader failure"),
    AssertionError("broken verifier invariant"),
])
def test_unexpected_table_reader_errors_are_not_swallowed(monkeypatch, failure):
    def broken_reader(**kwargs):
        raise failure

    monkeypatch.setattr(
        "valuation_engine.kr_filing_kpi_collector.propose_and_verify_table_cells",
        broken_reader,
    )
    with pytest.raises(type(failure)) as caught:
        _collect_with(_Transport(), metrics=("orders", "realized_price"))
    assert caught.value is failure


def test_explicit_reader_not_found_remains_a_coverage_gap():
    answer = json.dumps({"cells": [], "not_found": ["realized_price"]})
    batch = _collect_with(_Transport(table_answer=answer), metrics=("orders", "realized_price"))
    assert {record.metric for record in batch.records} == {"orders"}


def test_sealed_table_receipt_replays_without_any_model_call():
    first = _collect_with(_Transport()).records[0]
    receipt = first.notes.split("; table_cell_receipt=", 1)[1]
    replay = _collect_with(None, receipts={"realized_price": receipt})
    assert replay.records == (first,)


def test_changed_source_cannot_fall_back_from_receipt_to_model():
    first = _collect_with(_Transport()).records[0]
    receipt = first.notes.split("; table_cell_receipt=", 1)[1]
    transport = _Transport()
    with pytest.raises(ProposalParseError, match="EVIDENCE_RECONCILIATION_REQUIRED"):
        _collect_with(
            transport, body=PRICE_TABLE_BODY.replace("1,046,000", "1,047,000"),
            receipts={"realized_price": receipt},
        )
    assert transport.roles == []
