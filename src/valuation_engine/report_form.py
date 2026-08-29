from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .broker_runtime import BrokerResearchPreFreezeResult
from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import ExecutionMode, StageStatus
from .orchestrator import (
    ControlledRunResult,
    load_reporting_contract,
    load_stage_sequence,
    summarize_major_gates,
)
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import selected_methods_require_discount_rate
from .report_localization import (
    localize_stage_references,
    module_label_ko,
    stage_label_ko,
)
from .evidence_composition import EvidenceCompositionReport
from .source_reporting import build_source_link_index
from .valuation_sensitivity import (
    DISCOUNT_RATE,
    FCFF_LEVEL,
    TERMINAL_GROWTH,
    ValuationSensitivityReport,
)
from .visual_reporting import render_report_visuals


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STAGE_REGISTRY = _REPO_ROOT / "config" / "control_plane_stage_registry.yaml"
_ACCEPTABLE_STAGE_STATUSES = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}

_STAGE_STATUS_KO = {
    StageStatus.PASS: "통과",
    StageStatus.WARNING: "경고",
    StageStatus.BLOCKED: "차단",
    StageStatus.SKIPPED_NOT_APPLICABLE: "해당 없음",
    StageStatus.NOT_IMPLEMENTED: "미구현",
    StageStatus.RECOVERED: "복구 완료",
    StageStatus.RECOVERY_REQUIRED: "복구 필요",
    StageStatus.AWAITING_USER_DECISION: "사용자 결정 대기",
}


def _stage_status_ko(status: StageStatus) -> str:
    return _STAGE_STATUS_KO.get(status, status.value)


def _next_action_ko(
    value: str,
    reporting_contract: object,
) -> str:
    if value == "FINAL_RESULT_REPORT":
        return "최종 결과보고서"
    for gate in getattr(reporting_contract, "major_gates", ()):
        if value == gate.gate_id:
            return gate.title
        if value == f"RESOLVE_{gate.gate_id}":
            return f"{gate.title} 차단 해소"
    return localize_stage_references(value)


def _compact_localized_modules(values: object, *, limit: int = 8) -> str:
    if not isinstance(values, (list, tuple)) or not values:
        return "없음"
    labels = [module_label_ko(item) for item in values]
    head = labels[:limit]
    suffix = f" 외 {len(labels) - limit}개" if len(labels) > limit else ""
    return ", ".join(head) + suffix


@dataclass(frozen=True)
class ExecutionCheck:
    check_id: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        if not self.check_id or not self.detail:
            raise ValueError("execution check requires identity and detail")


@dataclass(frozen=True)
class RunAttestation:
    run_id: str
    checks: tuple[ExecutionCheck, ...]
    attestation_hash: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.checks or not self.attestation_hash:
            raise ValueError("run attestation is incomplete")

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.checks)


def _stable_hash(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _string_hash(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _check(check_id: str, passed: bool, success: str, failure: str) -> ExecutionCheck:
    return ExecutionCheck(check_id, passed, success if passed else failure)


def _markdown_section(report: object, heading: str) -> str | None:
    if not isinstance(report, str):
        return None
    marker = f"## {heading}"
    start = report.find(marker)
    if start < 0:
        return None
    next_heading = report.find("\n## ", start + len(marker))
    return report[start:] if next_heading < 0 else report[start:next_heading]


def _evidence_composition_lines(data: dict[str, Any]) -> list[str]:
    """Render how much of the committed model is filing versus judgement."""
    report = data.get("evidence_composition_report")
    if not isinstance(report, EvidenceCompositionReport):
        return []
    lines = ["", "### 근거 구성", "", f"- {report.summary_ko}"]
    if report.ledger_layers:
        lines.append(
            f"- 수집 근거 {report.ledger_active_count}건 — "
            + " · ".join(
                f"{item.label} {item.count}건({item.share * 100:.1f}%)"
                for item in report.ledger_layers
            )
        )
    detail = (
        f"- 공시·회사 공식계획 직접 인용 {report.valuation_primary_backed_share * 100:.1f}% · "
        f"분석가 추정 {report.valuation_underwriting_share * 100:.1f}%"
    )
    if report.valuation_mean_confidence is not None:
        detail += f" · 평균 신뢰도 {report.valuation_mean_confidence:.2f}"
    lines.append(detail)
    lines.extend(f"- 확인 필요: {item.detail}" for item in report.warnings)
    return lines


def _sensitivity_delta_ko(variable: str, base_input, high_input) -> str:
    delta = high_input - base_input
    if variable in {DISCOUNT_RATE, TERMINAL_GROWTH}:
        return f"±{delta * 100:.1f}%p"
    if variable == FCFF_LEVEL:
        return f"±{delta * 100:.0f}%"
    return f"±{delta}"


def _valuation_sensitivity_lines(data: dict[str, Any]) -> list[str]:
    """Render which single kernel variable the frozen value actually hangs on."""
    report = data.get("valuation_sensitivity_report")
    if not isinstance(report, ValuationSensitivityReport):
        return []
    lines = ["", "### 가치 민감도", ""]
    measured = False
    for scenario in report.scenarios:
        if not scenario.measured or scenario.base_value_per_share is None:
            lines.append(f"- {scenario.scenario_id}: {scenario.rationale}")
            continue
        measured = True
        moves = " · ".join(
            f"{item.label} {_sensitivity_delta_ko(item.variable, item.base_input, item.high_input)}"
            f" → {item.low_value_pct * 100:+.1f}%/{item.high_value_pct * 100:+.1f}%"
            for item in scenario.variables
        )
        lines.append(
            f"- {scenario.scenario_id} 기준 "
            f"{scenario.base_value_per_share:,.0f}원 — {moves}"
        )
    if measured:
        lines.append(f"- {report.summary_ko}")
    lines.extend(f"- 확인 필요: {item.detail}" for item in report.warnings)
    return lines


def attest_controlled_run(
    result: ControlledRunResult,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> RunAttestation:
    sequence = load_stage_sequence(stage_registry_path)
    reporting_contract = load_reporting_contract(stage_registry_path)
    observed_stages = tuple(item.stage for item in result.stage_traces)
    observed_statuses = tuple(
        (item.stage, item.status.value, item.blocking) for item in result.stage_traces
    )
    expected_gate_summaries = summarize_major_gates(
        result.stage_traces, reporting_contract
    )
    data = result.data
    try:
        source_links = build_source_link_index(
            data,
            require_all_http=reporting_contract.direct_http_links_required,
        )
    except (TypeError, ValueError):
        source_links = ()
    persisted_report = data.get("final_report")
    llm_section = _markdown_section(
        persisted_report,
        "인공지능 인사이트 — 환경 변화 × 기업 강점",
    )
    investment_summary = _markdown_section(persisted_report, "투자 요약")
    source_links_bound = bool(source_links) and isinstance(persisted_report, str) and all(
        item.url in persisted_report for item in source_links
    )
    reader_sections = tuple(
        f"## {section}" for section in reporting_contract.primary_section_order
    )
    reader_positions = (
        tuple(persisted_report.find(section) for section in reader_sections)
        if isinstance(persisted_report, str)
        else ()
    )
    broker_report_structure = bool(reader_positions) and all(
        position >= 0 for position in reader_positions
    ) and tuple(sorted(reader_positions)) == reader_positions
    first_screen_contract = (
        isinstance(investment_summary, str)
        and all(
            f"**{field}**" in investment_summary
            for field in reporting_contract.first_screen_required_fields
        )
        and all(
            f"### {block}" in investment_summary
            for block in reporting_contract.first_screen_required_blocks
        )
    )
    probability_assessment = data.get("scenario_probability_assessment")
    probability_drafts = data.get("probability_forecast_drafts", ())
    valuation = data.get("generic_valuation_result")
    bound_scenarios = getattr(data.get("bound_scenario_set"), "scenarios", ())
    uncalibrated_prior_contract = probability_assessment is None or (
        getattr(getattr(probability_assessment, "status", None), "value", None)
        == "UNCALIBRATED"
        and not bool(
            getattr(probability_assessment, "numeric_weighting_allowed", True)
        )
        and getattr(valuation, "expected_value_per_share", None) is None
        and all(getattr(item, "probability", None) is None for item in bound_scenarios)
        and isinstance(persisted_report, str)
        and "**시나리오 가능성**" in persisted_report
        and "시나리오 발생 가능성 — 미보정 분석가 사전확률" in persisted_report
        and bool(data.get("scenario_probability_assessment_hash"))
    )
    declared_history_contract = not probability_drafts or (
        isinstance(probability_drafts, tuple)
        and bool(data.get("probability_forecast_record_path"))
        and bool(data.get("probability_forecast_record_hash"))
        and isinstance(data.get("probability_forecast_ids"), tuple)
        and len(data["probability_forecast_ids"]) == len(probability_drafts)
        and isinstance(persisted_report, str)
        and "사전에 기록한 사건 예측 — 보정 이력 적립용" in persisted_report
    )

    checks: list[ExecutionCheck] = [
        _check(
            "live_primary_mode",
            result.execution_mode is ExecutionMode.LIVE_PRIMARY,
            "보고서가 LIVE_PRIMARY 모드에서 생성되었습니다",
            "보고서가 LIVE_PRIMARY 모드에서 생성되지 않았습니다",
        ),
        _check(
            "run_unblocked",
            not result.blocked_reasons,
            "통제 실행에 차단 사유가 없습니다",
            "통제 실행에 차단 사유가 있습니다",
        ),
        _check(
            "canonical_stage_sequence",
            observed_stages == sequence,
            f"표준 {len(sequence)}개 단계를 순서대로 실행했습니다",
            "관측된 단계 순서가 표준 레지스트리와 다릅니다",
        ),
        _check(
            "terminal_stage_statuses",
            bool(result.stage_traces)
            and all(
                item.status in _ACCEPTABLE_STAGE_STATUSES and not item.blocking
                for item in result.stage_traces
            ),
            "모든 단계가 비차단 최종 상태로 종료되었습니다",
            "하나 이상의 단계가 미해결 또는 차단 상태입니다",
        ),
        _check(
            "intrinsic_freeze_token",
            result.freeze_token is not None,
            "가치평가 결과가 이후 참고자료와 분리된 상태로 확정되었습니다",
            "가치평가 결과 확정 기록이 누락되었습니다",
        ),
        _check(
            "evidence_ledger_hash",
            _string_hash(data, "ledger_snapshot_hash") is not None,
            "고정된 증거 원장 해시가 있습니다",
            "ledger_snapshot_hash가 누락되었습니다",
        ),
        _check(
            "assumption_set_hash",
            _string_hash(data, "assumption_set_hash") is not None,
            "컴파일된 가정 집합 해시가 있습니다",
            "assumption_set_hash가 누락되었습니다",
        ),
        _check(
            "scenario_set_hash",
            _string_hash(data, "scenario_set_hash") is not None,
            "결속된 시나리오 집합 해시가 있습니다",
            "scenario_set_hash가 누락되었습니다",
        ),
        _check(
            "valuation_hash",
            _string_hash(data, "valuation_hash") is not None,
            "결정론적 가치평가 해시가 있습니다",
            "valuation_hash가 누락되었습니다",
        ),
        _check(
            "audit_hash",
            _string_hash(data, "audit_hash") is not None
            and bool(data.get("audit_passed")),
            "일반 감사에 통과했고 해시가 있습니다",
            "일반 감사에 통과하지 못했거나 해시가 누락되었습니다",
        ),
        _check(
            "persisted_final_report",
            isinstance(data.get("final_report"), str) and bool(data.get("final_report")),
            "영구 저장된 실행 데이터에서 최종보고서를 생성했습니다",
            "영구 저장된 최종보고서가 누락되었습니다",
        ),
        _check(
            "major_gate_reporting_contract",
            result.major_gate_summaries == expected_gate_summaries
            and len(expected_gate_summaries) == len(reporting_contract.major_gates),
            "5개 대형 게이트 모두 압축 완료 요약을 생성했습니다",
            "5개 게이트 요약이 누락·미완료되었거나 단계 추적과 일치하지 않습니다",
        ),
        _check(
            "major_gate_delivery",
            not result.reporting_warnings,
            "대형 게이트 요약 전달에 실패가 없습니다",
            "하나 이상의 대형 게이트 요약 전달이 실패했습니다",
        ),
        _check(
            "direct_source_links",
            source_links_bound,
            "모든 보고서 출처가 최종보고서의 직접 HTTP(S) 원문 링크에 연결되었습니다",
            "직접 원문 링크가 누락·유효하지 않거나 최종보고서에 포함되지 않았습니다",
        ),
        _check(
            "llm_insight_separation",
            llm_section is not None
            and len(llm_section) <= reporting_contract.llm_insight_max_chars,
            "인공지능 관여 내용이 결정론적 결과와 분리되어 1,000자 이하로 표시되었습니다",
            "인공지능 인사이트 독립 구역이 없거나 1,000자 상한을 초과했습니다",
        ),
        _check(
            "korean_broker_report_structure",
            broker_report_structure,
            "투자판단·가치평가·가정과 위험·시장 비교·원문 출처 순서의 한국어 증권사형 본문을 생성했습니다",
            "한국어 증권사형 본문 순서가 누락되었거나 개발자용 정보가 본문 구조를 대신하고 있습니다",
        ),
        _check(
            "investment_summary_is_report",
            first_screen_contract,
            "첫 화면의 투자 요약만으로 판단·현재가·내재가치·핵심 동인·판단 변경 조건을 확인할 수 있습니다",
            "투자 요약이 독립적인 투자보고서 역할을 하지 못하거나 필수 판단 항목이 누락되었습니다",
        ),
        _check(
            "probability_reporting_and_history_contract",
            uncalibrated_prior_contract and declared_history_contract,
            "미보정 사전확률은 기대값과 분리했고 선언된 사건 예측의 변경 이력을 보존했습니다",
            "미보정 확률이 기대값에 섞였거나 선언된 사건 예측의 생산 이력 저장이 누락되었습니다",
        ),
    ]

    selected_methods = data.get("selected_methods", ())
    selected_methods_ok = isinstance(selected_methods, tuple) and all(
        isinstance(item, str) for item in selected_methods
    )
    checks.append(
        _check(
            "selected_method_contract",
            selected_methods_ok,
            "선택된 가치평가 방법이 형식화되어 있습니다",
            "selected_methods가 누락되었거나 형식이 잘못되었습니다",
        )
    )
    requires_risk = bool(
        selected_methods_ok
        and selected_methods_require_discount_rate(selected_methods)
    )
    if requires_risk:
        beta = data.get("live_beta_result")
        wacc = data.get("live_wacc_result")
        risk_chain_ok = (
            isinstance(beta, LiveBetaStageResult)
            and isinstance(wacc, LiveWACCStageResult)
            and wacc.beta_result.snapshot_hash == beta.snapshot_hash
            and _string_hash(data, "beta_snapshot_hash") == beta.snapshot_hash
            and _string_hash(data, "wacc_snapshot_hash") == wacc.snapshot_hash
        )
        checks.append(
            _check(
                "beta_wacc_same_run_chain",
                risk_chain_ok,
                "베타와 가중평균자본비용 스냅샷이 하나의 위험 사슬에 결속되었습니다",
                "필수 베타·가중평균자본비용 출력 또는 동일 실행 해시 결속이 누락되었습니다",
            )
        )

    broker_required = bool(data.get("broker_research_required", False))
    broker_result = data.get("broker_research_prefreeze_result")
    broker_configured = broker_required or broker_result is not None
    if broker_configured:
        broker_runtime_ok = (
            isinstance(broker_result, BrokerResearchPreFreezeResult)
            and _string_hash(data, "broker_research_snapshot_hash")
            == broker_result.snapshot_hash
            and bool(data.get("broker_research_audit_passed"))
            and _string_hash(data, "broker_research_audit_hash") is not None
        )
        checks.append(
            _check(
                "broker_research_primary_verification_chain",
                broker_runtime_ok,
                "사전 증권사 조사자료가 가치평가 입력과 분리되고 원문 확인 기록에 연결되었습니다",
                "증권사 자료 탐색·1차 출처 검증 또는 감사 결속이 누락되었습니다",
            )
        )

    capacity = data.get("capacity_commitment_assessment")
    capacity_typed = isinstance(capacity, CapacityCommitmentAssessment)
    checks.append(
        _check(
            "capacity_assessment",
            capacity_typed
            and _string_hash(data, "capacity_commitment_assessment_hash")
            == capacity.assessment_hash,
            "형식화된 생산능력 투자확정 평가와 해시가 있습니다",
            "생산능력 투자확정 평가가 누락되었거나 최신 상태가 아닙니다",
        )
    )
    checks.append(
        _check(
            "capacity_audit",
            bool(data.get("capacity_audit_passed"))
            and _string_hash(data, "capacity_audit_hash") is not None,
            "생산능력 누락·이중계상 감사에 통과했습니다",
            "생산능력 감사에 통과하지 못했거나 해시가 누락되었습니다",
        )
    )
    core_projects = (
        capacity.core_inclusion_required_projects if capacity_typed else ()
    )
    if core_projects:
        required_capacity_hashes = (
            "capacity_bridge_consumption_hash",
            "capacity_scenario_binding_hash",
            "capacity_valuation_binding_hash",
            "capacity_per_binding_hash",
            "capacity_consistency_hash",
        )
        missing = tuple(
            key for key in required_capacity_hashes if _string_hash(data, key) is None
        )
        checks.append(
            _check(
                "capacity_core_consumption_chain",
                not missing,
                "핵심 생산능력·자본적지출·가동 정상화 경로가 가치평가까지 결속되었습니다",
                "누락된 생산능력 실행 해시: " + ", ".join(missing),
            )
        )

    token = result.freeze_token
    freeze_hash_match = False
    if token is not None:
        freeze_hash_match = all(
            (
                getattr(token, "run_id", None) == result.run_id,
                getattr(token, "ledger_snapshot_hash", None)
                == data.get("ledger_snapshot_hash"),
                getattr(token, "assumption_set_hash", None)
                == data.get("assumption_set_hash"),
                getattr(token, "valuation_hash", None) == data.get("valuation_hash"),
                getattr(token, "audit_hash", None) == data.get("audit_hash"),
            )
        )
    checks.append(
        _check(
            "freeze_hash_binding",
            freeze_hash_match,
            "고정 토큰이 동일 증거·가정·가치평가·감사에 결속되었습니다",
            "고정 토큰 필드가 통제 실행 해시와 일치하지 않습니다",
        )
    )

    payload = {
        "contract": "prism_verified_report_attestation/v1",
        "run_id": result.run_id,
        "execution_mode": result.execution_mode.value,
        "observed_stage_statuses": observed_statuses,
        "blocked_reasons": result.blocked_reasons,
        "checks": [
            {
                "check_id": item.check_id,
                "passed": item.passed,
                "detail": item.detail,
            }
            for item in checks
        ],
        "major_gate_summaries": [
            {
                "gate_id": item.gate_id,
                "status": item.status.value,
                "completed_stage_count": item.completed_stage_count,
                "expected_stage_count": item.expected_stage_count,
                "decisive_result": item.decisive_result,
                "residual_risk": item.residual_risk,
                "next_action": item.next_action,
            }
            for item in result.major_gate_summaries
        ],
        "reporting_warnings": result.reporting_warnings,
        "reporting_contract": {
            "contract_id": reporting_contract.contract_id,
            "main_body_target_pages": reporting_contract.main_body_target_pages,
            "audit_appendix_target_pages": reporting_contract.audit_appendix_target_pages,
            "total_page_cap": reporting_contract.total_page_cap,
            "visual_pages_included_in_main_body": reporting_contract.visual_pages_included_in_main_body,
            "body_min_pt": reporting_contract.body_min_pt,
            "primary_heading_min_pt": reporting_contract.primary_heading_min_pt,
            "section_heading_min_pt": reporting_contract.section_heading_min_pt,
            "dense_wide_tables_forbidden": reporting_contract.dense_wide_tables_forbidden,
            "direct_http_links_required": reporting_contract.direct_http_links_required,
            "claim_source_mapping_required": reporting_contract.claim_source_mapping_required,
            "non_http_source_refs_forbidden_in_live_reports": reporting_contract.non_http_source_refs_forbidden_in_live_reports,
            "llm_insight_separate_section_required": reporting_contract.llm_insight_separate_section_required,
            "llm_insight_max_chars": reporting_contract.llm_insight_max_chars,
            "deterministic_outputs_separated_from_llm": reporting_contract.deterministic_outputs_separated_from_llm,
            "uncalibrated_prior_display_allowed": reporting_contract.uncalibrated_prior_display_allowed,
            "uncalibrated_prior_weighting_forbidden": reporting_contract.uncalibrated_prior_weighting_forbidden,
            "declared_forecast_history_capture_required": reporting_contract.declared_forecast_history_capture_required,
            "append_only_probability_history_required": reporting_contract.append_only_probability_history_required,
            "visible_language": reporting_contract.visible_language,
            "primary_section_order": reporting_contract.primary_section_order,
            "decision_report_precedes_audit_appendix": reporting_contract.decision_report_precedes_audit_appendix,
            "technical_identifiers_collapsed": reporting_contract.technical_identifiers_collapsed,
        },
        "direct_source_links": [item.url for item in source_links],
        "hashes": {
            key: data.get(key)
            for key in (
                "ledger_snapshot_hash",
                "assumption_set_hash",
                "scenario_set_hash",
                "beta_snapshot_hash",
                "wacc_snapshot_hash",
                "capacity_commitment_assessment_hash",
                "capacity_bridge_consumption_hash",
                "capacity_scenario_binding_hash",
                "capacity_valuation_binding_hash",
                "capacity_per_binding_hash",
                "capacity_consistency_hash",
                "capacity_audit_hash",
                "valuation_hash",
                "audit_hash",
            )
        },
        "freeze_token_hash": getattr(token, "token_hash", None),
    }
    if broker_configured:
        payload["broker_research"] = {
            "snapshot_hash": data.get("broker_research_snapshot_hash"),
            "audit_hash": data.get("broker_research_audit_hash"),
        }
    return RunAttestation(result.run_id, tuple(checks), _stable_hash(payload))


def render_controlled_run_report(
    result: ControlledRunResult,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> str:
    attestation = attest_controlled_run(
        result,
        stage_registry_path=stage_registry_path,
    )
    data = result.data
    reporting_contract = load_reporting_contract(stage_registry_path)
    broker_configured = bool(data.get("broker_research_required", False)) or (
        data.get("broker_research_prefreeze_result") is not None
    )
    passed_checks = sum(item.passed for item in attestation.checks)
    failed_checks = tuple(item for item in attestation.checks if not item.passed)
    persisted = data.get("final_report")
    if (
        isinstance(persisted, str)
        and "<summary>작성 근거와 계산 과정 보기</summary>" in persisted
    ):
        return persisted.rstrip() + "\n"
    lines = []
    if isinstance(persisted, str) and persisted:
        lines.append(persisted.rstrip())
    else:
        lines.extend((
            "# 투자보고서",
            "",
            "최종 투자보고서가 저장되지 않았습니다.",
        ))
    if result.blocked_reasons or failed_checks:
        lines.extend((
            "",
            "> **확인 필요:** 보고서 작성 과정에서 추가 확인이 필요한 항목이 있습니다.",
        ))
        for item in failed_checks:
            lines.append(f"> - {item.detail}")
    auxiliary = tuple(
        (label, data.get(key))
        for label, key in (
            ("베타", "beta_snapshot_hash"),
            ("가중평균자본비용", "wacc_snapshot_hash"),
            ("생산능력 평가", "capacity_commitment_assessment_hash"),
            ("생산능력 반영", "capacity_bridge_consumption_hash"),
            ("생산능력 시나리오", "capacity_scenario_binding_hash"),
            ("생산능력 가치평가", "capacity_valuation_binding_hash"),
            ("생산능력 주가수익비율", "capacity_per_binding_hash"),
            ("생산능력 정합성", "capacity_consistency_hash"),
            ("생산능력 오류 점검", "capacity_audit_hash"),
            *(
                (
                    ("사전 증권사 조사자료", "broker_research_snapshot_hash"),
                    ("증권사 자료 확인", "broker_research_audit_hash"),
                )
                if broker_configured
                else ()
            ),
        )
        if data.get(key) is not None
    )
    lines.extend((
        "",
        "---",
        "",
        "<details>",
        "<summary>작성 근거와 계산 과정 보기</summary>",
        "",
        "## 분석 절차 요약",
        "",
        f"- 자동 오류 점검: {passed_checks}/{len(attestation.checks)}개 통과",
        f"- 분석 절차 기록: {len(result.stage_traces)}/33개 완료",
        "",
        "## 주요 작업 단계",
    ))
    for summary in result.major_gate_summaries:
        lines.extend(
            (
                "",
                f"### {summary.ordinal}. {summary.title} — {_stage_status_ko(summary.status)} "
                f"({summary.completed_stage_count}/{summary.expected_stage_count})",
                f"- 결과: {localize_stage_references(summary.decisive_result)}",
                f"- 잔여위험: {localize_stage_references(summary.residual_risk)} "
                f"· 다음 단계: {_next_action_ko(summary.next_action, reporting_contract)}",
            )
        )
    if not result.major_gate_summaries:
        lines.extend(("", "### 누락", "- 5개 게이트 보고 계약을 확인할 수 없습니다."))
    if result.reporting_warnings:
        lines.extend(("", "### 보고 전달 경고", ""))
        lines.extend(f"- {item}" for item in result.reporting_warnings)
    technical_stage_lines = ["", "### 33단계 진행 상태"]
    trace_index = {trace.stage: trace for trace in result.stage_traces}
    stage_number = {
        trace.stage: index
        for index, trace in enumerate(result.stage_traces, start=1)
    }
    for gate in reporting_contract.major_gates:
        compact = " · ".join(
            f"{stage_number[stage]} {stage_label_ko(stage)}={_stage_status_ko(trace_index[stage].status)}"
            for stage in gate.stages
            if stage in trace_index
        )
        technical_stage_lines.append(f"- **{gate.title}:** {compact or '미실행'}")
    technical_stage_lines.append(
        "- 단계별 사유와 출력값 식별자는 별도 분석 기록에 보존됩니다."
    )

    impact = data.get("module_impact_summary")
    technical_module_lines: list[str] = []
    if isinstance(impact, dict):
        technical_module_lines.extend((
            "",
            "### 분석 모듈 점검",
            f"- 영향 측정 완료: {_compact_localized_modules(impact.get('measured', []))}",
            f"- 영향 미측정: {_compact_localized_modules(impact.get('not_measurable', []))}",
            f"- 비적용: {_compact_localized_modules(impact.get('not_applicable', []))} · 실패: {_compact_localized_modules(impact.get('failed', []))}",
        ))

    lines.extend((
        "",
        "## 세부 계산 기록",
        *technical_stage_lines,
        *technical_module_lines,
        *_evidence_composition_lines(data),
        *_valuation_sensitivity_lines(data),
        "",
        "### 실행 식별자와 해시",
        "",
        f"- 실행 식별자: `{result.run_id}`",
        f"- 실행 모드: `{result.execution_mode.value}`",
        f"- 작성 확인 해시: `{attestation.attestation_hash}`",
        f"- 증거 해시: `{data.get('ledger_snapshot_hash') or '누락'}`",
        f"- 가정 해시: `{data.get('assumption_set_hash') or '누락'}`",
        f"- 시나리오 해시: `{data.get('scenario_set_hash') or '누락'}`",
        f"- 가치평가 해시: `{data.get('valuation_hash') or '누락'}`",
        f"- 오류 점검 해시: `{data.get('audit_hash') or '누락'}`",
        f"- 가치평가 확정 해시: `{getattr(result.freeze_token, 'token_hash', None) or '누락'}`",
    ))
    if auxiliary:
        lines.append(
            "- 보조 결속정보: "
            + " · ".join(f"{label} `{value}`" for label, value in auxiliary)
        )
    lines.extend((
        "- 단계 기술 식별자: "
        + " · ".join(
            f"{index} `{trace.stage}`={trace.status.value}"
            for index, trace in enumerate(result.stage_traces, start=1)
        ),
        "",
        "</details>",
    ))
    return "\n".join(lines) + "\n"


def render_report_form_template() -> str:
    return """# {{ 기업명 }} 투자보고서

## 투자 요약

| 핵심 판단 항목 | 내용 |
| --- | --- |
| **투자판단** | {{ 판단 또는 보류 사유 }} |
| **현재가** | {{ 현재가와 기준일 }} |
| **기준 내재가치** | {{ 기준 내재가치와 현재가 대비 차이 }} |
| **가치평가 범위** | {{ 하방 }}원–{{ 상방 }}원 |
| **시나리오 가능성** | {{ 미보정 사전확률 또는 보정 상태 · 기대값 적용 여부 }} |

### 한 문장 결론

{{ 근거에 기반한 투자논지와 현재가에서 확인할 결정요인 }}

### 투자포인트

- {{ 핵심 가치동인 }}
- {{ 가치평가 해석 }}
- {{ 남은 제약 }}

### 판단 변경 조건

- 상방 확인: {{ 어떤 사실이 확인되면 판단이 강화되는가 }}
- 하방 훼손: {{ 어떤 사실이 확인되면 판단이 약해지는가 }}
- 행동 가능 조건: {{ 확률 보정·진입 규칙 등 필요한 통제 }}

## 가치평가

- 하방 시나리오: 주당 {{ down_value }}원
- 기준 시나리오: 주당 {{ core_value }}원
- 상방 시나리오: 주당 {{ bull_value }}원
- 확률가중 기대값: {{ 보정 완료 시 산출 | 미보정 시 보류 }}

## 핵심 가정과 위험

- 평가방법: {{ 한국어 평가방법명 }}
- 위험 입력: {{ 계층형 베타와 가중평균자본비용 }}
- 시나리오 가정: {{ 기업잉여현금흐름·영구성장률·영구 투하자본이익률 }}
- 핵심 위험: {{ 실적·생산능력·자본적지출·확률 보정 제약 }}

## 증권사·시장 비교

{{ 가치평가 확정 후 참고한 증권사 목표가와 현재가 }}

## 인공지능 인사이트 — 환경 변화 × 기업 강점

{{ 가치평가 계산과 분리한 1,000자 이하 연결 인사이트 }}

## 최종 요약 이미지

{{ 회사 강점·투자 결론·가치평가 이미지 1장 }}

{{ 가치평가 가정·위험·출처 이미지 1장 }}

## 정보 출처 — 원문 바로 확인

{{ 모든 핵심 주장과 입력값의 직접 원문 링크 }}

## 분석 범위와 유의사항

{{ 사실·분석가 가정·인공지능 인사이트의 구분 및 평가 제약 }}

<details>
<summary>작성 근거와 계산 과정 보기</summary>

## 분석 절차 요약

- 자동 오류 점검: {{ passed_checks }}/{{ total_checks }}개 통과
- 분석 절차 기록: {{ terminal_stage_count }}/33개 완료

## 주요 작업 단계

### {{ 순번 }}. {{ 한국어 게이트명 }} — {{ 상태 }}

- 결과: {{ 한국어 요약 }}
- 잔여위험: {{ 한국어 위험 요약 }} · 다음 단계: {{ 한국어 단계명 }}

## 세부 계산 기록

### 분석 절차별 기록

{{ 33개 단계의 한국어 이름과 상태 }}

### 실행 식별자와 해시

- 실행 식별자: `{{ run_id }}`
- 작성 확인 해시: `{{ attestation_hash }}`
- 계산 기준 해시: `{{ ledger_snapshot_hash | assumption_set_hash | scenario_set_hash | valuation_hash | audit_hash | freeze_token_hash }}`
- 보조 결속정보: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | 해당 없음 }}`
- 실패 점검 기술 식별자: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | probability_reporting_and_history_contract | major_gate_reporting_contract | major_gate_delivery | direct_source_links | 없음 }}`

</details>
"""


def write_verified_report(
    result: ControlledRunResult,
    path: str | Path,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_controlled_run_report(
            result,
            stage_registry_path=stage_registry_path,
        ),
        encoding="utf-8",
    )
    for visual in render_report_visuals(result.data):
        (target.parent / visual.filename).write_text(visual.svg, encoding="utf-8")
    return target
