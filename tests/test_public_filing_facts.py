from copy import deepcopy
from hashlib import sha256
import json

import pytest

from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.kr_opendart_provider import OpenDartFilingSelection
from valuation_engine.public_filing_facts import load_public_filing_facts, public_filing_fact_provider


@pytest.fixture
def prepared(tmp_path):
    receipt = '20260316000001'
    url = f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}'
    identity = '<html>대한항공 <script>corp_code="00113526";</script></html>'
    html = '''<h2>연결재무제표</h2><p>제64기 2025.01.01부터 2025.12.31까지</p>
    <p>(단위: 백만원)</p><table><tr><th>계정</th><th>제64기</th><th>제63기</th></tr>
    <tr><td>매출액</td><td>1,234</td><td>900</td></tr>
    <tr><td>영업이익</td><td>(12)</td><td>4</td></tr></table>'''
    def doc(name, text):
        (tmp_path / name).write_text(text)
        return {'path': name, 'sha256': sha256(text.encode()).hexdigest(), 'url': url}
    payload = {'schema_version': 'public-filing-facts/v1', 'target_id': 'KR:DART:00113526',
        'issuer_name': '대한항공', 'as_of': '2026-03-17', 'rcept_no': receipt,
        'fs_div': 'CFS', 'business_year': '2025', 'report_code': '11011',
        'fiscal_period_end': '2025-12-31', 'identity_document': doc('identity.html', identity),
        'documents': [doc('facts.html', html) | {'document_id': 'statements',
            'scope_quote': '연결재무제표', 'period_quote': '제64기 2025.01.01부터 2025.12.31까지',
            'unit_quote': '(단위: 백만원)'}],
        'observations': [{'metric': 'revenue', 'document_id': 'statements',
            'table_index': 0, 'row_index': 1, 'column_index': 1,
            'row_label': '매출액', 'column_label': '제64기', 'value': '1234000000', 'unit': 'KRW'}]}
    path = tmp_path / 'facts.json'
    path.write_text(json.dumps(payload))
    filing = OpenDartFilingSelection(business_year='2025', report_code='11011',
                                    fiscal_period_end='2025-12-31', checked_at='2026-03-17')
    return path, payload, filing


def test_source_cell_and_receipt_survive_collection(prepared):
    path, payload, filing = prepared
    provider = public_filing_fact_provider(path, filing=filing, run_as_of='2026-03-17')
    batch = provider.collector(EvidenceCollectionRequest(payload['target_id'], ('revenue',)))
    assert batch.records[0].value == 1234000000
    assert batch.records[0].source_name == 'DART public financial statement HTML'
    receipt = json.loads(batch.records[0].notes.split('=', 1)[1])
    assert receipt['raw_cell'] == '1,234'
    assert receipt['document']['sha256'] == payload['documents'][0]['sha256']
    assert batch.document_ids == (payload['rcept_no'],)


@pytest.mark.parametrize('change', [
    lambda p: p['observations'][0].update(value='0'),
    lambda p: p['observations'][0].update(column_index=2, column_label='제63기', value='900000000'),
    lambda p: p['observations'][0].update(row_label='영업이익'),
    lambda p: p['observations'][0].update(metric='total_assets'),
    lambda p: p['documents'][0].update(period_quote='제64기 2024.12.31'),
    lambda p: p['documents'][0].update(scope_quote='별도재무제표'),
    lambda p: p['documents'][0].update(unit_quote='(단위: 천원)'),
    lambda p: p['documents'][0].update(url='https://example.com/?rcpNo=20260316000001'),
    lambda p: p.update(as_of='2026-03-18'),
    lambda p: p.update(issuer_name='다른회사'),
    lambda p: p.update(fs_div='OFS'),
    lambda p: p.update(target_id='KR:DART:00000001'),
    lambda p: p['observations'][0].update(table_index=-1),
    lambda p: p['observations'][0].update(value='NaN'),
    lambda p: p['observations'].append(deepcopy(p['observations'][0])),
])
def test_refuse_invalid_source_bindings(prepared, change):
    path, payload, filing = prepared
    change(payload)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_public_filing_facts(path, filing=filing, run_as_of='2026-03-17')


def test_changed_document_is_rechecked_at_collection(prepared):
    path, payload, filing = prepared
    provider = public_filing_fact_provider(path, filing=filing, run_as_of='2026-03-17')
    (path.parent / 'facts.html').write_text('tampered')
    with pytest.raises(ValueError, match='hash'):
        provider.collector(EvidenceCollectionRequest(payload['target_id'], ('revenue',)))


def test_wrong_requested_target_is_rejected(prepared):
    path, payload, filing = prepared
    provider = public_filing_fact_provider(path, filing=filing, run_as_of='2026-03-17')
    with pytest.raises(ValueError, match='target'):
        provider.collector(EvidenceCollectionRequest('KR:DART:00000001', ('revenue',)))


def test_negative_financial_amount(prepared):
    path, payload, filing = prepared
    payload['observations'][0].update(metric='operating_income', row_index=2,
                                    row_label='영업이익', value='-12000000')
    path.write_text(json.dumps(payload))
    records, *_ = load_public_filing_facts(path, filing=filing, run_as_of='2026-03-17')
    assert records[0].value == -12000000


def test_interim_shared_header_cannot_prove_ytd(prepared):
    from dataclasses import replace
    path, payload, filing = prepared
    payload['report_code'] = '11012'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='cumulative'):
        load_public_filing_facts(path, filing=replace(filing, report_code='11012'),
                                run_as_of='2026-03-17')


def test_malformed_json_duplicate_keys_rejected(prepared):
    path, _, filing = prepared
    path.write_text('{"target_id":"one","target_id":"two"}')
    with pytest.raises(ValueError, match='duplicate'):
        load_public_filing_facts(path, filing=filing, run_as_of='2026-03-17')


def test_dart_note_suffix_and_two_level_cumulative_header(prepared):
    from dataclasses import replace
    path, payload, filing = prepared
    html = '''<h2>연결 포괄손익계산서</h2>
    <table><tr><td>제 65 기 반기 2026.01.01 부터 2026.06.30 까지</td></tr>
    <tr><td>(단위 : 원)</td></tr></table>
    <table><tr><th></th><th colspan="2">제 65 기 반기</th></tr>
    <tr><th></th><th>3개월</th><th>누적</th></tr>
    <tr><td>매출 (주4,29,37)</td><td>123</td><td>321</td></tr></table>'''
    raw = path.parent / 'facts.html'
    raw.write_text(html)
    old_receipt, new_receipt = payload['rcept_no'], '20260814002803'
    payload.update(rcept_no=new_receipt, as_of='2026-09-13', business_year='2026',
                   fiscal_period_end='2026-06-30', report_code='11012')
    for document in [payload['identity_document'], *payload['documents']]:
        document['url'] = document['url'].replace(old_receipt, new_receipt)
    payload['documents'][0].update(sha256=sha256(html.encode()).hexdigest(),
        scope_quote='연결 포괄손익계산서', unit_quote='(단위 : 원)',
        period_quote='제 65 기 반기 2026.01.01 부터 2026.06.30 까지')
    payload['observations'][0].update(table_index=1, row_index=2, column_index=2,
        row_label='매출 (주4,29,37)', column_label='제 65 기 반기', value='321')
    path.write_text(json.dumps(payload))
    filing = replace(filing, business_year='2026', report_code='11012',
        fiscal_period_end='2026-06-30', checked_at='2026-09-13')
    records, *_ = load_public_filing_facts(path, filing=filing, run_as_of='2026-09-13')
    assert records[0].value == 321
    payload['observations'][0].update(column_index=1, value='123')
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='cumulative'):
        load_public_filing_facts(path, filing=filing, run_as_of='2026-09-13')
