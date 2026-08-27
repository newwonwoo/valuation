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
class MajorGateDefinition:
    gate_id: str
    title: str
    stages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.gate_id or not self.title or not self.stages:
            raise ValueError("major gate definition is incomplete")


@dataclass(frozen=True)
class ReportingContract:
    contract_id: str
    major_gates: tuple[MajorGateDefinition, ...]
    main_body_target_pages: tuple[int, int]
    audit_appendix_target_pages: tuple[int, int]
    total_page_cap: int
    body_min_pt: int
    primary_heading_min_pt: int
    section_heading_min_pt: int
    dense_wide_tables_forbidden: bool
    direct_http_links_required: bool
    claim_source_mapping_required: bool
    non_http_source_refs_forbidden_in_live_reports: bool

    def __post_init__(self) -> None:
        if not self.contract_id or not self.major_gates:
            raise ValueError("reporting contract is incomplete")
        for label, page_range in (
            ("main body", self.main_body_target_pages),
            ("audit appendix", self.audit_appendix_target_pages),
        ):
            if (
                len(page_range) != 2
                or page_range[0] < 1
                or page_range[0] > page_range[1]
            ):
                raise ValueError(f"invalid {label} page target")
        if self.total_page_cap < sum(
            (self.main_body_target_pages[0], self.audit_appendix_target_pages[0])
        ):
            raise ValueError("total report page cap is below the minimum page targets")
        if not (
            self.body_min_pt >= 12
            and self.primary_heading_min_pt > self.section_heading_min_pt
            and self.section_heading_min_pt > self.body_min_pt
        ):
            raise ValueError("report typography hierarchy is invalid")
        if not self.dense_wide_tables_forbidden:
            raise ValueError("compact report contract must forbid dense wide tables")
        if not all(
            (
                self.direct_http_links_required,
                self.claim_source_mapping_required,
                self.non_http_source_refs_forbidden_in_live_reports,
            )
        ):
            raise ValueError("live report source-link requirements cannot be disabled")


@dataclass(frozen=True)
class MajorGateSummary:
    gate_id: str
    title: str
    ordinal: int
    gate_count: int
    status: StageStatus
    completed_stage_count: int
    expected_stage_count: int
    decisive_result: str
    residual_risk: str
    next_action: str

    def __post_init__(self) -> None:
        if (
            not self.gate_id
            or not self.title
            or not self.decisive_result
            or not self.residual_risk
            or not self.next_action
        ):
            raise ValueError("major gate summary is incomplete")
        if not 1 <= self.ordinal <= self.gate_count:
            raise ValueError("major gate summary ordinal is invalid")
        if not 1 <= self.completed_stage_count <= self.expected_stage_count:
            raise ValueError("major gate stage counts are invalid")


@dataclass(frozen=True)
class ControlledRunResult:
    run_id: str
    execution_mode: ExecutionMode
    stage_traces: tuple[StageTrace, ...]
    data: dict[str, Any]
    blocked_reasons: tuple[str, ...]
    freeze_token: IntrinsicFreezeToken | None
    major_gate_summaries: tuple[MajorGateSummary, ...] = ()
    reporting_warnings: tuple[str, ...] = ()

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
MajorGateReporter = Callable[[MajorGateSummary], None]

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


def load_reporting_contract(path: str | Path) -> ReportingContract:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sequence = load_stage_sequence(path)
    raw = payload.get("reporting_contract")
    if not isinstance(raw, dict):
        raise ValueError("control-plane registry requires reporting_contract")
    raw_gates = raw.get("major_gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError("reporting contract requires major_gates")

    gates: list[MajorGateDefinition] = []
    cursor = 0
    seen_ids: set[str] = set()
    for item in raw_gates:
        if not isinstance(item, dict):
            raise TypeError("major gate entries must be mappings")
        gate_id = str(item.get("gate_id") or "").strip()
        title = str(item.get("title") or "").strip()
        terminal_stage = str(item.get("terminal_stage") or "").strip()
        if not gate_id or gate_id in seen_ids:
            raise ValueError("major gate IDs must be non-empty and unique")
        if terminal_stage not in sequence[cursor:]:
            raise ValueError(
                f"major gate {gate_id} terminal stage is missing or out of order"
            )
        terminal_index = sequence.index(terminal_stage, cursor)
        gates.append(
            MajorGateDefinition(
                gate_id=gate_id,
                title=title,
                stages=sequence[cursor : terminal_index + 1],
            )
        )
        seen_ids.add(gate_id)
        cursor = terminal_index + 1
    if cursor != len(sequence):
        raise ValueError("major gates must partition the full canonical stage sequence")

    page_policy = raw.get("final_report_page_policy")
    if not isinstance(page_policy, dict):
        raise ValueError("reporting contract requires final_report_page_policy")

    def page_range(key: str) -> tuple[int, int]:
        value = page_policy.get(key)
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, int) for item in value)
        ):
            raise ValueError(f"report page policy {key} must be [min, max]")
        return value[0], value[1]

    typography = raw.get("typography_policy")
    if not isinstance(typography, dict):
        raise ValueError("reporting contract requires typography_policy")
    source_links = raw.get("source_link_policy")
    if not isinstance(source_links, dict):
        raise ValueError("reporting contract requires source_link_policy")

    return ReportingContract(
        contract_id=str(raw.get("contract_id") or "").strip(),
        major_gates=tuple(gates),
        main_body_target_pages=page_range("main_body_target_pages"),
        audit_appendix_target_pages=page_range("audit_appendix_target_pages"),
        total_page_cap=int(page_policy.get("total_page_cap") or 0),
        body_min_pt=int(typography.get("body_min_pt") or 0),
        primary_heading_min_pt=int(
            typography.get("primary_heading_min_pt") or 0
        ),
        section_heading_min_pt=int(
            typography.get("section_heading_min_pt") or 0
        ),
        dense_wide_tables_forbidden=bool(
            typography.get("dense_wide_tables_forbidden", False)
        ),
        direct_http_links_required=bool(
            source_links.get("direct_http_links_required", False)
        ),
        claim_source_mapping_required=bool(
            source_links.get("claim_source_mapping_required", False)
        ),
        non_http_source_refs_forbidden_in_live_reports=bool(
            source_links.get("non_http_source_refs_forbidden_in_live_reports", False)
        ),
    )


def _major_gate_status(traces: tuple[StageTrace, ...]) -> StageStatus:
    if any(item.blocking or item.status is StageStatus.BLOCKED for item in traces):
        return StageStatus.BLOCKED
    if any(
        item.status
        in {
            StageStatus.WARNING,
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }
        for item in traces
    ):
        return StageStatus.WARNING
    if any(item.status is StageStatus.RECOVERED for item in traces):
        return StageStatus.RECOVERED
    return StageStatus.PASS


def _major_gate_summary(
    definition: MajorGateDefinition,
    *,
    ordinal: int,
    gate_count: int,
    traces: tuple[StageTrace, ...],
    next_gate: MajorGateDefinition | None,
) -> MajorGateSummary:
    relevant = tuple(item for item in traces if item.stage in definition.stages)
    if not relevant:
        raise ValueError(f"major gate {definition.gate_id} has no executed stages")
    status = _major_gate_status(relevant)
    if status is StageStatus.BLOCKED:
        next_action = f"RESOLVE_{definition.gate_id}"
        decisive_result = (
            f"{relevant[-1].stage} terminated with {relevant[-1].status.value}"
        )
        risks = tuple(
            f"{item.stage}:{item.status.name}"
            for item in relevant
            if item.blocking
            or item.status
            in {
                StageStatus.BLOCKED,
                StageStatus.NOT_IMPLEMENTED,
                StageStatus.RECOVERY_REQUIRED,
                StageStatus.AWAITING_USER_DECISION,
            }
        )
    else:
        next_action = (
            next_gate.gate_id if next_gate is not None else "FINAL_RESULT_REPORT"
        )
        decisive_result = relevant[-1].rationale
        risks = tuple(
            f"{item.stage}: {item.rationale}"
            for item in relevant
            if item.status in {StageStatus.WARNING, StageStatus.RECOVERED}
        )
    return MajorGateSummary(
        gate_id=definition.gate_id,
        title=definition.title,
        ordinal=ordinal,
        gate_count=gate_count,
        status=status,
        completed_stage_count=len(relevant),
        expected_stage_count=len(definition.stages),
        decisive_result=decisive_result,
        residual_risk=" | ".join(risks) if risks else "NONE",
        next_action=next_action,
    )


def summarize_major_gates(
    traces: tuple[StageTrace, ...],
    contract: ReportingContract,
) -> tuple[MajorGateSummary, ...]:
    summaries: list[MajorGateSummary] = []
    for index, definition in enumerate(contract.major_gates):
        relevant = tuple(item for item in traces if item.stage in definition.stages)
        if not relevant:
            break
        terminal_reached = relevant[-1].stage == definition.stages[-1]
        blocked = any(item.blocking for item in relevant)
        if not terminal_reached and not blocked:
            break
        next_gate = (
            contract.major_gates[index + 1]
            if index + 1 < len(contract.major_gates)
            else None
        )
        summaries.append(
            _major_gate_summary(
                definition,
                ordinal=index + 1,
                gate_count=len(contract.major_gates),
                traces=traces,
                next_gate=next_gate,
            )
        )
        if blocked:
            break
    return tuple(summaries)


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
        calibration_dataset_hash=str(
            context.data.get("probability_calibration_dataset_hash") or ""
        ),
        calibration_snapshot_hash=str(
            context.data.get("probability_calibration_snapshot_hash") or ""
        ),
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
    reporting_contract: ReportingContract | None = None,
    major_gate_reporter: MajorGateReporter | None = None,
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
    if major_gate_reporter is not None and reporting_contract is None:
        raise ValueError("major_gate_reporter requires a reporting_contract")
    if reporting_contract is not None:
        reporting_stages = tuple(
            stage
            for gate in reporting_contract.major_gates
            for stage in gate.stages
        )
        if reporting_stages != stage_sequence:
            raise ValueError(
                "reporting contract must partition the executed stage sequence exactly"
            )
    unknown_required = tuple(stage for stage in required_stages if stage not in stage_sequence)
    if unknown_required:
        raise ValueError("required stages not in sequence: " + ", ".join(unknown_required))

    registry = unit_contract_registry or load_default_unit_contract_registry()
    registry.validate()
    context = OrchestratorContext(run_id, execution_mode, dict(initial_data or {}))
    blockers: list[str] = []
    required = set(required_stages)
    emitted_gate_ids: set[str] = set()
    gate_summaries: tuple[MajorGateSummary, ...] = ()
    reporting_warnings: list[str] = []

    def emit_new_gate_summaries() -> None:
        nonlocal gate_summaries
        if reporting_contract is None:
            return
        gate_summaries = summarize_major_gates(
            tuple(context.stage_traces), reporting_contract
        )
        for summary in gate_summaries:
            if summary.gate_id in emitted_gate_ids:
                continue
            emitted_gate_ids.add(summary.gate_id)
            if major_gate_reporter is None:
                continue
            try:
                major_gate_reporter(summary)
            except Exception as exc:
                reporting_warnings.append(
                    f"{summary.gate_id}: major-gate reporter failed ({type(exc).__name__})"
                )

    for stage_index, stage in enumerate(stage_sequence):
        if stage in _POST_FREEZE_STAGES:
            if context.freeze_token is None:
                _blocked_trace(
                    context,
                    blockers,
                    stage=stage,
                    reason=f"{stage} requires IntrinsicFreezeToken",
                )
                emit_new_gate_summaries()
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
                emit_new_gate_summaries()
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
                emit_new_gate_summaries()
            except Exception as exc:
                _blocked_trace(
                    context,
                    blockers,
                    stage=stage,
                    reason=f"intrinsic freeze blocked: {type(exc).__name__}: {exc}",
                )
                emit_new_gate_summaries()
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
            emit_new_gate_summaries()
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
            emit_new_gate_summaries()
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
            emit_new_gate_summaries()
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
            emit_new_gate_summaries()
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
        emit_new_gate_summaries()

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
        major_gate_summaries=gate_summaries,
        reporting_warnings=tuple(reporting_warnings),
    )
