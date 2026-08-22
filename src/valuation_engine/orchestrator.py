from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    IntrinsicFreezeToken,
    StageStatus,
    authorize_post_freeze,
    issue_freeze_token,
)


@dataclass(frozen=True)
class StageExecutionResult:
    status: StageStatus
    rationale: str
    outputs: dict[str, Any] = field(default_factory=dict)
    blocking: bool = False

    def __post_init__(self) -> None:
        if not self.rationale:
            raise ValueError("stage result requires rationale")
        if self.status in {StageStatus.PENDING, StageStatus.READY, StageStatus.RUNNING}:
            raise ValueError("stage adapter must return a terminal or recovery status")


@dataclass(frozen=True)
class StageTrace:
    stage: str
    status: StageStatus
    rationale: str
    blocking: bool
    output_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControlledRunResult:
    run_id: str
    execution_mode: ExecutionMode
    stage_traces: tuple[StageTrace, ...]
    data: dict[str, Any]
    blocked_reasons: tuple[str, ...]
    freeze_token: IntrinsicFreezeToken | None

    @property
    def completed(self) -> bool:
        return not self.blocked_reasons and bool(self.stage_traces)


@dataclass
class OrchestratorContext:
    run_id: str
    execution_mode: ExecutionMode
    data: dict[str, Any] = field(default_factory=dict)
    stage_traces: list[StageTrace] = field(default_factory=list)
    freeze_token: IntrinsicFreezeToken | None = None


StageAdapter = Callable[[OrchestratorContext], StageExecutionResult]

_POST_FREEZE_STAGES = {
    "STREET_REFERENCE_LOAD",
    "STREET_GAP_ANALYZER",
    "MARKET_PRICE_LOAD",
    "MARKET_COMPARE",
    "THESIS_DELTA",
    "SAVE_STATE",
    "FINAL_REPORT",
}


def load_stage_sequence(path: str | Path) -> tuple[str, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    phases = payload.get("phases", {})
    sequence = tuple(stage for stages in phases.values() for stage in stages)
    if not sequence:
        raise ValueError("control-plane stage registry has no stages")
    if len(sequence) != len(set(sequence)):
        raise ValueError("control-plane stage sequence contains duplicates")
    if "INTRINSIC_VALUE_FREEZE" not in sequence:
        raise ValueError("control-plane sequence requires INTRINSIC_VALUE_FREEZE")
    return sequence


def _coverage_from_context(context: OrchestratorContext) -> tuple[DoctrineCoverageEntry, ...]:
    raw = context.data.get("doctrine_coverage")
    if not isinstance(raw, tuple) or not all(isinstance(x, DoctrineCoverageEntry) for x in raw):
        raise ValueError("intrinsic freeze requires doctrine_coverage tuple")
    return raw


def _freeze_from_context(context: OrchestratorContext) -> IntrinsicFreezeToken:
    required = (
        "audit_passed",
        "assumption_set_hash",
        "valuation_hash",
        "audit_hash",
        "industry_snapshot_hash",
        "source_snapshot_hash",
        "expected_module_ids",
    )
    missing = tuple(key for key in required if key not in context.data)
    if missing:
        raise ValueError("intrinsic freeze missing: " + ", ".join(missing))
    expected = context.data["expected_module_ids"]
    if not isinstance(expected, tuple) or not all(isinstance(x, str) and x for x in expected):
        raise ValueError("expected_module_ids must be a non-empty string tuple")
    return issue_freeze_token(
        run_id=context.run_id,
        audit_passed=bool(context.data["audit_passed"]),
        coverage_entries=_coverage_from_context(context),
        expected_module_ids=expected,
        assumption_set_hash=str(context.data["assumption_set_hash"]),
        valuation_hash=str(context.data["valuation_hash"]),
        audit_hash=str(context.data["audit_hash"]),
        industry_snapshot_hash=str(context.data["industry_snapshot_hash"]),
        source_snapshot_hash=str(context.data["source_snapshot_hash"]),
    )


def run_controlled_workflow(
    *,
    run_id: str,
    execution_mode: ExecutionMode,
    stage_sequence: tuple[str, ...],
    adapters: dict[str, StageAdapter],
    required_stages: tuple[str, ...],
    initial_data: dict[str, Any] | None = None,
) -> ControlledRunResult:
    """Execute the canonical stage order for PRIMARY_SHADOW/LIVE_PRIMARY.

    This is an orchestration shell, not a source collector or valuation model. Each live
    capability is supplied as a stage adapter. Missing required adapters fail closed and are
    visible as NOT_IMPLEMENTED; optional non-applicable stages must return an explicit
    SKIPPED_NOT_APPLICABLE result. LEGACY_REGRESSION remains in workflow.py.
    """
    if execution_mode is ExecutionMode.LEGACY_REGRESSION:
        raise ValueError("LEGACY_REGRESSION must use the legacy workflow, not this orchestrator")
    if not run_id:
        raise ValueError("run_id is required")
    if len(stage_sequence) != len(set(stage_sequence)):
        raise ValueError("stage_sequence contains duplicates")
    unknown_required = tuple(stage for stage in required_stages if stage not in stage_sequence)
    if unknown_required:
        raise ValueError("required stages not in sequence: " + ", ".join(unknown_required))

    context = OrchestratorContext(run_id, execution_mode, dict(initial_data or {}))
    blockers: list[str] = []
    required = set(required_stages)

    for stage in stage_sequence:
        if stage in _POST_FREEZE_STAGES:
            if context.freeze_token is None:
                reason = f"{stage} requires IntrinsicFreezeToken"
                context.stage_traces.append(
                    StageTrace(stage, StageStatus.BLOCKED, reason, True)
                )
                blockers.append(reason)
                break
            authorize_post_freeze(context.freeze_token, run_id=run_id)

        if stage == "INTRINSIC_VALUE_FREEZE":
            try:
                context.freeze_token = _freeze_from_context(context)
                context.data["intrinsic_freeze_token"] = context.freeze_token
                context.stage_traces.append(
                    StageTrace(stage, StageStatus.PASS, "audit and doctrine coverage authorized intrinsic freeze", False, ("intrinsic_freeze_token",))
                )
            except Exception as exc:
                reason = f"intrinsic freeze blocked: {type(exc).__name__}: {exc}"
                context.stage_traces.append(
                    StageTrace(stage, StageStatus.BLOCKED, reason, True)
                )
                blockers.append(reason)
                break
            continue

        adapter = adapters.get(stage)
        if adapter is None:
            status = StageStatus.NOT_IMPLEMENTED
            is_blocking = stage in required
            reason = "required stage adapter is not implemented" if is_blocking else "optional stage adapter is not implemented"
            context.stage_traces.append(StageTrace(stage, status, reason, is_blocking))
            if is_blocking:
                blockers.append(f"{stage}: {reason}")
                break
            continue

        try:
            result = adapter(context)
        except Exception as exc:
            reason = f"stage adapter failed: {type(exc).__name__}: {exc}"
            context.stage_traces.append(StageTrace(stage, StageStatus.BLOCKED, reason, True))
            blockers.append(f"{stage}: {reason}")
            break

        if result.outputs:
            overlap = set(result.outputs).intersection(context.data)
            if overlap:
                raise ValueError(
                    f"stage {stage} attempted silent overwrite of context keys: {sorted(overlap)}"
                )
            context.data.update(result.outputs)

        context.stage_traces.append(
            StageTrace(
                stage,
                result.status,
                result.rationale,
                result.blocking,
                tuple(sorted(result.outputs)),
            )
        )

        unresolved = result.blocking and result.status in {
            StageStatus.BLOCKED,
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }
        if unresolved:
            blockers.append(f"{stage}: {result.rationale}")
            break

    return ControlledRunResult(
        run_id=run_id,
        execution_mode=execution_mode,
        stage_traces=tuple(context.stage_traces),
        data=dict(context.data),
        blocked_reasons=tuple(blockers),
        freeze_token=context.freeze_token,
    )
