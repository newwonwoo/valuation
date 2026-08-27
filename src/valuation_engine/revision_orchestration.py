from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable

from .unit_contracts import UnitContractRegistry


class RevisionScope(str, Enum):
    SOURCE_EVIDENCE = "source_evidence"
    INVESTMENT_THESIS = "investment_thesis"
    MODEL_INPUT = "model_input"
    VALUATION_LOGIC = "valuation_logic"
    REPORT_CONTENT = "report_content"
    REPORT_LAYOUT = "report_layout"
    CONTRACT_GOVERNANCE = "contract_governance"
    GENERATED_ARTIFACT = "generated_artifact"


class RevisionClaimTreatment(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    VALUED = "valued"
    REFERENCE_ONLY = "reference_only"


class RevisionTaskStatus(str, Enum):
    PASS = "pass"
    FAILED = "failed"
    BLOCKED = "blocked"


_SCOPE_REQUIRED_UNITS: dict[RevisionScope, tuple[str, ...]] = {
    RevisionScope.SOURCE_EVIDENCE: (
        "PRIMARY_EVIDENCE_COLLECTION",
        "EVIDENCE_LEDGER",
    ),
    RevisionScope.INVESTMENT_THESIS: (
        "ROCKET_INSIGHT_SCAN",
        "BLIND_RED_TEAM_B",
    ),
    RevisionScope.MODEL_INPUT: (
        "EVIDENCE_TO_ASSUMPTION_BRIDGE",
        "ASSUMPTION_COMPILER",
        "SCENARIO_ENGINE",
        "DETERMINISTIC_VALUATION",
        "AUDIT_GATE",
        "INTRINSIC_FREEZE",
        "FINAL_REPORT",
    ),
    RevisionScope.VALUATION_LOGIC: (
        "DETERMINISTIC_VALUATION",
        "AUDIT_GATE",
        "INTRINSIC_FREEZE",
        "FINAL_REPORT",
    ),
    RevisionScope.REPORT_CONTENT: ("FINAL_REPORT",),
    RevisionScope.REPORT_LAYOUT: ("FINAL_REPORT",),
    RevisionScope.CONTRACT_GOVERNANCE: (
        "DOCTRINE_CONSTITUTION",
        "VALUATION_CONTROL_PLANE",
        "AUDIT_GATE",
    ),
    RevisionScope.GENERATED_ARTIFACT: ("FINAL_REPORT",),
}

_VALUED_CLAIM_UNITS = _SCOPE_REQUIRED_UNITS[RevisionScope.MODEL_INPUT]


@dataclass(frozen=True)
class RevisionClause:
    clause_id: str
    desired_outcome: str
    scopes: tuple[RevisionScope, ...]
    root_unit_ids: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    material_report_claim: bool = False
    claim_treatment: RevisionClaimTreatment = RevisionClaimTreatment.NOT_APPLICABLE

    def __post_init__(self) -> None:
        if not self.clause_id or not self.desired_outcome.strip():
            raise ValueError("revision clause requires identity and desired outcome")
        if not self.scopes or not self.root_unit_ids or not self.acceptance_criteria:
            raise ValueError(
                "revision clause requires scopes, root units and acceptance criteria"
            )
        if self.material_report_claim:
            if self.claim_treatment is RevisionClaimTreatment.NOT_APPLICABLE:
                raise ValueError("material report claim requires a valuation treatment")
            if self.claim_treatment is RevisionClaimTreatment.REFERENCE_ONLY:
                raise ValueError("REFERENCE_ONLY claim cannot lead a material report change")


@dataclass(frozen=True)
class RevisionTask:
    task_id: str
    clause_ids: tuple[str, ...]
    owner: str
    unit_ids: tuple[str, ...]
    read_set: tuple[str, ...]
    write_set: tuple[str, ...]
    depends_on: tuple[str, ...]
    output_ids: tuple[str, ...]
    validators: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.task_id or not self.owner:
            raise ValueError("revision task requires identity and owner")
        if not self.clause_ids or not self.unit_ids:
            raise ValueError("revision task requires clauses and units")
        if not self.output_ids or not self.validators:
            raise ValueError("revision task requires outputs and validators")
        if len(self.write_set) != len(set(self.write_set)):
            raise ValueError(f"duplicate write target in {self.task_id}")


@dataclass(frozen=True)
class RevisionWave:
    ordinal: int
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class RevisionPlan:
    request_id: str
    base_revision: str
    clauses: tuple[RevisionClause, ...]
    tasks: tuple[RevisionTask, ...]
    waves: tuple[RevisionWave, ...]
    selected_unit_ids: tuple[str, ...]
    skipped_unit_ids: tuple[str, ...]
    plan_hash: str

    def task(self, task_id: str) -> RevisionTask:
        try:
            return next(item for item in self.tasks if item.task_id == task_id)
        except StopIteration as exc:
            raise KeyError(task_id) from exc


@dataclass(frozen=True)
class RevisionTaskResult:
    plan_hash: str
    base_revision: str
    task_id: str
    status: RevisionTaskStatus
    actual_write_set: tuple[str, ...]
    output_ids: tuple[str, ...]
    completed_validators: tuple[str, ...]


@dataclass(frozen=True)
class RevisionExecutionAudit:
    merge_ready: bool
    missing_task_ids: tuple[str, ...]
    failed_task_ids: tuple[str, ...]
    unplanned_write_paths: tuple[str, ...]
    stale_result_task_ids: tuple[str, ...]


def _required_units_for_clause(clause: RevisionClause) -> tuple[str, ...]:
    selected: set[str] = set()
    selected.update(clause.root_unit_ids)
    for scope in clause.scopes:
        selected.update(_SCOPE_REQUIRED_UNITS[scope])
    if clause.claim_treatment is RevisionClaimTreatment.VALUED:
        selected.update(_VALUED_CLAIM_UNITS)
    return tuple(sorted(selected))


def required_unit_ids(clauses: Iterable[RevisionClause]) -> tuple[str, ...]:
    """Return the smallest canonical unit set required by the declared clauses."""

    return tuple(
        sorted(
            {
                unit_id
                for clause in clauses
                for unit_id in _required_units_for_clause(clause)
            }
        )
    )


def _task_descendants(tasks: tuple[RevisionTask, ...], roots: set[str]) -> set[str]:
    children: dict[str, set[str]] = {task.task_id: set() for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            children[dependency].add(task.task_id)
    reached = set(roots)
    queue = list(sorted(roots))
    while queue:
        current = queue.pop(0)
        for child in sorted(children[current]):
            if child in reached:
                continue
            reached.add(child)
            queue.append(child)
    return reached


def _depends_transitively(
    task_id: str,
    target_dependency: str,
    task_map: dict[str, RevisionTask],
) -> bool:
    seen: set[str] = set()
    queue = list(task_map[task_id].depends_on)
    while queue:
        current = queue.pop(0)
        if current == target_dependency:
            return True
        if current in seen:
            continue
        seen.add(current)
        queue.extend(task_map[current].depends_on)
    return False


def build_parallel_waves(tasks: Iterable[RevisionTask]) -> tuple[RevisionWave, ...]:
    rows = tuple(tasks)
    task_map = {task.task_id: task for task in rows}
    if len(task_map) != len(rows):
        raise ValueError("revision task IDs must be unique")
    for task in rows:
        unknown = set(task.depends_on) - set(task_map)
        if unknown:
            raise ValueError(
                f"unknown task dependencies for {task.task_id}: {sorted(unknown)}"
            )
        if task.task_id in task.depends_on:
            raise ValueError(f"revision task cannot depend on itself: {task.task_id}")

    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            overlap = set(left.write_set).intersection(right.write_set)
            ordered = _depends_transitively(
                left.task_id, right.task_id, task_map
            ) or _depends_transitively(right.task_id, left.task_id, task_map)
            if overlap and not ordered:
                raise ValueError(
                    "unordered revision tasks have overlapping write sets: "
                    f"{left.task_id}, {right.task_id}: {sorted(overlap)}"
                )

    pending = set(task_map)
    completed: set[str] = set()
    waves: list[RevisionWave] = []
    while pending:
        ready = tuple(
            sorted(
                task_id
                for task_id in pending
                if set(task_map[task_id].depends_on).issubset(completed)
            )
        )
        if not ready:
            raise ValueError("revision task dependency graph contains a cycle")
        waves.append(RevisionWave(len(waves) + 1, ready))
        completed.update(ready)
        pending.difference_update(ready)
    return tuple(waves)


def _plan_hash_payload(
    *,
    request_id: str,
    base_revision: str,
    clauses: tuple[RevisionClause, ...],
    tasks: tuple[RevisionTask, ...],
    waves: tuple[RevisionWave, ...],
) -> str:
    payload = {
        "request_id": request_id,
        "base_revision": base_revision,
        "clauses": [asdict(item) for item in clauses],
        "tasks": [asdict(item) for item in tasks],
        "waves": [asdict(item) for item in waves],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.value if isinstance(value, Enum) else str(value),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_revision_plan(
    *,
    request_id: str,
    base_revision: str,
    clauses: Iterable[RevisionClause],
    tasks: Iterable[RevisionTask],
    registry: UnitContractRegistry,
) -> RevisionPlan:
    if not request_id or not base_revision:
        raise ValueError("revision plan requires request ID and base revision")
    clause_rows = tuple(clauses)
    task_rows = tuple(tasks)
    clause_ids = tuple(item.clause_id for item in clause_rows)
    if not clause_rows or len(clause_ids) != len(set(clause_ids)):
        raise ValueError("revision plan requires unique clauses")
    if not task_rows:
        raise ValueError("revision plan requires tasks")

    registry.validate()
    known_units = {unit.unit_id for unit in registry.units}
    for clause in clause_rows:
        unknown_roots = set(clause.root_unit_ids) - known_units
        if unknown_roots:
            raise ValueError(
                f"unknown root units for {clause.clause_id}: {sorted(unknown_roots)}"
            )
    for task in task_rows:
        unknown_clauses = set(task.clause_ids) - set(clause_ids)
        unknown_units = set(task.unit_ids) - known_units
        if unknown_clauses or unknown_units:
            raise ValueError(
                f"invalid revision task mapping for {task.task_id}: "
                f"clauses={sorted(unknown_clauses)}, units={sorted(unknown_units)}"
            )

    uncovered = {
        clause_id
        for clause_id in clause_ids
        if not any(clause_id in task.clause_ids for task in task_rows)
    }
    if uncovered:
        raise ValueError(f"revision clauses lack tasks: {sorted(uncovered)}")

    for clause in clause_rows:
        clause_units = {
            unit_id
            for task in task_rows
            if clause.clause_id in task.clause_ids
            for unit_id in task.unit_ids
        }
        missing_clause_units = set(_required_units_for_clause(clause)) - clause_units
        if missing_clause_units:
            raise ValueError(
                f"revision clause {clause.clause_id} omits required unit path: "
                + ", ".join(sorted(missing_clause_units))
            )

    required_units = required_unit_ids(clause_rows)
    selected_units = tuple(
        sorted({unit_id for task in task_rows for unit_id in task.unit_ids})
    )
    missing_units = set(required_units) - set(selected_units)
    if missing_units:
        raise ValueError(
            "revision plan omits required unit path: " + ", ".join(sorted(missing_units))
        )
    unexpected_units = set(selected_units) - set(required_units)
    if unexpected_units:
        raise ValueError(
            "revision plan invokes unrelated units: "
            + ", ".join(sorted(unexpected_units))
        )

    waves = build_parallel_waves(task_rows)
    digest = _plan_hash_payload(
        request_id=request_id,
        base_revision=base_revision,
        clauses=clause_rows,
        tasks=task_rows,
        waves=waves,
    )
    return RevisionPlan(
        request_id=request_id,
        base_revision=base_revision,
        clauses=clause_rows,
        tasks=task_rows,
        waves=waves,
        selected_unit_ids=selected_units,
        skipped_unit_ids=tuple(sorted(known_units - set(selected_units))),
        plan_hash=digest,
    )


def invalidate_descendants(
    plan: RevisionPlan,
    failed_task_ids: Iterable[str],
) -> tuple[str, ...]:
    roots = set(failed_task_ids)
    unknown = roots - {task.task_id for task in plan.tasks}
    if unknown:
        raise ValueError(f"unknown failed tasks: {sorted(unknown)}")
    return tuple(sorted(_task_descendants(plan.tasks, roots)))


def audit_revision_execution(
    plan: RevisionPlan,
    results: Iterable[RevisionTaskResult],
    *,
    current_base_revision: str,
) -> RevisionExecutionAudit:
    result_rows = tuple(results)
    result_map = {item.task_id: item for item in result_rows}
    if len(result_map) != len(result_rows):
        raise ValueError("revision task results must be unique")
    task_map = {task.task_id: task for task in plan.tasks}
    unknown_results = set(result_map) - set(task_map)
    if unknown_results:
        raise ValueError(f"unknown revision task results: {sorted(unknown_results)}")

    missing: list[str] = []
    failed: list[str] = []
    unplanned: set[str] = set()
    stale: list[str] = []
    for task_id, task in task_map.items():
        result = result_map.get(task_id)
        if result is None:
            missing.append(task_id)
            continue
        if (
            result.plan_hash != plan.plan_hash
            or result.base_revision != plan.base_revision
            or current_base_revision != plan.base_revision
        ):
            stale.append(task_id)
        if result.status is not RevisionTaskStatus.PASS:
            failed.append(task_id)
        unplanned.update(set(result.actual_write_set) - set(task.write_set))
        if not set(task.output_ids).issubset(result.output_ids):
            failed.append(task_id)
        if not set(task.validators).issubset(result.completed_validators):
            failed.append(task_id)

    return RevisionExecutionAudit(
        merge_ready=not any((missing, failed, unplanned, stale)),
        missing_task_ids=tuple(sorted(set(missing))),
        failed_task_ids=tuple(sorted(set(failed))),
        unplanned_write_paths=tuple(sorted(unplanned)),
        stale_result_task_ids=tuple(sorted(set(stale))),
    )
