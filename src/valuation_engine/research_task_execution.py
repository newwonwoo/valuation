"""Deterministic task receipts with parallel isolated research callbacks."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, MutableMapping


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _copy(value: Any) -> Any:
    return json.loads(_encoded(value))


def _hash(value: Any) -> str:
    return sha256(_encoded(value).encode()).hexdigest()


@dataclass(frozen=True)
class ResearchContext:
    target: str
    as_of: str
    model_version: str
    policy_version: str = "1"
    source_version: str = "1"

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in asdict(self).values()):
            raise ValueError("complete research context required")


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    inputs: Mapping[str, Any]
    depends_on: tuple[str, ...] = ()
    read_set: tuple[str, ...] = ()
    write_set: tuple[str, ...] = ()
    version: str = "1"
    priority: int = 0  # Smaller ranks dispatch first; excluded from semantic identity.

    def __post_init__(self):
        if not self.task_id or not self.version:
            raise ValueError("task identity and version required")
        _encoded(dict(self.inputs))
        for refs in (self.depends_on, self.read_set, self.write_set):
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate task references")
        for path in (*self.read_set, *self.write_set):
            _path(path)


@dataclass(frozen=True)
class ResearchExecution:
    outputs: Mapping[str, Any]
    fingerprints: Mapping[str, str]
    executed_task_ids: tuple[str, ...]
    reused_task_ids: tuple[str, ...]
    waves: tuple[tuple[str, ...], ...]


def _path(path: str) -> tuple[str, bool]:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError("invalid artifact path")
    prefix = path.endswith("/**")
    raw = path[:-3] if prefix else path
    parts = PurePosixPath(raw)
    if parts.is_absolute() or ".." in parts.parts or "*" in raw or str(parts) != raw or raw == ".":
        raise ValueError("paths must be canonical relative paths or prefix/**")
    return raw, prefix


def _overlap(left: str, right: str) -> bool:
    a, ap = _path(left)
    b, bp = _path(right)
    return a == b or (ap and b.startswith(a + "/")) or (bp and a.startswith(b + "/"))


def build_research_waves(tasks: tuple[ResearchTask, ...]) -> tuple[tuple[str, ...], ...]:
    by_id = {t.task_id: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate task identity")
    for task in tasks:
        if task.task_id in task.depends_on or not set(task.depends_on) <= set(by_id):
            raise ValueError("unknown or self dependency")
    done, pending, waves = set(), set(by_id), []
    while pending:
        ready = tuple(sorted((k for k in pending if set(by_id[k].depends_on) <= done), key=lambda k: (by_id[k].priority, k)))
        if not ready:
            raise ValueError("research dependency cycle")
        waves.append(ready)
        done.update(ready)
        pending.difference_update(ready)

    def ancestors(key):
        reached, pending = set(), list(by_id[key].depends_on)
        while pending:
            key = pending.pop()
            if key not in reached:
                reached.add(key)
                pending.extend(by_id[key].depends_on)
        return reached

    for index, left in enumerate(tasks):
        for right in tasks[index + 1:]:
            if left.task_id in ancestors(right.task_id) or right.task_id in ancestors(left.task_id):
                continue
            collisions = [(a, b) for a in left.write_set for b in (*right.write_set, *right.read_set)]
            collisions += [(a, b) for a in right.write_set for b in left.read_set]
            if any(_overlap(a, b) for a, b in collisions):
                raise ValueError("unordered read/write artifact conflict")
    return tuple(waves)


def run_research_tasks(
    tasks: tuple[ResearchTask, ...],
    runner: Callable[[ResearchTask, Mapping[str, Any]], Any],
    *,
    context: ResearchContext,
    cache: MutableMapping[str, dict] | None = None,
    max_workers: int = 4,
    cacheable: Callable[[Any], bool] | None = None,
) -> ResearchExecution:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    # Snapshot mutable caller inputs before any worker executes.
    tasks = tuple(ResearchTask(t.task_id, _copy(dict(t.inputs)), t.depends_on, t.read_set, t.write_set, t.version, t.priority) for t in tasks)
    waves = build_research_waves(tasks)
    by_id = {t.task_id: t for t in tasks}
    receipts = cache if cache is not None else {}
    can_cache = cacheable or (lambda output: True)
    outputs, output_hashes, fingerprints = {}, {}, {}
    executed, reused = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for wave in waves:
            futures = {}
            for key in wave:
                task = by_id[key]
                deps = {k: _copy(outputs[k]) for k in task.depends_on}
                semantic_task = asdict(task)
                semantic_task.pop("priority")
                fingerprint = _hash({"context": asdict(context), "task": semantic_task,
                                     "dependency_outputs": {k: output_hashes[k] for k in task.depends_on}})
                fingerprints[key] = fingerprint
                receipt = receipts.get(fingerprint)
                if receipt is not None:
                    if receipt.get("fingerprint") != fingerprint or receipt.get("output_hash") != _hash(receipt.get("output")):
                        raise ValueError("cached output integrity failure")
                    if can_cache(_copy(receipt["output"])):
                        outputs[key] = _copy(receipt["output"])
                        output_hashes[key] = receipt["output_hash"]
                        reused.append(key)
                        continue
                futures[key] = pool.submit(runner, task, deps)
            for key, future in futures.items():
                output = _copy(future.result())
                digest = _hash(output)
                fingerprint = fingerprints[key]
                if can_cache(_copy(output)):
                    receipts[fingerprint] = {"fingerprint": fingerprint, "output_hash": digest, "output": _copy(output)}
                outputs[key], output_hashes[key] = output, digest
                executed.append(key)
    return ResearchExecution(outputs, fingerprints, tuple(executed), tuple(reused), waves)
