"""Real KISCO pipeline/renderer tests; staff and financial inputs are fixtures."""
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from research_report_completion import complete_research_report, _materialize_replay
from run_kr_live import RunbookError


SOURCE = ROOT / 'runs/kisco-104700'


def fixture_answer(gap, normalized_ebitda=None):
    for request in gap['staff_requests']:
        if request['role'] == 'filing_table_reader':
            raw = {'cells': [], 'not_found': re.findall(r'^- ([^:]+):', request['prompt'], re.M)}
        else:
            raw = json.loads((SOURCE / 'declarations/staff' / (request['role'] + '.json')).read_text())
        if isinstance(raw, list):
            raw = raw[0]
        if normalized_ebitda is not None:
            if request['role'] == 'bridge_analyst':
                for draft in raw['drafts']:
                    if draft['assumption_key'] == 'normalized_ebitda' and draft['scenario_id'] == 'Base':
                        draft['value'] = normalized_ebitda
            elif request['role'] == 'intelligence_officer':
                raw = json.loads(json.dumps(raw).replace('EBITDA of 60', f'EBITDA of {normalized_ebitda}'))
        path = Path(request['response_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 'staff-response/v1', 'role': request['role'],
            'prompt_sha256': request['prompt_sha256'], 'response': raw}))


def test_assisted_missing_staff_recovery_replays_and_publishes_real_report(tmp_path):
    original_hash = sha256((SOURCE / 'declarations/underwriting.yaml').read_bytes()).hexdigest()
    gaps = []
    def repair(gap):
        gaps.append(gap)
        fixture_answer(gap)
    result = complete_research_report(SOURCE, tmp_path / 'completion',
        SOURCE / 'declarations/underwriting.yaml', recovery_provider=repair, max_rounds=10)
    assert result['status'] == 'COMPLETED', result
    assert gaps and any(g['staff_requests'] for g in gaps)
    assert len(result['reached_stages']) >= 33
    report = Path(result['versioned_report_path'])
    assert report.is_file()
    assert '17,339' in report.read_text()
    bundle = report.parent
    assert len(list(bundle.glob('*.svg'))) == 2
    latest = json.loads(Path(result['latest_manifest_path']).read_text())
    assert latest['report_sha256'] == sha256(report.read_bytes()).hexdigest()
    assert (bundle / 'audit.json').is_file()
    assert (bundle / 'execution_attestation.json').is_file()
    for path in (Path(result['prepared_run_dir']) / 'declarations/staff').glob('*.json'):
        turns = json.loads(path.read_text())
        assert isinstance(turns, list)
        assert all(t['schema_version'] == 'staff-response/v1' for t in turns)
    assert sha256((SOURCE / 'declarations/underwriting.yaml').read_bytes()).hexdigest() == original_hash
    reused = complete_research_report(SOURCE, tmp_path / 'completion', SOURCE / 'declarations/underwriting.yaml')
    assert reused['versioned_report_path'] == str(report)


def test_unresolved_handoff_has_no_report_and_resumes(tmp_path):
    workspace = tmp_path / 'completion'
    result = complete_research_report(SOURCE, workspace, SOURCE / 'declarations/underwriting.yaml')
    assert result['status'] == 'WORK_REQUIRED'
    assert result['staff_requests']
    assert not (workspace / 'published/latest.json').exists()
    assert not list(workspace.glob('published/**/*.md'))
    fixture_answer(result)
    resumed = complete_research_report(SOURCE, workspace, SOURCE / 'declarations/underwriting.yaml',
        recovery_provider=fixture_answer)
    assert resumed['status'] == 'COMPLETED', resumed


def test_recovery_success_flag_without_input_change_does_not_pass(tmp_path):
    result = complete_research_report(SOURCE, tmp_path / 'completion',
        SOURCE / 'declarations/underwriting.yaml', recovery_provider=lambda gap: {'status': 'PASS'}, max_rounds=2)
    assert result['status'] == 'WORK_REQUIRED'
    assert '시도 한도' in result['recovery_note']


def test_target_change_by_recovery_is_rejected(tmp_path):
    def repair(gap):
        config_path = Path(gap['prepared_run_dir']) / 'run.yaml'
        config = yaml.safe_load(config_path.read_text())
        config['as_of'] = '2026-08-30'
        config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(RunbookError, match='identity'):
        complete_research_report(SOURCE, tmp_path / 'completion',
            SOURCE / 'declarations/underwriting.yaml', recovery_provider=repair)


def test_changed_underwriting_requires_bound_report_review(tmp_path):
    underwriting = yaml.safe_load((SOURCE / 'declarations/underwriting.yaml').read_text())
    # Commentary change still requires confirming the report matches its exact input.
    underwriting['declarations']['normalized_ebitda']['rationale'] += ' Explicit report review fixture.'
    path = tmp_path / 'underwriting.yaml'
    path.write_text(yaml.safe_dump(underwriting))
    workspace = tmp_path / 'completion'
    gap = complete_research_report(SOURCE, workspace, path, staff_mode='replay')
    assert gap['status'] == 'WORK_REQUIRED'
    assert gap['blocked_stage'] == 'REPORT_PROFILE_REVIEW'
    assert not list(workspace.glob('published/**/*.md'))
    def review(gap):
        (Path(gap['prepared_run_dir']) / 'declarations/report_review.json').write_text(json.dumps({
            'underwriting_sha256': gap['underwriting_sha256'], 'profile_sha256': gap['profile_sha256'],
            'rationale': 'Fixture commentary changed only; all reported numeric assumptions remain consistent.'}))
    result = complete_research_report(SOURCE, workspace, path, staff_mode='replay', recovery_provider=review)
    assert result['status'] == 'COMPLETED', result


def test_transcript_prompt_tampering_cannot_materialize_replay(tmp_path):
    request = tmp_path / 'role.digest.session.1.request.json'
    request.write_text(json.dumps({'role': 'role', 'prompt': 'original', 'prompt_sha256': 'tampered'}))
    request.with_name('role.digest.session.1.response.json').write_text(json.dumps({
        'role': 'role', 'prompt_sha256': 'tampered', 'response': {}}))
    with pytest.raises(RunbookError, match='identity'):
        _materialize_replay({request}, tmp_path)


def test_cli_three_research_misses_peer_margin_recovery_to_changed_report(tmp_path, monkeypatch, capsys):
    """No pipeline/compiler/reporter mocks: only host model responses are fixtures."""
    from types import ModuleType
    from tests.test_research_campaign import campaign_plan, answer
    from tests.test_peer_margin_research import proposal_inputs
    from valuation_engine.peer_margin_research import build_peer_margin_proposal
    from run_research_campaign import main

    baseline = yaml.safe_load((SOURCE / 'declarations/underwriting.yaml').read_text())
    plan = campaign_plan()
    plan.update(target_id=baseline['target_id'], as_of=baseline['as_of'])
    plan['requests'][0].update(metric='normalized_ebitda', margin_basis='EBITDA', forecast_year=2027, unit='KRW_billion')
    plan_path = tmp_path / 'plan.yaml'
    plan_path.write_text(yaml.safe_dump(plan))
    inputs = proposal_inputs()
    inputs.update(target=plan['target_id'], segment='core', as_of=plan['as_of'], economic_path_id='capacity-path')
    inputs['years'][0]['year'] = 2027
    inputs['years'][1]['year'] = 2028
    inputs['years'][0]['adjustments'][0]['percentage_points']['base'] = '-12.5'
    inputs['sources'][0]['url'] = baseline['declarations']['normalized_ebitda']['source_refs'][0]
    receipt = build_peer_margin_proposal(inputs)
    calls = []
    def research(order):
        calls.append(order['recovery'])
        if len(calls) <= 3:
            return None
        response = answer(order, '65')
        response['bounds'] = {'low': '40', 'high': '100'}
        response['sources'] = [{**inputs['sources'][0], 'source_family': 'fixture-peer-filings',
            'kind': 'primary', 'excerpt': 'Explicit synthetic peer results fixture, not actual company disclosure.'}]
        response['rationale'] = 'Fixture comparable margins adjusted 12.5 percentage points for target utilization.'
        response['peer_margin'] = {'receipt': receipt, 'year': 2027, 'case': 'base',
            'revenue': {'value': '200', 'unit': 'KRW_billion', 'source_ids': ['annual'], 'year': 2027,
                'rationale': 'Explicit target revenue fixture used only for end-to-end implementation testing.'}}
        return response
    def repair(gap):
        fixture_answer(gap, normalized_ebitda=65)
        if gap['blocked_stage'] == 'REPORT_PROFILE_REVIEW':
            prepared = Path(gap['prepared_run_dir'])
            profile_path = prepared / 'declarations/investor_report.yaml'
            profile = yaml.safe_load(profile_path.read_text().replace('600억원', '650억원'))
            profile['major_assumptions'] += ' 비교기업 이익률을 조정한 테스트 가정으로 기준 EBITDA 650억원을 반영했습니다.'
            profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True))
            (prepared / 'declarations/report_review.json').write_text(json.dumps({
                'underwriting_sha256': gap['underwriting_sha256'],
                'profile_sha256': sha256(profile_path.read_bytes()).hexdigest(),
                'rationale': 'Reviewed updated EBITDA fixture and synchronized report assumptions.'}))
    host = ModuleType('completion_test_host')
    host.research, host.repair = research, repair
    monkeypatch.setitem(sys.modules, host.__name__, host)
    workspace = tmp_path / 'work'
    code = main([str(plan_path), '--workspace', str(workspace), '--run-dir', str(SOURCE),
        '--provider', 'completion_test_host:research', '--recovery-provider', 'completion_test_host:repair'])
    output = capsys.readouterr()
    assert code == 0, output.out + output.err
    assert len(calls) == 4
    manifests = list(workspace.glob('completion/*/published/*_LATEST_REPORT.json'))
    assert len(manifests) == 1
    latest = json.loads(manifests[0].read_text())
    report = manifests[0].parent / latest['report_filename']
    assert report.is_file()
    assert '650억원' in report.read_text()
    assert '600억원' not in report.read_text()
    numeric_report = (report.parent / 'final_report.md').read_text()
    base_value = re.search(r'기준 시나리오:\*\* 내재가치 주당 ([\d,]+)원', numeric_report)
    assert base_value and int(base_value.group(1).replace(',', '')) > 17339
    assert len(list(report.parent.glob('*.svg'))) == 2
    assert latest['report_sha256'] == sha256(report.read_bytes()).hexdigest()
    merged = next(workspace.glob('underwriting-*.yaml'))
    row = yaml.safe_load(merged.read_text())['declarations']['normalized_ebitda']
    assert str(row['value']) == '65'
    assert row['research_receipt']['response']['peer_margin']['receipt']['metric'] == 'EBITDA'
