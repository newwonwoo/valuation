from dataclasses import replace
from threading import Barrier
import pytest
from valuation_engine.research_task_execution import ResearchContext, ResearchTask, run_research_tasks, build_research_waves

CTX = ResearchContext('issuer', '2026-09-12', 'model-v1')


def test_parallel_execution_and_branch_selective_reuse():
    tasks = (ResearchTask('a', {'n': 1}), ResearchTask('b', {'n': 2}), ResearchTask('c', {}, ('a',)))
    barrier = Barrier(2)
    cache = {}
    def runner(task, deps):
        if task.task_id in ('a', 'b'):
            barrier.wait(timeout=3)
        return {'n': task.inputs.get('n', sum(d['n'] for d in deps.values()))}
    first = run_research_tasks(tasks, runner, context=CTX, cache=cache)
    assert first.waves == (('a', 'b'), ('c',))
    second = run_research_tasks(tasks, lambda *_: pytest.fail('must reuse'), context=CTX, cache=cache)
    assert set(second.reused_task_ids) == {'a', 'b', 'c'}
    changed = (replace(tasks[0], inputs={'n': 3}), *tasks[1:])
    third = run_research_tasks(changed, lambda t, d: {'n': t.inputs.get('n', sum(v['n'] for v in d.values()))}, context=CTX, cache=cache)
    assert third.executed_task_ids == ('a', 'c')
    assert third.reused_task_ids == ('b',)
    assert third.outputs['c'] == {'n': 3}


def test_output_hash_allows_descendant_reuse_when_upstream_receipt_changes_but_value_does_not():
    tasks = (ResearchTask('a', {'n': 1}), ResearchTask('b', {}, ('a',)))
    cache = {}
    run_research_tasks(tasks, lambda *_: 5, context=CTX, cache=cache)
    changed = (replace(tasks[0], inputs={'n': 2}), tasks[1])
    run = run_research_tasks(changed, lambda *_: 5, context=CTX, cache=cache)
    assert run.executed_task_ids == ('a',)
    assert run.reused_task_ids == ('b',)


def test_integrity_context_and_external_output_mutation():
    task = (ResearchTask('a', {}),)
    cache = {}
    result = run_research_tasks(task, lambda *_: {'x': []}, context=CTX, cache=cache)
    result.outputs['a']['x'].append(1)
    assert run_research_tasks(task, lambda *_: None, context=CTX, cache=cache).outputs['a'] == {'x': []}
    newer = run_research_tasks(task, lambda *_: 2, context=replace(CTX, model_version='v2'), cache=cache)
    assert newer.executed_task_ids == ('a',)
    cache[result.fingerprints['a']]['output']['x'].append(3)
    with pytest.raises(ValueError, match='integrity'):
        run_research_tasks(task, lambda *_: None, context=CTX, cache=cache)


def test_waiting_receipts_are_not_cached():
    cache = {}
    task = (ResearchTask('a', {}),)
    kwargs = dict(context=CTX, cache=cache, cacheable=lambda x: x['status'] == 'ACCEPTED')
    run_research_tasks(task, lambda *_: {'status': 'WORK_REQUIRED'}, **kwargs)
    assert not cache
    result = run_research_tasks(task, lambda *_: {'status': 'ACCEPTED'}, **kwargs)
    assert result.executed_task_ids == ('a',)
    assert cache


@pytest.mark.parametrize('tasks', [
    (ResearchTask('a', {}, write_set=('evidence/**',)), ResearchTask('b', {}, read_set=('evidence/new.json',))),
    (ResearchTask('a', {}, write_set=('out',)), ResearchTask('b', {}, write_set=('out',))),
    (ResearchTask('a', {}, ('b',)), ResearchTask('b', {}, ('a',))),
    (ResearchTask('a', {}), ResearchTask('a', {})),
])
def test_conflicts_cycles_and_duplicates_fail(tasks):
    with pytest.raises(ValueError):
        build_research_waves(tasks)


def test_ordered_shared_writes_and_duplicate_refs():
    assert build_research_waves((ResearchTask('a', {}, write_set=('out',)), ResearchTask('b', {}, ('a',), read_set=('out',)))) == (('a',), ('b',))
    with pytest.raises(ValueError, match='duplicate'):
        ResearchTask('a', {}, depends_on=('b', 'b'))
    with pytest.raises(ValueError, match='canonical'):
        ResearchTask('a', {}, read_set=('../out',))


def test_priority_controls_dispatch_without_invalidating_cache():
    tasks = (ResearchTask('a', {}, priority=5), ResearchTask('z', {}, priority=0))
    seen = []
    cache = {}
    def runner(task, deps):
        seen.append(task.task_id)
        return task.task_id
    run_research_tasks(tasks, runner, context=CTX, cache=cache, max_workers=1)
    assert seen == ['z', 'a']
    result = run_research_tasks(tuple(replace(t, priority=-t.priority) for t in tasks), lambda *_: pytest.fail('priority does not change evidence'), context=CTX, cache=cache)
    assert set(result.reused_task_ids) == {'a', 'z'}
