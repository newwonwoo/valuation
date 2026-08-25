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
from .doctrine_runtime import (
    DoctrineCoverageSnapshot,
    build_doctrine_coverage,
    load_default_unit_contract_registry,
)
from .runtime_safety import (
    evidence_ledgers,
    mutable_guard_snapshot,
    mutated_guard_keys,
    read_only_data_view,
    sanitize_runtime_text,
)
from .unit_contracts import UnitContractRegistry


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
        if not isinstance(self.outputs, dict):
            raise TypeError("stage result outputs must be a dict")


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


def _freeze_from_context(
    context: OrchestratorContext,
    coverage: DoctrineCoverageSnapshot,
) -> IntrinsicFreezeToken:
    required = (
        "audit_passed",
        "decision_impact_completed",
        "ledger_snapshot_hash",
        "assumption_set_hash",
        "valuation_hash",
        "audit_hash",
        "industry_snapshot_hash",
        "source_snapshot_hash",
    )
    missing = tuple(key for key in required if key not in context.data)
    if missing:
        raise ValueError("intrinsic freeze missing: " + ", ".join(missing))
    if not bool(context.data["decision_impact_completed"]):
        raise ValueError("decision-impact measurement must complete before intrinsic freeze")
    return issue_freeze_token(
        run_id=context.run_id,
        audit_passed=bool(context.data["audit_passed"]),
        coverage_entries=coverage.entries,
        expected_module_ids=coverage.expected_unit_ids,
        ledger_snapshot_hash=str(context.data["ledger_snapshot_hash"]),
        assumption_set_hash=str(context.data["assumption_set_hash"]),
        valuation_hash=str(context.data["valuation_hash"]),
        audit_hash=str(context.data["audit_hash"]),
        industry_snapshot_hash=str(context.data["industry_snapshot_hash"]),
        source_snapshot_hash=str(context.data["source_snapshot_hash"]),
    )


def _put_runtime_value(context: OrchestratorContext, key: str, value: Any) -> None:
    existing = context.data.get(key)
    if key in context.data and existing != value:
        raise ValueError(f"Control Plane runtime key mismatch for {key}")
    context.data[key] = value


def _blocked_trace(
    context: OrchestratorContext,
    blockers: list[str],
    *,
    stage: str,
    reason: str,
) -> None:
    safe = sanitize_runtime_text(reason)
    context.stage_traces.append(StageTrace(stage, StageStatus.BLOCKED, safe, True))
    blockers.append(f"{stage}: {safe}")


def _adapter_context(context: OrchestratorContext) -> tuple[OrchestratorContext, object]:
    data_view = read_only_data_view(context.data)
    view = OrchestratorContext(
        context.run_id,
        context.execution_mode,
        data_view,  # type: ignore[arg-type]
        list(context.stage_traces),
        context.freeze_token,
    )
    return view, data_view


def _adapter_control_mutations(
    *,
    canonical: OrchestratorContext,
    adapter_view: OrchestratorContext,
    original_data_view: object,
) -> tuple[str, ...]:
    changed: list[str] = []
    if adapter_view.run_id != canonical.run_id:
        changed.append("run_id")
    if adapter_view.execution_mode is not canonical.execution_mode:
        changed.append("execution_mode")
    if adapter_view.data is not original_data_view:
        changed.append("data_binding")
    if adapter_view.stage_traces != canonical.stage_traces:
        changed.append("stage_traces")
    if adapter_view.freeze_token != canonical.freeze_token:
        changed.append("freeze_token")
    return tuple(changed)


def run_controlled_workflow(
    *,
    run_id: str,
    execution_mode: ExecutionMode,
    stage_sequence: tuple[str, ...],
    adapters: dict[str, StageAdapter],
    required_stages: tuple[str, ...],
    initial_data: dict[str, Any] | None = None,
    unit_contract_registry: UnitContractRegistry | None = None,
) -> ControlledRunResult:
    """Execute the canonical stage order for PRIMARY_SHADOW/LIVE_PRIMARY.

    Stage adapters receive an isolated, read-only top-level view. Mutable builtin values are
    stage-local copies, EvidenceLedger is sealed while a downstream adapter runs, and stage
    control fields are disposable. All adapter failure text is sanitized before persistence.
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

    registry = unit_contract_registry or load_default_unit_contract_registry()
    registry.validate()
    context = OrchestratorContext(run_id, execution_mode, dict(initial_data or {}))
    blockers: list[str] = []
    required = set(required_stages)

    for stage_index, stage in enumerate(stage_sequence):
        if stage in _POST_FREEZE_STAGES:
            if context.freeze_token is None:
                _blocked_trace(
                    context,
                    blockers,
                    stage=stage,
                    reason=f"{stage} requires IntrinsicFreezeToken",
                )
                break
            authorize_post_freeze(context.freeze_token, run_id=run_id)

        if stage == "AUDIT_GATE":
            try:
                pre_audit = build_doctrine_coverage(
                    registry,
                    relevant_stages=stage_sequence[:stage_index],
                    stage_traces=context.stage_traces,
                    required_stages=required_stages,
                )
                _put_runtime_value(context, "pre_audit_doctrine_coverage", pre_audit.entries)
                _put_runtime_value(context, "pre_audit_expected_unit_ids", pre_audit.expected_unit_ids)
            except Exception as exc:
                _blocked_trace(
                    context,
                    blockers,
                    stage=stage,
                    reason=(
                        "pre-audit doctrine coverage failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                break

        if stage == "INTRINSIC_VALUE_FREEZE":
            try:
                final_coverage = build_doctrine_coverage(
                    registry,
                    relevant_stages=stage_sequence[: stage_index + 1],
                    stage_traces=context.stage_traces,
                    required_stages=required_stages,
                    prospective_pass_stages=("INTRINSIC_VALUE_FREEZE",),
                )
                token = _freeze_from_context(context, final_coverage)
                context.freeze_token = token
                _put_runtime_value(context, "runtime_doctrine_coverage", final_coverage.entries)
                _put_runtime_value(context, "runtime_expected_unit_ids", final_coverage.expected_unit_ids)
                if "doctrine_coverage" not in context.data:
                    context.data["doctrine_coverage"] = final_coverage.entries
                if "doctrine_expected_unit_ids" not in context.data:
                    context.data["doctrine_expected_unit_ids"] = final_coverage.expected_unit_ids
                context.data["intrinsic_freeze_token"] = token
                context.stage_traces.append(
                    StageTrace(
                        stage,
                        StageStatus.PASS,
                        "audit, decision-impact record and generated doctrine coverage authorized intrinsic freeze",
                        False,
                        (
                            "intrinsic_freeze_token",
                            "runtime_doctrine_coverage",
                            "runtime_expected_unit_ids",
                        ),
                    )
                )
            except Exception as exc:
                _blocked_trace(
                    context,
                    blockers,
                    stage=stage,
                    reason=f"intrinsic freeze blocked: {type(exc).__name__}: {exc}",
                )
                break
            continue

        adapter = adapters.get(stage)
        if adapter is None:
            status = StageStatus.NOT_IMPLEMENTED
            is_blocking = stage in required
            reason = (
                "required stage adapter is not implemented"
                if is_blocking
                else "optional stage adapter is not implemented"
            )
            context.stage_traces.append(StageTrace(stage, status, reason, is_blocking))
            if is_blocking:
                blockers.append(f"{stage}: {reason}")
                break
            continue

        adapter_view, data_view = _adapter_context(context)
        guard_before = mutable_guard_snapshot(adapter_view.data)
        sealed_ledgers = evidence_ledgers(context.data)
        for ledger in sealed_ledgers:
            ledger._enter_runtime_readonly()
        adapter_error: Exception | None = None
        result: object | None = None
        try:
            result = adapter(adapter_view)
        except Exception as exc:
            adapter_error = exc
        finally:
            for ledger in reversed(sealed_ledgers):
                ledger._exit_runtime_readonly()

        control_mutations = _adapter_control_mutations(
            canonical=context,
            adapter_view=adapter_view,
            original_data_view=data_view,
        )
        data_mutations = mutated_guard_keys(guard_before, adapter_view.data)
        if control_mutations or data_mutations:
            details = []
            if control_mutations:
                details.append("control=" + ",".join(control_mutations))
            if data_mutations:
                details.append("upstream_data=" + ",".join(data_mutations))
            _blocked_trace(
                context,
                blockers,
                stage=stage,
                reason=(
                    "stage adapter attempted out-of-band context mutation: "
                    + "; ".join(details)
                ),
            )
            break

        if adapter_error is not None:
            _blocked_trace(
                context,
                blockers,
                stage=stage,
                reason=(
                    "stage adapter failed: "
                    f"{type(adapter_error).__name__}: {adapter_error}"
                ),
            )
            break

        if not isinstance(result, StageExecutionResult):
            _blocked_trace(
                context,
                blockers,
                stage=stage,
                reason=(
                    "stage adapter contract violation: expected StageExecutionResult, got "
                    + type(result).__name__
                ),
            )
            break

        safe_rationale = sanitize_runtime_text(result.rationale)
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
                safe_rationale,
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
            blockers.append(f"{stage}: {safe_rationale}")
            break

    return ControlledRunResult(
        run_id=run_id,
        execution_mode=execution_mode,
        stage_traces=tuple(context.stage_traces),
        data=dict(context.data),
        blocked_reasons=tuple(blockers),
        freeze_token=context.freeze_token,
    )
