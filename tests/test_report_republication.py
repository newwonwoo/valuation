from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from valuation_engine.report_republication import (
    ReportRepublicationError,
    republish_frozen_investor_report,
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


FULL = '''# 합성회사 투자보고서

## 1. 투자판단 요약
- 투자의견: 시나리오 기준 평가
- 현재가: 200원 (2026-10-26T09:00:00+09:00)
- 평가 기준가: 100원
- 핵심 결론: 계약조건을 확인한 조건부 평가이며 추가 납입 후 권리와 기존 거래주식의 권리는 서로 다릅니다.
- 판단 근거: 확률가중 기대값과 구체 매수가는 산출하지 않았습니다.

## 2. 투자논리
### 계약과 비용
- 관찰과 가정: 신규 계약 확대를 조건으로 매출이 증가합니다.
- 근거: 공시 계약 현황을 확인했습니다.
- 가치 연결: 추가 수행 비용과 운전자본을 함께 반영했습니다.
- 반증 조건: 계약이 지연되면 매출을 낮춰야 합니다.

## 3. 가치평가와 민감도
| 시나리오 | 주당가치 | 성립 조건 | 가치 연결 |
|---|---:|---|---|
| Down | 50원 | 계약 지연 | 현금 유입 감소 |
| Base | 100원 | 조건부 계약 이행 | 비용과 투자 반영 |
| Bull | 150원 | 고객 확대 | 수행 비용 증가 |

- 평가방법: 조건부 DCF
- 방법 선택 이유: 매출과 비용을 현금흐름으로 연결했습니다.
- 주요 가정: 임의의 추가 재무가정을 선택하지 않습니다.
- 평가 제외 항목: 미확정 신규 사업은 미산정이며 0가치로 단정하지 않습니다.

### 계산된 가치 민감도
| 시나리오 | 대상 | 변수 변화 | 주당가치 영향 |
|---|---|---:|---:|
| Base | 전체 | 할인율 ±0.5%p | +3% / -3% |

## 4. 사업부별 평가
| 구분 | 평가 여부 | 핵심 내용 | 비고 |
|---|---|---|---|
| 핵심 | 평가 | 현재 계약 범위 | 신규 미확정 사업은 별도 |

## 5. 위험과 판단 변경 조건
### 주요 위험
- 자본조달은 확약되지 않았습니다.
- 추가 고객지원 비용이 발생할 수 있습니다.
- 마지막 위험 문장도 삭제하지 않아야 합니다.
### 판단 변경 조건
- 상방 조건: 계약이 실제 확대되는 경우입니다.
- 하방 조건: 더 높은 수행비용이 설득력 있는 경우입니다.
- 행동 가능 조건: 검증되지 않은 매수가를 제시하지 않습니다.

## 6. 증권사·시장 비교
- 증권사 개별 목표가는 확보하지 못했습니다.
- 비교 원문에 포함된 전체 한계 문장을 상세에 보존합니다.

## 7. 원문 자료
- 공시: [계약 원문](https://example.com/filing)
- 추가 추정 근거: [마지막 원문](https://example.com/last)
'''


@pytest.fixture
def frozen(tmp_path):
    root = tmp_path / 'frozen'
    root.mkdir()
    identity = {'run_id': 'SYNTHETIC-1', 'ticker': 'SYNTHETIC', 'as_of': '2026-10-26',
                'valuation_hash': 'v' * 64, 'audit_hash': 'a' * 64}
    from valuation_engine.runtime_authority import _attestation_hash
    attestation = {'run_id': identity['run_id'], 'execution_mode': 'test', 'stage_receipt_hashes': ['stage'], 'final_stage': 'FINAL_REPORT', 'freeze_token_hash': 'f' * 64}
    attestation['attestation_hash'] = _attestation_hash(**attestation)
    contents = {
        'manifest.json': {'run_id': identity['run_id'], 'ticker': identity['ticker'], 'status': 'COMPLETED', 'audit_passed': True},
        'freeze_token.json': {**identity, 'token_hash': 'f' * 64},
        'valuation.json': {'valuation_hash': identity['valuation_hash'], 'scenarios': [{'value_per_share': '100'}]},
        'execution_attestation.json': attestation,
        'control_plane_trace.json': {'completed': True}, 'audit.json': {'findings': []},
        'compiled_assumptions.json': {'exact_amount': '123.456789012345678901'},
    }
    for name, content in contents.items():
        write_json(root / name, content)
    (root / 'report.md').write_text(FULL)
    (root / 'final_report.md').write_text('Unchanged internal audit report')
    (root / 'card_1.svg').write_text('<svg>100</svg>')
    (root / 'card_2.svg').write_text('<svg>conditions</svg>')
    manifest = {**identity, 'schema_version': 'kr-live-report-bundle/v1', 'artifact_id': 'SYNTHETIC-20261026-TP100-ABCDEF123456',
                'run_input_sha256': 'i' * 64, 'reference_value_per_share': '100', 'report_filename': 'report.md',
                'files': [{'filename': p.name, 'sha256': digest(p)} for p in sorted(root.iterdir())]}
    write_json(root / 'report_bundle_manifest.json', manifest)
    return root


def test_frozen_display_only_reissue_preserves_financial_and_original_bytes(frozen, tmp_path):
    original = {p.name: digest(p) for p in frozen.iterdir()}
    result = republish_frozen_investor_report(frozen, expected_manifest_sha256=original['report_bundle_manifest.json'], output_dir=tmp_path / 'out')
    assert {p.name: digest(p) for p in frozen.iterdir()} == original
    main, detail = Path(result['versioned_report_path']), Path(result['versioned_detail_report_path'])
    assert f']({detail.name})' in main.read_text()
    from valuation_engine.investor_report import append_report_detail_links
    assert detail.read_text() == append_report_detail_links(FULL, has_broker_review=False)
    for text in ('100원', '200원', '2026-10-26T09:00:00+09:00', '추가 납입 후 권리', '마지막 위험 문장', '0가치', '매수가'):
        assert text in main.read_text()
    receipt = json.loads((main.parent / 'report_presentation_revision.json').read_text())
    assert receipt['financial_recalculation_performed'] is False
    assert receipt['new_financial_audit_or_freeze_performed'] is False
    for name in ('valuation.json', 'compiled_assumptions.json', 'freeze_token.json', 'audit.json', 'execution_attestation.json', 'card_1.svg', 'card_2.svg'):
        assert digest(main.parent / name) == original[name]
    manifest = json.loads((main.parent / 'report_bundle_manifest.json').read_text())
    assert all(digest(main.parent / row['filename']) == row['sha256'] for row in manifest['files'])
    assert result['valuation_hash'] == 'v' * 64
    with pytest.raises(ReportRepublicationError, match='already exists'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=original['report_bundle_manifest.json'], output_dir=tmp_path / 'out')


def test_reissue_rejects_tampered_source_before_any_output(frozen, tmp_path):
    pin = digest(frozen / 'report_bundle_manifest.json')
    (frozen / 'valuation.json').write_text('{}')
    with pytest.raises(ReportRepublicationError, match='source file hash mismatch'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=pin, output_dir=tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_reissue_requires_operator_pin_and_external_destination(frozen, tmp_path):
    with pytest.raises(ReportRepublicationError, match='operator pin'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256='0' * 64, output_dir=tmp_path / 'out')
    with pytest.raises(ReportRepublicationError, match='inside'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=digest(frozen / 'report_bundle_manifest.json'), output_dir=frozen / 'out')


def test_source_detail_only_change_changes_publication_identity(frozen, tmp_path):
    first = republish_frozen_investor_report(frozen, expected_manifest_sha256=digest(frozen / 'report_bundle_manifest.json'), output_dir=tmp_path / 'one')
    # A separately pinned immutable source, not mutation of the original bundle.
    import shutil
    other = tmp_path / 'other'
    shutil.copytree(frozen, other)
    p = other / 'report.md'
    p.write_text(p.read_text() + '- 추가: [새 상세 출처](https://example.com/new-detail)\n')
    manifest = json.loads((other / 'report_bundle_manifest.json').read_text())
    for row in manifest['files']:
        if row['filename'] == 'report.md':
            row['sha256'] = digest(p)
    write_json(other / 'report_bundle_manifest.json', manifest)
    second = republish_frozen_investor_report(other, expected_manifest_sha256=digest(other / 'report_bundle_manifest.json'), output_dir=tmp_path / 'two')
    assert first['artifact_id'] != second['artifact_id']
    assert first['valuation_hash'] == second['valuation_hash']


def test_unreceipted_file_and_symlink_rejected(frozen, tmp_path):
    pin = digest(frozen / 'report_bundle_manifest.json')
    (frozen / 'unbound.txt').write_text('not authorized')
    with pytest.raises(ReportRepublicationError, match='unreceipted'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=pin, output_dir=tmp_path / 'out')
    (frozen / 'unbound.txt').unlink()
    text = (frozen / 'report.md').read_text()
    (tmp_path / 'outside.md').write_text(text)
    (frozen / 'report.md').unlink()
    (frozen / 'report.md').symlink_to(tmp_path / 'outside.md')
    with pytest.raises(ReportRepublicationError, match='symlink'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=pin, output_dir=tmp_path / 'out')


def test_reissue_rejects_path_ticker_even_with_new_operator_pin(frozen, tmp_path):
    manifest = json.loads((frozen / 'report_bundle_manifest.json').read_text())
    manifest['ticker'] = '../../outside'
    write_json(frozen / 'report_bundle_manifest.json', manifest)
    with pytest.raises(ReportRepublicationError, match='unsafe ticker'):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=digest(frozen / 'report_bundle_manifest.json'), output_dir=tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_normal_publisher_requests_bound_host_brief_and_links_detail(tmp_path):
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'scripts'))
    from run_kr_live import execute_run, publish_report_bundle, reuse_published_report_bundle
    run = root / 'runs/koreazinc-010130'
    _, stopped, reason, result = execute_run(run, state_root=str(tmp_path / 'state'))
    assert stopped is None, reason
    published = publish_report_bundle(run, result, output_dir=tmp_path / 'normal')
    assert published['editorial_status'] == 'HOST_BRIEF_REQUIRED_SAFE_FALLBACK'
    main = Path(published['versioned_report_path'])
    detail = Path(published['versioned_detail_report_path'])
    request = json.loads((main.parent / 'report_brief_request.json').read_text())
    assert request['source_report_sha256'] == digest(detail)
    assert request['source_report'] == detail.read_text()
    assert request['financial_mutation_allowed'] is False
    assert f']({detail.name})' in main.read_text()
    assert list((tmp_path / 'normal/staff_requests').glob('report_editor.*.json'))
    reused = reuse_published_report_bundle(run, output_dir=tmp_path / 'normal')
    assert reused['versioned_detail_report_path'] == str(detail)
    detail.write_text(detail.read_text() + 'tamper')
    with pytest.raises(Exception, match='detail report hash'):
        reuse_published_report_bundle(run, output_dir=tmp_path / 'normal')


def test_llm_may_author_brief_but_not_execute_publication(frozen, tmp_path):
    from valuation_engine.runtime_authority import llm_proposal_scope
    with llm_proposal_scope(), pytest.raises(PermissionError):
        republish_frozen_investor_report(frozen, expected_manifest_sha256=digest(frozen / 'report_bundle_manifest.json'), output_dir=tmp_path / 'out')
