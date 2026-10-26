"""Recover a prepared research run, then publish its audited deterministic replay.

Providers repair inputs in the isolated run; they never return a pass flag.
Raw filings and financial values in integration tests remain explicit fixtures.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import uuid4

import yaml

from run_kr_live import (
    RunbookError, _run_input_sha256, execute_run, publish_report_bundle,
    reuse_published_report_bundle,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _prepare(source: Path, workspace: Path, underwriting: Path) -> Path:
    """Snapshot the input authority, preserving externally referenced config files."""
    identity = {'source_input_hash': _run_input_sha256(source),
                'underwriting_sha256': _hash(underwriting)}
    marker = workspace / 'completion_inputs.json'
    prepared = workspace / 'prepared_run'
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            raise RunbookError('completion source inputs changed; use a new workspace')
        if not prepared.is_dir():
            raise RunbookError('completion prepared run is missing')
        return prepared
    if prepared.exists():
        raise RunbookError('unattested prepared run already exists')
    shutil.copytree(source, prepared, ignore=shutil.ignore_patterns('out', '__pycache__'))
    config = yaml.safe_load((source / 'run.yaml').read_text())

    def rebase(value):
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, str):
            path = (source / value).resolve()
            if path.is_file():
                try:
                    relative = path.relative_to(source)
                except ValueError:
                    return str(path)
                return str(prepared / relative)
        return value

    (prepared / 'run.yaml').write_text(yaml.safe_dump(rebase(config), allow_unicode=True))
    shutil.copyfile(underwriting, prepared / 'declarations/underwriting.yaml')
    _write(marker, identity)
    return prepared


def _audit_requests(prepared: Path) -> set[Path]:
    return set((prepared / 'out/staff_requests/audit').glob('*.request.json'))


def _requests(paths: set[Path]) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(paths)]


def _materialize_replay(paths: set[Path], prepared: Path) -> None:
    """Only this successful execution's ordered transcript becomes replay input."""
    groups: dict[str, list[tuple[int, dict]]] = {}
    sessions = set()
    for request_path in paths:
        request = json.loads(request_path.read_text())
        response_path = request_path.with_name(request_path.name.replace('.request.json', '.response.json'))
        if not response_path.is_file():
            raise RunbookError('successful run has an unanswered staff request')
        response = json.loads(response_path.read_text())
        if (request.get('role') != response.get('role') or
                request.get('prompt_sha256') != response.get('prompt_sha256') or
                sha256(request['prompt'].encode()).hexdigest() != request['prompt_sha256']):
            raise RunbookError('staff transcript identity mismatch')
        parts = request_path.name.split('.')
        sessions.add(parts[-4])
        groups.setdefault(request['role'], []).append((int(parts[-3]), {
            'schema_version': 'staff-response/v1', 'role': request['role'],
            'prompt_sha256': request['prompt_sha256'], 'response': response['response'],
        }))
    if len(sessions) > 1:
        raise RunbookError('multiple staff sessions in one execution')
    for role, turns in groups.items():
        path = prepared / 'declarations/staff' / f'{role}.json'
        path.write_text(json.dumps([turn for _, turn in sorted(turns)], ensure_ascii=False, indent=2))


def _protected_sources(source: Path, prepared: Path) -> None:
    """Input repair is not permission to alter an original filing or issuer."""
    def originals(root):
        return {str(path.relative_to(root)): _hash(path) for path in root.rglob('*')
                if path.is_file() and path.relative_to(root).parts[0] not in {'out', 'declarations'}
                and path.name != 'run.yaml' and '__pycache__' not in path.parts}
    if originals(source) != originals(prepared):
        raise RunbookError('recovery changed immutable original source files')
    original = yaml.safe_load((source / 'run.yaml').read_text())
    current = yaml.safe_load((prepared / 'run.yaml').read_text())
    for key in ('company_query', 'as_of', 'jurisdiction', 'run_id', 'filing'):
        if original.get(key) != current.get(key):
            raise RunbookError('recovery changed target or valuation identity')
    def rebase(value):
        if isinstance(value, dict):
            return {key: rebase(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, str):
            path = (source / value).resolve()
            if path.is_file():
                return str(prepared / path.relative_to(source)) if path.is_relative_to(source) else str(path)
        return value
    expected = rebase(original)
    if ({key: value for key, value in current.items() if key not in {'method', 'segments'}} !=
            {key: value for key, value in expected.items() if key not in {'method', 'segments'}}):
        raise RunbookError('recovery changed immutable source configuration or run identity')
    # Source descriptors can live in declarations or outside the run. Comparing
    # only raw/ permits rewriting a saved HTML document plus its self-declared hash.
    # Capture the original descriptor and its document bytes before calling a host.
    authority_path = prepared.parent / 'completion_source_authority.json'
    references = {}
    if original.get('public_filing_facts_path'):
        descriptor = (source / original['public_filing_facts_path']).resolve()
        copied = Path(expected['public_filing_facts_path'])
        payload = json.loads(descriptor.read_text())
        pairs = [(descriptor, copied)]
        for item in [payload.get('identity_document', {}), *payload.get('documents', [])]:
            if item.get('path'):
                pairs.append(((descriptor.parent / item['path']).resolve(),
                              (copied.parent / item['path']).resolve()))
        references = {str(target): {'original_path': str(original_path), 'sha256': _hash(original_path)}
                      for original_path, target in pairs}
    if authority_path.is_file():
        if json.loads(authority_path.read_text()) != references:
            raise RunbookError('recovery changed original source authority')
    else:
        _write(authority_path, references)
    if any(_hash(Path(path)) != receipt['sha256'] for path, receipt in references.items()):
        raise RunbookError('recovery changed immutable original source documents or descriptor')


def _recovery_input_hash(prepared: Path) -> str:
    """Economic/source inputs, not rejected response bytes or variable error prose."""
    files = {str(path.relative_to(prepared)): _hash(path) for path in prepared.rglob('*')
             if path.is_file() and path.relative_to(prepared).parts[0] != 'out'
             and not path.relative_to(prepared).as_posix().startswith('declarations/staff/')}
    return sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _source_bindings(prepared: Path) -> tuple[dict, ...]:
    paths = [prepared / 'run.yaml', prepared / 'declarations/underwriting.yaml',
             *sorted((prepared / 'raw').rglob('*'))]
    return tuple({'path': str(path), 'sha256': _hash(path),
                  'kind': 'original_source' if 'raw' in path.relative_to(prepared).parts else 'declared_input'}
                 for path in paths if path.is_file())


def _early_judgment(prepared: Path, input_hash: str):
    """Bounded supported input decisions, not an unrestricted file-patch tool."""
    from valuation_engine.host_judgment import HostJudgmentRequest, JudgmentGap
    from valuation_engine.investor_report import load_investor_report_profile
    config = yaml.safe_load((prepared / 'run.yaml').read_text())
    underwriting = yaml.safe_load((prepared / 'declarations/underwriting.yaml').read_text())
    target_id = str(underwriting.get('target_id') or config['company_query'])
    common = dict(target_id=target_id, as_of=str(config['as_of']), blocked_stage='RUN_INPUT')
    envelope = {'schema_version': 'host-judgment-response/v1',
        'request_hash': '<copy request_hash from this request>',
        'target_id': target_id, 'as_of': str(config['as_of']),
        'decision': '<one allowed action>', 'rationale': '<source-supported reasoning>',
        'counterevidence': [], 'uncertainty': '<explicit limitations>'}
    bindings = _source_bindings(prepared)
    profile = prepared / 'declarations/investor_report.yaml'
    profile_error = None
    try:
        if not profile.is_file():
            raise ValueError('investor_report.yaml is missing')
        load_investor_report_profile(profile)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        profile_error = str(exc)
    # A missing method has a real economic decision contract. Determine choices
    # from the resolved issuer's Industry DNA and actual runtime capabilities.
    if not config.get('segments') and not config.get('method'):
        from run_kr_live import (
            _build_network, _optional_classification_map_path, _resolved_target_context,
        )
        from valuation_engine.generic_kr_industry import load_kr_industry_classification
        from valuation_engine.generic_valuation_plan import SUPPORTED_EXECUTION_FAMILIES
        from valuation_engine.method_capabilities import (
            MethodKind, MethodRuntimeStatus, load_default_method_capability_registry,
        )
        try:
            classification_path = _optional_classification_map_path(prepared, config)
            network = _build_network(prepared)
            identity, _, company = _resolved_target_context(prepared, config, network)
            classification_map = (load_kr_industry_classification(classification_path)
                                  if classification_path is not None else load_kr_industry_classification())
            classification = classification_map.lookup(company.induty_code)
            archetypes = {item.value for item in classification.archetypes}
            candidates = [f'{item.archetype}/{item.method}'
                          for item in load_default_method_capability_registry().capabilities
                          if item.archetype in archetypes
                          and item.kind is MethodKind.SEGMENT_EVALUATOR
                          and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
                          and item.execution_family in SUPPORTED_EXECUTION_FAMILIES]
            detail = ('Choose an evidence-supported primary method for this issuer. '
                      'Registry permission is not proof of evidence completeness; '
                      'canonical compilation and audit still apply.')
            if not candidates:
                raise ValueError('no implemented primary segment evaluator for the resolved Industry DNA')
        except (ValueError, TypeError, OSError, KeyError) as exc:
            candidates = []
            detail = f'No safe method choices can be exposed: {exc}'
        return HostJudgmentRequest.build(
            gap=JudgmentGap(cause_code='METHOD_INPUT_MISSING', judgment_kind='METHOD_SELECTION',
                            detail=detail + ' Select only a listed primary method; warranted PER is never a primary evaluator.',
                            gap_type='METHOD_SELECTION', **common),
            source_bindings=bindings,
            allowed_actions=('SELECT_METHOD', 'UNRESOLVED') if candidates else ('UNRESOLVED',),
            response_schema={**envelope, 'proposal': {'method': {'enum': candidates},
                'evidence': [{'path': 'bound source path', 'sha256': 'bound source digest',
                              'quote': 'verbatim source excerpt'}]}},
            input_hash=input_hash, validator_id='completion-method-selection/v1',
            write_targets=('run.yaml:method',))
    if profile_error is not None:
        return HostJudgmentRequest.build(
            gap=JudgmentGap(cause_code='INVESTOR_PROFILE_INPUT_REQUIRED',
                judgment_kind='REPORT_PROFILE', detail=profile_error +
                    '. Supply investor_report/v2 prose consistent with current inputs; '
                    'no new value, probability, source or audit claims. Allowed existing source URLs: '
                    + json.dumps(sorted(_known_source_urls(prepared)), ensure_ascii=False),
                gap_type='MISSING_INPUT', **common), source_bindings=bindings,
            allowed_actions=('PROVIDE_REPORT_PROFILE', 'UNRESOLVED'),
            response_schema={**envelope, 'proposal': {'profile': {
                'schema_version': 'investor_report/v2',
                'conclusion': '<at least 20 characters, conditional investment conclusion>',
                'investment_points': [{key: '<source-supported prose; one to three rows>'
                    for key in ('title', 'content', 'evidence', 'valuation_link', 'falsifier')}],
                'valuation_method': '<current approved method>', 'method_rationale': '<economic rationale>',
                'major_assumptions': '<current bound inputs; no invented outputs>',
                'valuation_exclusions': '<known exclusions or explicit none>',
                'scenario_conditions': [{'scenario_id': scenario, 'condition': '<driver conditions>',
                    'valuation_effect': '<conditional direction, not invented result>'}
                    for scenario in ('Down', 'Base', 'Bull')],
                'sensitivity_watch': [{key: '<one to five source-supported rows>'
                    for key in ('driver', 'downside', 'upside', 'monitor')}],
                'segment_notes': [{'segment_id': '<real business unit>', 'content': '<method>',
                                   'note': '<scope/limitations>'}],
                'risks': ['<three to five material risks>'],
                'conditions': {key: '<observable decision-change conditions>'
                               for key in ('upside', 'downside', 'actionable')},
                'sources': [{'source_type': '<공시|IR|신용평가|시장가격|산업자료|비교기업 공시|증권사 조사단서>',
                             'label': '<source title>', 'url': '<one existing bound URL>'}]}}},
            input_hash=input_hash, validator_id='completion-investor-profile/v1',
            write_targets=('declarations/investor_report.yaml',))
    return None


def _known_source_urls(prepared: Path) -> set[str]:
    """Only existing source identities may enter a typed presentation repair."""
    import re
    urls = set()
    for path in (prepared / 'declarations').rglob('*'):
        if not path.is_file() or path.name == 'investor_report.yaml':
            continue
        for match in re.findall(r'https://[^\s<>"\']+', path.read_text(errors='replace')):
            urls.add(match.rstrip('),;'))
    listing = prepared / 'raw/list.json'
    if listing.is_file():
        for row in json.loads(listing.read_text()).get('list', []):
            receipt = str(row.get('rcept_no', ''))
            if receipt.isdigit():
                urls.add('https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt)
    return urls


def _apply_judgment(prepared: Path, request, response: dict) -> bool:
    from valuation_engine.host_judgment import parse_host_judgment_response
    from valuation_engine.investor_report import load_investor_report_profile
    if request.to_dict()['input_hash'] != _run_input_sha256(prepared):
        raise RunbookError('judgment input hash is stale')
    accepted = parse_host_judgment_response(response, request)
    if accepted['decision'] == 'UNRESOLVED':
        return False
    proposal = accepted['proposal']
    contract = request.to_dict()
    if accepted['decision'] == 'SELECT_METHOD':
        if set(proposal) != {'method', 'evidence'}:
            raise RunbookError('method proposal permits only method and source-bound evidence')
        if proposal['method'] not in contract['response_schema']['proposal']['method']['enum']:
            raise RunbookError('method is not an allowed primary runtime candidate')
        if not isinstance(proposal['evidence'], list) or not proposal['evidence']:
            raise RunbookError('method selection requires source-bound evidence')
        bindings = {item['path']: item for item in contract['source_bindings']}
        for evidence in proposal['evidence']:
            if not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256', 'quote'}:
                raise RunbookError('invalid method source evidence')
            binding = bindings.get(evidence['path'])
            quote = evidence['quote']
            if (binding is None or evidence['sha256'] != binding['sha256']
                    or _hash(Path(evidence['path'])) != binding['sha256']
                    or not isinstance(quote, str) or len(quote.strip()) < 5
                    or quote not in Path(evidence['path']).read_text(errors='replace')):
                raise RunbookError('method evidence source/hash/quote mismatch')
        path = prepared / 'run.yaml'
        config = yaml.safe_load(path.read_text())
        config['method'] = proposal['method']
        path.write_text(yaml.safe_dump(config, allow_unicode=True))
    elif accepted['decision'] == 'PROVIDE_REPORT_PROFILE':
        if set(proposal) != {'profile'} or not isinstance(proposal['profile'], dict):
            raise RunbookError('profile proposal permits only a typed profile')
        profile = proposal['profile']
        current_path = prepared / 'declarations/investor_report.yaml'
        existing = yaml.safe_load(current_path.read_text()) if current_path.is_file() else {}
        existing = existing if isinstance(existing, dict) else {}
        for field in ('entry_rule', 'prior_reference_per_share', 'prior_scope_label'):
            if (field in profile) != (field in existing) or profile.get(field) != existing.get(field):
                raise RunbookError('prose-only profile judgment cannot change entry policy or prior value claims')
        known = _known_source_urls(prepared)
        if any(not isinstance(row, dict) or row.get('url') not in known
               for row in profile.get('sources', ())):
            raise RunbookError('report profile introduced an unbound source identity')
        # Validate before replacing a canonical declaration; reject unknown fields
        # and malformed scenarios, claims, entry rules via the existing contract.
        candidate = prepared / 'out/investor_report.candidate.yaml'
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(yaml.safe_dump(profile, allow_unicode=True))
        load_investor_report_profile(candidate)
        candidate.replace(prepared / 'declarations/investor_report.yaml')
    else:
        raise RunbookError('unsupported completion judgment decision')
    _write(prepared / 'declarations/judgment_receipts' / f'{request.request_hash}.json',
           {'request': contract, 'response': accepted})
    return True


def _handoff_fingerprint(prepared: Path, workspace: Path) -> str:
    """Staff responses are output handoffs until validated, not run inputs."""
    paths = [*sorted((prepared / 'out/staff_requests').glob('*.response.json')),
             *sorted((workspace / 'judgment_responses').glob('*.json'))]
    return sha256(json.dumps({str(path): _hash(path) for path in paths},
                             sort_keys=True).encode()).hexdigest()


def _stage_scope_response(prepared: Path, typed: dict, staff_requests: list, response) -> None:
    """Adapt the common host envelope to the existing prompt-bound staff channel.

    This validates only the envelope. The canonical scope loader owns substantive
    source/period/perimeter validation and is the sole issuer of a scope receipt.
    """
    from valuation_engine.host_judgment import (
        HostJudgmentRequest, JudgmentGap, parse_host_judgment_response,
    )
    request = HostJudgmentRequest.build(gap=JudgmentGap(**typed['gap']),
        source_bindings=tuple(typed['source_bindings']), allowed_actions=tuple(typed['allowed_actions']),
        response_schema=typed['response_schema'], input_hash=typed['input_hash'],
        validator_id=typed['validator_id'], write_targets=tuple(typed['write_targets']),
        resume_entrypoint=typed['resume_entrypoint'])
    if request.request_hash != typed['request_hash'] or request.gap.judgment_kind != 'company_scope':
        raise RunbookError('unsupported or corrupted staff judgment request')
    parsed = parse_host_judgment_response(response, request)
    matching = [item for item in staff_requests if item['role'] == 'company_scope_analyst'
                and item['prompt'] == request.prompt()
                and item['prompt_sha256'] == sha256(item['prompt'].encode()).hexdigest()]
    if len(matching) != 1:
        raise RunbookError('scope judgment lacks a unique bound staff request')
    staff = matching[0]
    target = prepared / 'declarations/staff/responses' / f"{staff['role']}.{staff['prompt_sha256']}.json"
    if target.resolve() != Path(staff['response_path']).resolve():
        raise RunbookError('staff response path is outside its declared role handoff')
    _write(target, {'schema_version': 'staff-response/v1', 'role': staff['role'],
                   'prompt_sha256': staff['prompt_sha256'], 'response': parsed})


def complete_research_report(run_dir, workspace, underwriting_path, *, staff_mode='assisted',
                             recovery_provider=None, max_rounds=10) -> dict:
    """Return COMPLETED with immutable report paths, or a resumable WORK_REQUIRED.

    recovery_provider(gap) may repair files under gap['prepared_run_dir']. Return
    values convey no authority. Every changed input goes through the real runner.
    The original run is never mutated. No external publication/network is done.
    """
    if staff_mode not in {'replay', 'assisted', 'live'}:
        raise ValueError('unsupported staff mode')
    if not 1 <= max_rounds <= 20:
        raise ValueError('max_rounds must be between 1 and 20')
    source, workspace, underwriting = map(lambda p: Path(p).resolve(),
                                          (run_dir, workspace, underwriting_path))
    workspace.mkdir(parents=True, exist_ok=True)
    # Keep the coordinator single-writer even when research runs in parallel.
    import fcntl
    with (workspace / 'completion.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _complete(source, workspace, underwriting, staff_mode,
                         recovery_provider, max_rounds)


def _complete(source, workspace, underwriting, staff_mode, recovery_provider, max_rounds):
    prepared = _prepare(source, workspace, underwriting)
    _protected_sources(source, prepared)
    published = reuse_published_report_bundle(prepared, output_dir=workspace / 'published')
    if published:
        return {'status': 'COMPLETED', 'prepared_run_dir': str(prepared), **published}
    invocation = uuid4().hex
    ledger_path = workspace / 'completion_state.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {
        'schema_version': 'completion-recovery-state/v1', 'attempts': {}, 'executions': 0}
    for index in range(max_rounds):
        _protected_sources(source, prepared)
        before_hash = _run_input_sha256(prepared)
        handoff_hash = _handoff_fingerprint(prepared, workspace)
        judgment = _early_judgment(prepared, before_hash)
        prior = ledger.get('last_gap')
        same_state = (prior is not None and prior.get('input_sha256') == before_hash
                      and ledger.get('handoff_sha256') == handoff_hash)
        if same_state and ledger.get('unchanged_response'):
            return prior
        if same_state and prior.get('recovery_allowed') is False:
            return prior
        before_audit = _audit_requests(prepared)
        if same_state:
            # Waiting for a host answer is not another canonical execution.
            reached, stopped, reason, result = (), prior['blocked_stage'], prior['reason'], None
            new_audit = set()
        elif judgment is not None:
            reached, stopped, reason, result = (), 'RUN_INPUT', judgment.gap.detail, None
            new_audit = set()
        else:
            ledger['executions'] += 1
            _write(ledger_path, ledger)
            try:
                reached, stopped, reason, result = execute_run(prepared,
                    state_root=str(workspace / 'state' / f'{invocation}-{index}'), staff_mode=staff_mode)
            except (RunbookError, ValueError, TypeError, OSError, KeyError) as exc:
                reached, stopped, reason, result = (), 'RUN_INPUT', str(exc), None
            if _run_input_sha256(prepared) != before_hash:
                raise RunbookError('run inputs changed during execution; result cannot be published')
            new_audit = _audit_requests(prepared) - before_audit
        if (result is not None and
                result.data.get('host_judgment_status') == 'ACCEPTED_REENTRY_REQUIRED'):
            # The typed adapter validates this proposal, but only a fresh canonical
            # run may use it. The next loader revalidates against original sources.
            receipt = result.data.get('host_judgment_receipt')
            from valuation_engine.host_judgment import judgment_hash
            if (not isinstance(receipt, dict) or
                    receipt.get('schema_version') != 'company-scope-receipt/v1' or
                    receipt.get('request') != result.data.get('host_judgment_request') or
                    receipt.get('request_hash') != receipt.get('request', {}).get('request_hash') or
                    receipt.get('response', {}).get('request_hash') != receipt.get('request_hash') or
                    receipt.get('receipt_hash') != judgment_hash(
                        {key: value for key, value in receipt.items() if key != 'receipt_hash'})):
                raise RunbookError('invalid company scope receipt candidate')
            final = prepared / 'declarations/company_scope_receipt.json'
            if final.is_file() and json.loads(final.read_text()) == receipt:
                raise RunbookError('company scope reentry proposed an unchanged receipt')
            _write(prepared / 'out/company_scope_receipt.candidate.json', receipt)
            _write(final, receipt)
            _write(workspace / 'history' / f'{invocation}-{index}-scope-accepted.json', receipt)
            ledger.pop('last_gap', None)
            ledger['unchanged_response'] = False
            _write(ledger_path, ledger)
            continue
        if result is not None and result.completed and stopped is None:
            if new_audit:
                _materialize_replay(new_audit, prepared)
                replay_hash = _run_input_sha256(prepared)
                reached, stopped, reason, result = execute_run(prepared,
                    state_root=str(workspace / 'state' / f'{invocation}-{index}-replay'), staff_mode='replay')
                if _run_input_sha256(prepared) != replay_hash:
                    raise RunbookError('replay inputs changed during execution')
            if stopped is None and result.completed:
                valuation = result.data.get('generic_valuation_result')
                scope = getattr(getattr(valuation, 'scope', None), 'value', '')
                if scope == 'PARTIAL_INTRINSIC' or getattr(valuation, 'unvalued_segments', ()):
                    stopped, reason = 'VALUATION_SCOPE', '전체 기업가치에 포함되지 않은 사업부의 추정과 계산을 보완해야 합니다.'
                elif _hash(prepared / 'declarations/underwriting.yaml') != _hash(source / 'declarations/underwriting.yaml'):
                    review_path = prepared / 'declarations/report_review.json'
                    profile_path = prepared / 'declarations/investor_report.yaml'
                    try:
                        review = json.loads(review_path.read_text()) if review_path.is_file() else {}
                    except (ValueError, OSError):
                        review = {}
                    if not isinstance(review, dict):
                        review = {}
                    if (review.get('underwriting_sha256') != _hash(prepared / 'declarations/underwriting.yaml') or
                            not profile_path.is_file() or review.get('profile_sha256') != _hash(profile_path) or
                            len(str(review.get('rationale', '')).strip()) < 10):
                        stopped, reason = 'REPORT_PROFILE_REVIEW', '변경된 추정값과 보고서 설명의 일치 여부를 검토하고 declarations/report_review.json에 근거를 기록하세요.'
            if stopped is None and result is not None and result.completed:
                try:
                    published = publish_report_bundle(prepared, result, output_dir=workspace / 'published')
                except (RunbookError, ValueError, TypeError, OSError) as exc:
                    stopped, reason = 'FINAL_REPORT_PUBLICATION', str(exc)
                else:
                    completion = {'status': 'COMPLETED', 'prepared_run_dir': str(prepared),
                        'reached_stages': list(reached), **published}
                    _write(workspace / 'history' / f'{invocation}-{index}-completed.json', completion)
                    return completion
        gap = {'schema_version': 'valuation-completion-gap/v1', 'status': 'WORK_REQUIRED',
            'prepared_run_dir': str(prepared), 'blocked_stage': stopped or 'INCOMPLETE_RUN',
            'reason': reason, 'round': index + 1, 'input_sha256': _run_input_sha256(prepared),
            'staff_requests': prior['staff_requests'] if same_state else _requests(new_audit),
            'strategy': ['공시와 기업 자료를 추가 조사', '산업 자료와 증권사 리포트에서 단서 조사',
                '비교 기업의 동일 기준 이익률을 근거와 함께 조정', '근거 있는 범위와 시나리오로 추정 후 재계산'],
            'instructions': '입력·근거·역할 답변을 보완하세요. 감사 결과나 상태를 덮어쓰지 마세요.'}
        gap['underwriting_sha256'] = _hash(prepared / 'declarations/underwriting.yaml')
        profile = prepared / 'declarations/investor_report.yaml'
        gap['profile_sha256'] = _hash(profile) if profile.is_file() else None
        typed = judgment.to_dict() if judgment else (
            result.data.get('host_judgment_request') if result is not None else
            prior.get('host_judgment_request') if same_state else None)
        if typed:
            gap['host_judgment_request'] = typed
            request_path = workspace / 'judgment_requests' / f"{typed['request_hash']}.json"
            if request_path.is_file() and json.loads(request_path.read_text()) != typed:
                raise RunbookError('immutable judgment request changed')
            _write(request_path, typed)
            gap['judgment_request_path'] = str(request_path)
            gap['judgment_response_path'] = str(workspace / 'judgment_responses' / f"{typed['request_hash']}.json")
            gap['instructions'] = ('Return the exact typed host response to judgment_response_path, '
                'or answer the source-bound staff request for company scope. No file patches or PASS flags.')
        else:
            gap['judgment_support'] = 'EXISTING_STAFF_OR_INPUT_REPAIR_ONLY'
            if not gap['staff_requests']:
                gap['unsupported_judgment_reason'] = 'No typed decision validator is registered for this gap; canonical input repair or capability work is required.'
        budget_identity = ({'judgment': typed['request_hash']} if typed else {
            'stage': gap['blocked_stage'], 'input': _recovery_input_hash(prepared)})
        gap_key = sha256(json.dumps(budget_identity,
                                  sort_keys=True).encode()).hexdigest()
        attempts = ledger['attempts'].get(gap_key, 0)
        gap['recovery_attempts'] = attempts
        gap['active_strategy'] = gap['strategy'][min(attempts, len(gap['strategy']) - 1)]
        ledger['last_gap'], ledger['handoff_sha256'] = gap, _handoff_fingerprint(prepared, workspace)
        ledger['unchanged_response'] = False
        def save_gap():
            _write(workspace / 'history' / f'{invocation}-{index}-gap.json', gap)
            _write(workspace / 'completion_request.json', gap)
            _write(ledger_path, ledger)
        save_gap()
        if result is not None and result.data.get('host_judgment_status') == 'HARD_BLOCKED':
            gap['recovery_allowed'] = False
            gap['instructions'] = '원문·결합정보 무결성 오류입니다. 모델 판단이나 PASS로 우회할 수 없습니다. 원래 입력과 오류를 확인하세요.'
            gap['recovery_note'] = 'Source integrity failure; host discretion and automatic input repair withheld.'
            save_gap()
            return gap
        scope_judgment = bool(typed and typed['gap']['judgment_kind'] == 'company_scope')
        response_path = Path(gap['judgment_response_path']) if judgment or scope_judgment else None
        supplied = response_path is not None and response_path.is_file()
        if attempts >= max_rounds:
            gap['recovery_note'] = '같은 원인·입력의 보완 시도 한도에 도달했습니다. 새 근거 또는 입력 변경이 필요합니다.'
            save_gap()
            return gap
        if recovery_provider is None and not supplied:
            return gap
        snapshot = _run_input_sha256(prepared)
        handoffs = _handoff_fingerprint(prepared, workspace)
        ledger['attempts'][gap_key] = attempts + 1
        gap['recovery_attempts'] = attempts + 1
        save_gap()  # debit the persistent budget before invoking host code
        response = None
        try:
            if supplied:
                from valuation_engine.host_judgment import load_judgment_json
                response = load_judgment_json(response_path.read_text())
            elif recovery_provider is not None:
                response = recovery_provider(gap)
            _protected_sources(source, prepared)
            if judgment is not None:
                if _run_input_sha256(prepared) != snapshot:
                    raise RunbookError('typed judgment provider changed run inputs instead of returning a proposal')
                if response is None and response_path.is_file():
                    from valuation_engine.host_judgment import load_judgment_json
                    response = load_judgment_json(response_path.read_text())
                if response is not None:
                    _apply_judgment(prepared, judgment, response)
            elif scope_judgment:
                if response is None and response_path.is_file():
                    from valuation_engine.host_judgment import load_judgment_json
                    response = load_judgment_json(response_path.read_text())
                if response is not None:
                    _stage_scope_response(prepared, typed, gap['staff_requests'], response)
        except (ValueError, TypeError, KeyError) as exc:
            _protected_sources(source, prepared)
            if (judgment is None and not scope_judgment) or _run_input_sha256(prepared) != snapshot:
                raise
            gap['response_error'] = str(exc)
        if _run_input_sha256(prepared) == snapshot and _handoff_fingerprint(prepared, workspace) == handoffs:
            gap['recovery_note'] = '보완 입력이 변경되지 않았습니다. 동일 응답 재시도 한도에 도달했으며 새 근거·답변을 기다립니다.'
            ledger['unchanged_response'] = True
            save_gap()
            return gap
        ledger['unchanged_response'] = False
        save_gap()
        if index + 1 == max_rounds:
            gap['recovery_note'] = '이번 실행의 보완 시도 한도에 도달했습니다. 변경된 입력을 같은 작업공간에서 재실행하세요.'
            save_gap()
            return gap
    raise RunbookError('canonical reentry budget reached; resume the saved workspace')
