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
<TR><TD>수주총액</TD><TD>620,000 백만원</TD></TR>
<TR><TD>수주잔고</TD><TD>1,080,000 백만원</TD></TR>
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
        "utilization", "realized_price",
    }


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
