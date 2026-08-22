from datetime import date
from urllib.parse import parse_qs, urlparse
import pytest

from valuation_engine.live_indexers import (
    MissingCredentialError,
    build_opendart_filing_list_url,
    index_iea_monthly_electricity,
    index_kiet_psi,
    index_kisdi_ict,
    index_opendart_filing_list,
    parse_json_response,
    require_env_credential,
    snapshot_kosis_json,
)


def test_kiet_indexer_uses_metadata_only_parser():
    def fetch(url):
        return "산업경기 전문가 서베이조사결과(`26년 7월 현황과 8월 전망) 2026.07.26"
    batch = index_kiet_psi(fetch, checked_at=date(2026,8,21))
    assert len(batch.records) == 1
    assert batch.records[0].published_at == date(2026,7,26)


def test_kisdi_indexer_parses_public_report_metadata():
    text = "#### ICT 산업 중장기 전망(2026~2030) 및 대응 전략\n발행일 2025-12-31"
    batch = index_kisdi_ict(lambda _: text, checked_at=date(2026,8,21))
    assert batch.records[0].published_at == date(2025,12,31)
    assert batch.records[0].document_class == "medium_term_outlook"


def test_iea_indexer_reconciles_stale_product_and_fresh_tool():
    product="""# Monthly Electricity Statistics\nLast updated July 2026\nNext release 17th August 2026\n20/07/2026 Latest\nSDMX legacy CSV discontinue after August 2026"""
    tool="""# Monthly Electricity Statistics\nLast updated 17 Aug 2026\n17/08/2026 Latest"""
    def fetch(url): return tool if "/data-tools/" in url else product
    result=index_iea_monthly_electricity(fetch, checked_at=date(2026,8,21))
    assert result.resolved_latest_published_at == date(2026,8,17)
    assert result.endpoint_warning is not None
    assert result.schema_transition_note is not None


def test_missing_api_credential_fails_closed(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(MissingCredentialError):
        require_env_credential("DART_API_KEY")


def test_opendart_url_builder_keeps_credential_runtime_only():
    url = build_opendart_filing_list_url(corp_code="00126380", begin_date="20260801", end_date="20260821", api_key="TESTKEY")
    q = parse_qs(urlparse(url).query)
    assert q["crtfc_key"] == ["TESTKEY"]
    assert q["corp_code"] == ["00126380"]


def test_opendart_indexer_normalizes_filing_metadata():
    payload='{"status":"000","list":[{"corp_code":"00126380","stock_code":"005930","report_nm":"반기보고서 (2026.06)","rcept_no":"20260814001234","rcept_dt":"20260814"}]}'
    batch=index_opendart_filing_list(lambda _:payload,checked_at=date(2026,8,21),corp_code="00126380",begin_date="20260801",end_date="20260821",api_key="TESTKEY")
    assert batch.records[0].document_id == "DART_20260814001234"
    assert batch.records[0].published_at == date(2026,8,14)


def test_kosis_snapshot_separates_fact_and_schema_hashes():
    payload='[{"PRD_DE":"202606","ITM_NM":"생산","DT":"100"},{"PRD_DE":"202607","ITM_NM":"생산","DT":"105"}]'
    snap=snapshot_kosis_json(lambda _:payload,url="https://example.test/kosis")
    assert snap.row_count == 2
    assert snap.periods == ("202606","202607")
    assert snap.fact_hash != snap.schema_hash


def test_opendart_error_json_fails_closed():
    with pytest.raises(Exception):
        parse_json_response('{"status":"013","message":"no data"}')
