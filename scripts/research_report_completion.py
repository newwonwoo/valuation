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
    published = reuse_published_report_bundle(prepared, output_dir=workspace / 'published')
    if published:
        return {'status': 'COMPLETED', 'prepared_run_dir': str(prepared), **published}
    invocation = uuid4().hex
    identity_path = prepared / 'run.yaml'
    config = yaml.safe_load((source / 'run.yaml').read_text())
    identity = {key: config.get(key) for key in ('company_query', 'as_of', 'jurisdiction', 'run_id')}
    for index in range(max_rounds):
        current = yaml.safe_load(identity_path.read_text())
        if any(current.get(key) != value for key, value in identity.items()):
            raise RunbookError('recovery changed target or valuation identity')
        before_hash = _run_input_sha256(prepared)
        before_audit = _audit_requests(prepared)
        try:
            reached, stopped, reason, result = execute_run(prepared,
                state_root=str(workspace / 'state' / f'{invocation}-{index}'), staff_mode=staff_mode)
        except (RunbookError, ValueError, TypeError, OSError) as exc:
            reached, stopped, reason, result = (), 'RUN_INPUT', str(exc), None
        if _run_input_sha256(prepared) != before_hash:
            raise RunbookError('run inputs changed during execution; result cannot be published')
        new_audit = _audit_requests(prepared) - before_audit
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
            'reason': reason, 'round': index + 1, 'input_sha256': before_hash,
            'staff_requests': _requests(new_audit),
            'strategy': ['공시와 기업 자료를 추가 조사', '산업 자료와 증권사 리포트에서 단서 조사',
                '비교 기업의 동일 기준 이익률을 근거와 함께 조정', '근거 있는 범위와 시나리오로 추정 후 재계산'],
            'instructions': '입력·근거·역할 답변을 보완하세요. 감사 결과나 상태를 덮어쓰지 마세요.'}
        gap['underwriting_sha256'] = _hash(prepared / 'declarations/underwriting.yaml')
        profile = prepared / 'declarations/investor_report.yaml'
        gap['profile_sha256'] = _hash(profile) if profile.is_file() else None
        gap['active_strategy'] = gap['strategy'][min(index, len(gap['strategy']) - 1)]
        _write(workspace / 'history' / f'{invocation}-{index}-gap.json', gap)
        _write(workspace / 'completion_request.json', gap)
        if recovery_provider is None or index + 1 == max_rounds:
            if recovery_provider is not None:
                gap['recovery_note'] = '보완 시도 한도에 도달했습니다. 입력을 보완한 뒤 재개할 수 있습니다.'
            return gap
        snapshot = _run_input_sha256(prepared)
        recovery_provider(gap)
        if _run_input_sha256(prepared) == snapshot:
            gap = {**gap, 'recovery_note': '보완 입력이 변경되지 않았습니다.'}
            continue
    raise AssertionError('unreachable')
