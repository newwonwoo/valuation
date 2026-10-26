"""Early host judgments are proposals; fixtures never claim live valuation evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import research_report_completion as completion
from run_kr_live import RunbookError, _execution_outcome, _run_input_sha256, reuse_published_report_bundle
from valuation_engine.control_plane import StageStatus
from valuation_engine.orchestrator import StageTrace

SOURCE = ROOT / 'runs/kisco-104700'


@pytest.fixture
def prepared(tmp_path):
    target = tmp_path / 'source'
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns('out'))
    config_path = target / 'run.yaml'
    config = yaml.safe_load(config_path.read_text())
    for key in ('artifact', 'provenance', 'conditioning'):
        config['calibration'][key] = str((SOURCE / config['calibration'][key]).resolve())
    config_path.write_text(yaml.safe_dump(config))
    return target


def response_for(request, decision, proposal):
    return {'schema_version': 'host-judgment-response/v1', 'request_hash': request.request_hash,
            'target_id': request.gap.target_id, 'as_of': request.gap.as_of,
            'decision': decision, 'proposal': proposal,
            'rationale': 'Explicit fixture judgment; canonical validators remain authoritative.',
            'counterevidence': [], 'uncertainty': 'Fixture does not establish actual investment performance.'}


def missing_method(prepared):
    path = prepared / 'run.yaml'
    config = yaml.safe_load(path.read_text())
    config.pop('method')
    path.write_text(yaml.safe_dump(config))
    return completion._early_judgment(prepared, _run_input_sha256(prepared))


def method_proposal(prepared):
    source = prepared / 'raw/company.json'
    return {'method': 'commodity_price_taker/normalized_multiple',
            'evidence': [{'path': str(source), 'sha256': sha256(source.read_bytes()).hexdigest(),
                          'quote': '"induty_code": "2411"'}]}


def test_missing_method_exposes_only_actual_primary_dna_candidates(prepared):
    request = missing_method(prepared)
    candidates = request.response_schema['proposal']['method']['enum']
    assert 'commodity_price_taker/normalized_multiple' in candidates
    assert not any('warranted_per' in item for item in candidates)
    assert not any('recurring_subscription' in item for item in candidates)
    assert request.write_targets == ('run.yaml:method',)
    assert completion._apply_judgment(prepared, request,
        response_for(request, 'SELECT_METHOD', method_proposal(prepared)))
    assert yaml.safe_load((prepared / 'run.yaml').read_text())['method'] == 'commodity_price_taker/normalized_multiple'
    assert list((prepared / 'declarations/judgment_receipts').glob('*.json'))


@pytest.mark.parametrize('bad', ['stale', 'forged_quote', 'forged_source', 'wrong_role', 'pass', 'patch_path'])
def test_method_judgment_rejects_unbound_or_unauthorized_responses(prepared, bad):
    request = missing_method(prepared)
    response = response_for(request, 'SELECT_METHOD', method_proposal(prepared))
    if bad == 'stale':
        response['request_hash'] = 'f' * 64
    elif bad == 'forged_quote':
        response['proposal']['evidence'][0]['quote'] = 'This sentence does not exist in the original source.'
    elif bad == 'forged_source':
        response['proposal']['evidence'][0]['sha256'] = 'f' * 64
    elif bad == 'wrong_role':
        response['proposal']['method'] = 'capacity_manufacturing/warranted_per'
    elif bad == 'pass':
        response['decision'] = 'PASS'
    else:
        response['proposal']['path'] = '../../published/final_report.md'
    before = _run_input_sha256(prepared)
    with pytest.raises(ValueError):
        completion._apply_judgment(prepared, request, response)
    assert _run_input_sha256(prepared) == before


def test_stale_parent_input_is_rejected_before_apply(prepared):
    request = missing_method(prepared)
    response = response_for(request, 'SELECT_METHOD', method_proposal(prepared))
    (prepared / 'declarations/new_evidence.txt').write_text('changed parent input')
    with pytest.raises(RunbookError, match='stale'):
        completion._apply_judgment(prepared, request, response)


def test_first_run_profile_missing_emits_actionable_request_without_publication(prepared, tmp_path):
    (prepared / 'declarations/investor_report.yaml').unlink()
    assert reuse_published_report_bundle(prepared) is None
    result = completion.complete_research_report(prepared, tmp_path / 'workspace',
        prepared / 'declarations/underwriting.yaml')
    assert result['status'] == 'WORK_REQUIRED'
    assert result['host_judgment_request']['gap']['judgment_kind'] == 'REPORT_PROFILE'
    assert Path(result['judgment_request_path']).is_file()
    assert not list((tmp_path / 'workspace').glob('published/**/*.md'))


def test_typed_profile_accepts_existing_sources_rejects_forged_sources(prepared):
    profile_path = prepared / 'declarations/investor_report.yaml'
    profile = yaml.safe_load(profile_path.read_text())
    profile_path.unlink()
    request = completion._early_judgment(prepared, _run_input_sha256(prepared))
    bad = deepcopy(profile)
    bad['sources'][0]['url'] = 'https://example.invalid/forged-primary'
    with pytest.raises(RunbookError, match='unbound source'):
        completion._apply_judgment(prepared, request,
            response_for(request, 'PROVIDE_REPORT_PROFILE', {'profile': bad}))
    completion._apply_judgment(prepared, request,
        response_for(request, 'PROVIDE_REPORT_PROFILE', {'profile': profile}))
    assert profile_path.is_file()


def test_valid_method_response_reenters_canonical_runner(prepared, tmp_path, monkeypatch):
    missing_method(prepared)
    calls = []
    def runner(path, **kwargs):
        calls.append(yaml.safe_load((path / 'run.yaml').read_text())['method'])
        return (), 'NEXT_INPUT', 'Fixture stops after canonical entry, not a completed valuation.', None
    monkeypatch.setattr(completion, 'execute_run', runner)
    def host(gap):
        typed = gap.get('host_judgment_request')
        if typed is None:
            return None
        target = Path(gap['prepared_run_dir'])
        request = completion._early_judgment(target, _run_input_sha256(target))
        return response_for(request, 'SELECT_METHOD', method_proposal(target))
    result = completion.complete_research_report(prepared, tmp_path / 'workspace',
        prepared / 'declarations/underwriting.yaml', recovery_provider=host)
    assert calls == ['commodity_price_taker/normalized_multiple']
    assert result['status'] == 'WORK_REQUIRED' and result['blocked_stage'] == 'NEXT_INPUT'


def test_unchanged_callback_not_reexecuted_or_reset_by_reinvocation(prepared, tmp_path, monkeypatch):
    calls, repairs = [], []
    def runner(*args, **kwargs):
        calls.append(1)
        return (), 'MISSING_FIXTURE_INPUT', 'same fixture gap', None
    monkeypatch.setattr(completion, 'execute_run', runner)
    def host(gap):
        repairs.append(1)
        return {'status': 'PASS'}
    work = tmp_path / 'workspace'
    for _ in range(2):
        result = completion.complete_research_report(prepared, work,
            prepared / 'declarations/underwriting.yaml', recovery_provider=host)
        assert result['status'] == 'WORK_REQUIRED'
    assert len(calls) == len(repairs) == 1
    ledger = json.loads((work / 'completion_state.json').read_text())
    assert sum(ledger['attempts'].values()) == 1
    assert ledger['executions'] == 1


def test_prepared_original_source_cannot_be_repaired(prepared, tmp_path, monkeypatch):
    monkeypatch.setattr(completion, 'execute_run', lambda *args, **kwargs:
                        ((), 'FIXTURE_INPUT_GAP', 'controlled fixture input gap', None))
    def host(gap):
        source = Path(gap['prepared_run_dir']) / 'raw/company.json'
        source.write_text('{}')
    with pytest.raises(RunbookError, match='immutable original source'):
        completion.complete_research_report(prepared, tmp_path / 'workspace',
            prepared / 'declarations/underwriting.yaml', recovery_provider=host)


def test_resolved_historical_red_team_handoff_does_not_stop_wrapper():
    original = SimpleNamespace(id='ISSUE-1', blocking=True, resolved=False)
    recovered = SimpleNamespace(id='ISSUE-1', blocking=True, resolved=True)
    result = SimpleNamespace(stage_traces=(
        StageTrace('BLIND_RED_TEAM_B', StageStatus.RECOVERY_REQUIRED, 'needs evidence', False),
        StageTrace('RESEARCH_LOOP', StageStatus.RECOVERED, 'source-bound recovery', False),
        StageTrace('AUDIT', StageStatus.PASS, 'fixture audit', False)),
        data={'red_team_proposal': SimpleNamespace(issues=(original,)),
              'recovered_red_team_proposal': SimpleNamespace(issues=(recovered,)),
              'recovered_red_team_issue_ids': ('ISSUE-1',)})
    reached, stop, _, _ = _execution_outcome(result)
    assert stop is None and reached[-1] == 'AUDIT'
    result.data['recovered_red_team_issue_ids'] = ('OTHER',)
    assert _execution_outcome(result)[1] == 'BLIND_RED_TEAM_B'
    result.data['recovered_red_team_issue_ids'] = ('ISSUE-1',)
    result.stage_traces = (StageTrace('UNRELATED', StageStatus.RECOVERY_REQUIRED, 'unresolved', False),
                           *result.stage_traces[1:])
    assert _execution_outcome(result)[1] == 'UNRELATED'


def test_research_budget_survives_same_workspace_invocation(tmp_path):
    from run_research_campaign import execute_campaign
    from tests.test_research_campaign import campaign_plan, answer
    plan = tmp_path / 'plan.yaml'
    plan.write_text(yaml.safe_dump(campaign_plan()))
    work, calls = tmp_path / 'workspace', []
    def host(order):
        calls.append(order)
        return None
    for _ in range(2):
        result, _ = execute_campaign(plan, work, provider=host, max_rounds=2)
        assert result['status'] == 'WORK_REQUIRED'
    assert len(calls) == 2
    order = calls[-1]
    (work / 'responses/capacity.json').write_text(json.dumps(answer(order)))
    result, _ = execute_campaign(plan, work, provider=host, max_rounds=2)
    assert result['status'] == 'READY_FOR_COMPILATION'
    assert len(calls) == 2


def test_scope_common_response_path_feeds_bound_staff_and_requires_reentry(tmp_path):
    from tests.test_company_scope_judgment import loader, IDENTITY, answer
    from valuation_engine.host_judgment import HostJudgmentRequired
    from valuation_engine.staff_transport import StaffTransport
    prepared = tmp_path / 'prepared'
    transport = StaffTransport(prepared / 'declarations/staff', mode='assisted',
                               request_dir=prepared / 'out/staff_requests')
    scope = loader(prepared, transport=transport)
    with pytest.raises(HostJudgmentRequired) as missing:
        scope(IDENTITY)
    request = missing.value.request
    staff = completion._requests(completion._audit_requests(prepared))
    completion._stage_scope_response(prepared, request.to_dict(), staff, answer(request))
    with pytest.raises(HostJudgmentRequired) as accepted:
        # Fresh transport: one canonical execution, not a hidden within-run pass.
        loader(prepared, transport=StaffTransport(prepared / 'declarations/staff', mode='assisted',
            request_dir=prepared / 'out/staff_requests'))(IDENTITY)
    assert accepted.value.status == 'ACCEPTED_REENTRY_REQUIRED'
    assert not (prepared / 'declarations/company_scope_receipt.json').exists()
    receipt = accepted.value.accepted_receipt
    completion._write(prepared / 'declarations/company_scope_receipt.json', receipt)
    snapshot = loader(prepared, transport=StaffTransport(prepared / 'declarations/staff', mode='assisted'))(IDENTITY)
    assert any('SEGMENT_SCOPE:SINGLE' in item for item in snapshot.evidence_ids)


def test_original_descriptor_and_nested_document_cannot_be_rehashed(prepared, tmp_path):
    directory = prepared / 'declarations/public_sources'
    directory.mkdir()
    document = directory / 'original.html'
    document.write_text('<p>Original source bytes.</p>')
    descriptor = directory / 'facts.json'
    descriptor.write_text(json.dumps({'identity_document': {'path': 'original.html',
        'sha256': sha256(document.read_bytes()).hexdigest()}, 'documents': []}))
    config_path = prepared / 'run.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['public_filing_facts_path'] = str(descriptor)
    config_path.write_text(yaml.safe_dump(config))
    work = tmp_path / 'work'
    work.mkdir()
    copy = completion._prepare(prepared, work, prepared / 'declarations/underwriting.yaml')
    completion._protected_sources(prepared, copy)
    forged = copy / 'declarations/public_sources/original.html'
    forged.write_text('<p>Forged source bytes.</p>')
    copied_descriptor = copy / 'declarations/public_sources/facts.json'
    payload = json.loads(copied_descriptor.read_text())
    payload['identity_document']['sha256'] = sha256(forged.read_bytes()).hexdigest()
    copied_descriptor.write_text(json.dumps(payload))
    with pytest.raises(RunbookError, match='immutable original source documents'):
        completion._protected_sources(prepared, copy)


def test_rejected_scope_responses_share_source_bound_budget(prepared, tmp_path, monkeypatch):
    from tests.test_company_scope_judgment import source, answer
    request, _ = source()
    calls = []
    def runner(path, **kwargs):
        prompt = request.prompt()
        digest = sha256(prompt.encode()).hexdigest()
        staff = {'role': 'company_scope_analyst', 'prompt': prompt, 'prompt_sha256': digest,
                 'response_path': str(path / 'declarations/staff/responses' / f'company_scope_analyst.{digest}.json')}
        completion._write(path / 'out/staff_requests/audit' / f'scope.{len(calls)}.request.json', staff)
        calls.append(1)
        result = SimpleNamespace(completed=False, data={'host_judgment_request': request.to_dict(),
            'host_judgment_status': 'HOST_DECISION_REQUIRED'})
        return (), 'INDUSTRY_KNOWLEDGE_LOAD', f'changed rejection prose {len(calls)}', result
    monkeypatch.setattr(completion, 'execute_run', runner)
    provider_calls = []
    def host(gap):
        provider_calls.append(1)
        response = answer(request)
        response['rationale'] += f' Invalid fixture revision {len(provider_calls)}.'
        return response
    work = tmp_path / 'workspace'
    for _ in range(3):
        result = completion.complete_research_report(prepared, work,
            prepared / 'declarations/underwriting.yaml', recovery_provider=host, max_rounds=2)
    assert result['status'] == 'WORK_REQUIRED'
    assert len(provider_calls) == 2
    ledger = json.loads((work / 'completion_state.json').read_text())
    assert list(ledger['attempts'].values()) == [2]


def test_hard_integrity_block_never_invokes_host_on_same_workspace_resume(prepared, tmp_path, monkeypatch):
    calls, host_calls = [], []
    def runner(*args, **kwargs):
        calls.append(1)
        result = SimpleNamespace(completed=False, data={'host_judgment_status': 'HARD_BLOCKED'})
        return (), 'INDUSTRY_KNOWLEDGE_LOAD', 'fixture source integrity mismatch', result
    monkeypatch.setattr(completion, 'execute_run', runner)
    for _ in range(2):
        result = completion.complete_research_report(prepared, tmp_path / 'workspace',
            prepared / 'declarations/underwriting.yaml', recovery_provider=lambda gap: host_calls.append(gap))
        assert result['recovery_allowed'] is False
    assert len(calls) == 1
    assert not host_calls


@pytest.mark.parametrize('policy', [
    {'entry_rule': {'margin_of_safety': '0.1', 'rationale': 'Unauthorized new investment entry policy fixture.'}},
    {'prior_reference_per_share': '10000', 'prior_scope_label': 'forged prior value'},
])
def test_prose_only_profile_cannot_create_entry_policy_or_prior_value(prepared, policy):
    path = prepared / 'declarations/investor_report.yaml'
    profile = yaml.safe_load(path.read_text())
    path.unlink()
    request = completion._early_judgment(prepared, _run_input_sha256(prepared))
    with pytest.raises(RunbookError, match='entry policy or prior value'):
        completion._apply_judgment(prepared, request,
            response_for(request, 'PROVIDE_REPORT_PROFILE', {'profile': {**profile, **policy}}))
    assert not path.exists()
