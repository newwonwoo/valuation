from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .control_plane import DoctrineCoverageEntry, StageStatus
from .unit_contracts import UnitContractRegistry, load_unit_contract_registry


_DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "unit_contract_registry.yaml"

_STATUS_PRIORITY = {
    StageStatus.SKIPPED_NOT_APPLICABLE: 10,
    StageStatus.PASS: 20,
    StageStatus.RECOVERED: 30,
    StageStatus.WARNING: 40,
    StageStatus.NOT_IMPLEMENTED: 50,
    StageStatus.RECOVERY_REQUIRED: 60,
    StageStatus.AWAITING_USER_DECISION: 70,
    StageStatus.BLOCKED: 80,
}


@dataclass(frozen=True)
class DoctrineCoverageSnapshot:
    entries: tuple[DoctrineCoverageEntry, ...]
    expected_unit_ids: tuple[str, ...]
    relevant_stages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entries or not self.expected_unit_ids:
            raise ValueError("doctrine coverage snapshot cannot be empty")
        if tuple(item.module_id for item in self.entries) != self.expected_unit_ids:
            raise ValueError("coverage entries must follow expected_unit_ids exactly")


@lru_cache(maxsize=1)
def load_default_unit_contract_registry() -> UnitContractRegistry:
    return load_unit_contract_registry(_DEFAULT_REGISTRY_PATH)


def _trace_fields(trace) -> tuple[str, StageStatus, str, bool]:
    stage = getattr(trace, "stage", None)
    status = getattr(trace, "status", None)
    rationale = getattr(trace, "rationale", None)
    blocking = bool(getattr(trace, "blocking", False))
    if not isinstance(stage, str) or not stage:
        raise ValueError("stage trace requires stage")
    if not isinstance(status, StageStatus):
        raise ValueError(f"stage trace {stage} has invalid status")
    if not isinstance(rationale, str) or not rationale:
        raise ValueError(f"stage trace {stage} requires rationale")
    return stage, status, rationale, blocking


def _aggregate_status(rows: tuple[tuple[str, StageStatus, str, bool], ...]) -> tuple[StageStatus, bool]:
    if not rows:
        raise ValueError("cannot aggregate empty stage coverage")
    if all(status is StageStatus.SKIPPED_NOT_APPLICABLE for _, status, _, _ in rows):
        return StageStatus.SKIPPED_NOT_APPLICABLE, False

    # A later explicit RECOVERED trace may resolve an earlier recoverable request in the
    # same Unit Contract (e.g. BLIND_RED_TEAM_B → RESEARCH_LOOP). It may never erase a
    # hard BLOCKED / NOT_IMPLEMENTED / AWAITING_USER_DECISION state, and it cannot erase a
    # RECOVERY_REQUIRED emitted after the recovery trace.
    last_recovered = max(
        (index for index, row in enumerate(rows) if row[1] is StageStatus.RECOVERED),
        default=-1,
    )
    effective_rows = rows
    if last_recovered >= 0:
        hard_unresolved = {
            StageStatus.BLOCKED,
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.AWAITING_USER_DECISION,
        }
        if not any(row[1] in hard_unresolved for row in rows):
            later_recovery_required = any(
                row[1] is StageStatus.RECOVERY_REQUIRED
                for row in rows[last_recovered + 1 :]
            )
            if not later_recovery_required:
                effective_rows = tuple(
                    row
                    for index, row in enumerate(rows)
                    if not (
                        index < last_recovered
                        and row[1] is StageStatus.RECOVERY_REQUIRED
                    )
                )

    status = max((row[1] for row in effective_rows), key=lambda item: _STATUS_PRIORITY[item])
    unresolved = {
        StageStatus.BLOCKED,
        StageStatus.NOT_IMPLEMENTED,
        StageStatus.RECOVERY_REQUIRED,
        StageStatus.AWAITING_USER_DECISION,
    }
    blocking = any(row[3] and row[1] in unresolved for row in effective_rows)
    return status, blocking


def build_doctrine_coverage(
    registry: UnitContractRegistry,
    *,
    relevant_stages: Iterable[str],
    stage_traces: Iterable[object],
    required_stages: Iterable[str] = (),
    prospective_pass_stages: Iterable[str] = (),
) -> DoctrineCoverageSnapshot:
    """Generate no-silent-skip coverage from the static Unit Contract map and runtime traces.

    The audit input snapshot is built from stages completed before AUDIT_GATE. The freeze
    snapshot is rebuilt after Audit and may mark INTRINSIC_VALUE_FREEZE prospectively PASS
    only while issuing the hash-bound token. The token function remains the final authority.
    """
    registry.validate()
    stages = tuple(dict.fromkeys(str(item) for item in relevant_stages if str(item)))
    if not stages:
        raise ValueError("doctrine coverage requires relevant stages")
    stage_set = set(stages)
    required = {str(item) for item in required_stages}
    prospective = {str(item) for item in prospective_pass_stages}

    trace_map: dict[str, tuple[str, StageStatus, str, bool]] = {}
    for trace in stage_traces:
        row = _trace_fields(trace)
        if row[0] in trace_map:
            raise ValueError(f"duplicate runtime trace for stage {row[0]}")
        trace_map[row[0]] = row

    entries: list[DoctrineCoverageEntry] = []
    expected: list[str] = []
    for contract in registry.units:
        contract_stages = tuple(contract.stages)
        is_global = "GLOBAL" in contract_stages
        active = tuple(stage for stage in contract_stages if stage in stage_set)
        if not is_global and not active:
            continue

        expected.append(contract.unit_id)
        if is_global:
            entries.append(
                DoctrineCoverageEntry(
                    contract.unit_id,
                    StageStatus.PASS,
                    "GLOBAL: canonical doctrine/control authority loaded for this run",
                )
            )
            continue

        rows: list[tuple[str, StageStatus, str, bool]] = []
        for stage in active:
            if stage in prospective:
                rows.append((stage, StageStatus.PASS, "freeze prerequisites are being validated atomically", False))
                continue
            trace = trace_map.get(stage)
            if trace is None:
                rows.append(
                    (
                        stage,
                        StageStatus.NOT_IMPLEMENTED,
                        "runtime stage left no trace",
                        stage in required,
                    )
                )
            else:
                rows.append(trace)
        aggregated, blocking = _aggregate_status(tuple(rows))
        rationale = " | ".join(
            f"{stage}={status.value}: {detail}" for stage, status, detail, _ in rows
        )
        entries.append(DoctrineCoverageEntry(contract.unit_id, aggregated, rationale, blocking))

    return DoctrineCoverageSnapshot(tuple(entries), tuple(expected), stages)
