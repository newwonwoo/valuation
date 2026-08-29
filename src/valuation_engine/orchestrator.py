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
    visual_pages_included_in_main_body: int
    body_min_pt: int
    primary_heading_min_pt: int
    section_heading_min_pt: int
    dense_wide_tables_forbidden: bool
    direct_http_links_required: bool
    claim_source_mapping_required: bool
    non_http_source_refs_forbidden_in_live_reports: bool
    llm_insight_separate_section_required: bool
    llm_insight_max_chars: int
    deterministic_outputs_separated_from_llm: bool
    uncalibrated_prior_display_allowed: bool = True
    uncalibrated_prior_weighting_forbidden: bool = True
    declared_forecast_history_capture_required: bool = True
    append_only_probability_history_required: bool = True
    visible_language: str = "ko"
    primary_section_order: tuple[str, ...] = (
        "투자 요약",
        "가치평가",
        "핵심 가정과 위험",
        "증권사·시장 비교",
        "정보 출처 — 원문 바로 확인",
    )
    decision_report_precedes_audit_appendix: bool = True
    technical_identifiers_collapsed: bool = True
    immutable_versioned_report_required: bool = True
    latest_manifest_required: bool = True
    visible_artifact_id_required: bool = True
    user_delivery_must_use_versioned_filename: bool = True
    brokerage_style_reference: str = "docs/KOREAN_BROKERAGE_REPORT_STYLE.md"
    brokerage_style_sample_count: int = 4
    brokerage_style_structural_only: bool = True
    brokerage_style_content_copy_forbidden: bool = True
    summary_is_investment_report: bool = True
    first_screen_required_fields: tuple[str, ...] = (
        "투자판단",
        "현재가",
        "기준 내재가치",
        "가치평가 범위",
    )
    first_screen_required_blocks: tuple[str, ...] = (
        "한 문장 결론",
        "투자포인트",
        "판단 변경 조건",
    )

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
        if self.visual_pages_included_in_main_body != 2:
            raise ValueError("final report must include exactly two visual pages inside the main-body target")
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
        if not all(
            (
                self.immutable_versioned_report_required,
                self.latest_manifest_required,
                self.visible_artifact_id_required,
                self.user_delivery_must_use_versioned_filename,
            )
        ):
            raise ValueError("report artifact freshness requirements cannot be disabled")
        if not all(
            (
                self.llm_insight_separate_section_required,
                self.deterministic_outputs_separated_from_llm,
            )
        ) or not 1 <= self.llm_insight_max_chars <= 1000:
            raise ValueError("LLM insight reporting must be separate and capped at 1,000 characters")
        if not all(
            (
                self.uncalibrated_prior_display_allowed,
                self.uncalibrated_prior_weighting_forbidden,
                self.declared_forecast_history_capture_required,
                self.append_only_probability_history_required,
            )
        ):
            raise ValueError(
                "probability reporting must separate display priors from calibrated weighting "
                "and require append-only declared forecast history"
            )
        if self.visible_language != "ko" or not self.primary_section_order:
            raise ValueError("reader-facing report must declare Korean section ordering")
        if not all(
            (
                self.decision_report_precedes_audit_appendix,
                self.technical_identifiers_collapsed,
            )
        ):
            raise ValueError("developer-facing report details must remain in the collapsed audit appendix")
        if (
            not self.brokerage_style_reference.endswith(".md")
            or self.brokerage_style_sample_count < 3
            or not self.first_screen_required_fields
            or not self.first_screen_required_blocks
        ):
            raise ValueError("brokerage style basis and first-screen contract are incomplete")
        if not all(
            (
                self.brokerage_style_structural_only,
                self.brokerage_style_content_copy_forbidden,
                self.summary_is_investment_report,
            )
        ):
            raise ValueError("brokerage samples may define structure only and the summary must be the investment report")


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

_GATE_COMPLETION_KO = {
    "G1_EVIDENCE_ROUTING": "증거 수집·산업 라우팅을 완료하고 근거 기록을 확정했습니다",
    "G2_INSIGHT_CHALLENGE": "환경 변화와 기업 강점의 연결 인사이트 및 반증 검토를 완료했습니다",
    "G3_ASSUMPTIONS_METHOD_RISK": "가정·평가방법·베타·가중평균자본비용의 적용 여부를 확정했습니다",
    "G4_VALUATION_AUDIT_FREEZE": "가치평가와 오류 점검을 마치고 결과를 확정했습니다",
    "G5_POST_FREEZE_PERSISTENCE": "시장·증권사 비교 후 한국어 최종보고서와 요약 이미지 2장을 저장했습니다",
}


def _stage_risk_ko(trace: StageTrace) -> str:
    if trace.stage == "PROBABILITY_DISTRIBUTION_ANALYSIS":
        return (
            "시나리오 확률 보정 점검: 실제 해결 이력 기반 확률 보정이 "
            "완료되지 않아 확률가중 기대값을 산출하지 않았습니다"
        )
    if trace.stage == "ROCKET_INSIGHT_SCAN":
        return "환경 변화 인사이트 탐색: 로켓슬라 인사이트 스캐너가 확인 필요 경고를 남겼습니다"
    if trace.stage == "MARKET_COMPARE":
        return (
            "시장 함의 기대치 역산: 현재 시장가격이 요구하는 영구성장률·현금흐름 "
            "수준이 확정된 가정과 달라 확인이 필요합니다"
        )
    return "확인 필요 상태가 기록되었습니다. 상세 사유는 분석 기록을 확인하십시오"


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
    artifact_policy = raw.get("artifact_freshness_policy")
    if not isinstance(artifact_policy, dict):
        raise ValueError("reporting contract requires artifact_freshness_policy")
    llm_policy = raw.get("llm_insight_policy")
    if not isinstance(llm_policy, dict):
        raise ValueError("reporting contract requires llm_insight_policy")
    probability_policy = raw.get("probability_reporting_policy")
    if not isinstance(probability_policy, dict):
        raise ValueError("reporting contract requires probability_reporting_policy")
    reader_policy = raw.get("reader_experience_policy")
    if not isinstance(reader_policy, dict):
        raise ValueError("reporting contract requires reader_experience_policy")
    primary_section_order = reader_policy.get("primary_section_order")
    if not isinstance(primary_section_order, list) or not all(
        isinstance(item, str) and item.strip() for item in primary_section_order
    ):
        raise ValueError("reader experience policy requires primary_section_order")
    brokerage_style = reader_policy.get("brokerage_style_basis")
    if not isinstance(brokerage_style, dict):
        raise ValueError("reader experience policy requires brokerage_style_basis")

    def required_text_tuple(key: str) -> tuple[str, ...]:
        value = brokerage_style.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"brokerage style basis requires {key}")
        return tuple(item.strip() for item in value)

    return ReportingContract(
        contract_id=str(raw.get("contract_id") or "").strip(),
        major_gates=tuple(gates),
        main_body_target_pages=page_range("main_body_target_pages"),
        audit_appendix_target_pages=page_range("audit_appendix_target_pages"),
        total_page_cap=int(page_policy.get("total_page_cap") or 0),
        visual_pages_included_in_main_body=int(
            page_policy.get("visual_pages_included_in_main_body") or 0
        ),
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
        immutable_versioned_report_required=bool(
            artifact_policy.get("immutable_versioned_report_required", False)
        ),
        latest_manifest_required=bool(
            artifact_policy.get("latest_manifest_required", False)
        ),
        visible_artifact_id_required=bool(
            artifact_policy.get("visible_artifact_id_required", False)
        ),
        user_delivery_must_use_versioned_filename=bool(
            artifact_policy.get("user_delivery_must_use_versioned_filename", False)
        ),
        llm_insight_separate_section_required=bool(
            llm_policy.get("separate_section_required", False)
        ),
        llm_insight_max_chars=int(llm_policy.get("max_chars") or 0),
        deterministic_outputs_separated_from_llm=bool(
            llm_policy.get("deterministic_outputs_separated", False)
        ),
        uncalibrated_prior_display_allowed=bool(
            probability_policy.get("uncalibrated_prior_display_allowed", False)
        ),
        uncalibrated_prior_weighting_forbidden=bool(
            probability_policy.get("uncalibrated_prior_weighting_forbidden", False)
        ),
        declared_forecast_history_capture_required=bool(
            probability_policy.get("declared_forecast_history_capture_required", False)
        ),
        append_only_probability_history_required=bool(
            probability_policy.get("append_only_history_required", False)
        ),
        visible_language=str(reader_policy.get("visible_language") or "").strip(),
        primary_section_order=tuple(item.strip() for item in primary_section_order),
        decision_report_precedes_audit_appendix=bool(
            reader_policy.get("decision_report_precedes_audit_appendix", False)
        ),
        technical_identifiers_collapsed=bool(
            reader_policy.get("technical_identifiers_collapsed", False)
        ),
        brokerage_style_reference=str(
            brokerage_style.get("reference_document") or ""
        ).strip(),
        brokerage_style_sample_count=int(
            brokerage_style.get("reviewed_sample_count") or 0
        ),
        brokerage_style_structural_only=bool(
            brokerage_style.get("structural_patterns_only", False)
        ),
        brokerage_style_content_copy_forbidden=bool(
            brokerage_style.get("content_copy_forbidden", False)
        ),
        summary_is_investment_report=bool(
            brokerage_style.get("summary_is_investment_report", False)
        ),
        first_screen_required_fields=required_text_tuple(
            "first_screen_required_fields"
        ),
        first_screen_required_blocks=required_text_tuple(
            "first_screen_required_blocks"
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
        decisive_result = f"{relevant[-1].stage} 단계가 {relevant[-1].status.value} 상태로 종료되었습니다"
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
        decisive_result = _GATE_COMPLETION_KO.get(
            definition.gate_id,
            f"{relevant[-1].stage} 단계까지 완료했습니다",
        )
        risks = tuple(
            _stage_risk_ko(item)
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
        residual_risk=" | ".join(risks) if risks else "없음",
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
