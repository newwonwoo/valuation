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
from .source_reporting import build_source_link_index
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
    source_links_bound = bool(source_links) and isinstance(persisted_report, str) and all(
        item.url in persisted_report for item in source_links
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
            "동일 실행에서 내재가치 고정 토큰을 발급했습니다",
            "내재가치 고정 토큰이 발급되지 않았습니다",
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
                "고정 전 증권사 자료가 분리되고 1차 출처 검증 및 감사에 결속되었습니다",
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
    status = (
        "차단 (`BLOCKED`)"
        if result.blocked_reasons
        else (
            "검증·고정 완료 (`VERIFIED_FROZEN`)"
            if attestation.passed
            else "검증 미완료 (`INCOMPLETE`)"
        )
    )
    data = result.data
    reporting_contract = load_reporting_contract(stage_registry_path)
    broker_configured = bool(data.get("broker_research_required", False)) or (
        data.get("broker_research_prefreeze_result") is not None
    )
    passed_checks = sum(item.passed for item in attestation.checks)
    failed_checks = tuple(item for item in attestation.checks if not item.passed)
    lines = [
        "# PRISM 검증·통제 실행 보고서",
        "",
        f"- 실행 ID: `{result.run_id}`",
        f"- 실행 모드: `{result.execution_mode.value}`",
        f"- 실행 상태: **{status}**",
        f"- 검증증명 해시: `{attestation.attestation_hash}`",
        "",
        "## 실행 검증",
        f"- 점검 결과: **{passed_checks}/{len(attestation.checks)} 통과**",
        f"- 표준 단계: **{len(result.stage_traces)}/33개 최종 추적 완료**",
    ]
    for item in failed_checks:
        lines.append(f"- **실패 `{item.check_id}`:** {item.detail}")

    lines.extend(
        (
            "",
            "## 고정된 식별정보 사슬",
            f"- 증거: `{data.get('ledger_snapshot_hash') or '누락'}`",
            f"- 가정: `{data.get('assumption_set_hash') or '누락'}`",
            f"- 시나리오: `{data.get('scenario_set_hash') or '누락'}`",
            f"- 가치평가: `{data.get('valuation_hash') or '누락'}`",
            f"- 감사: `{data.get('audit_hash') or '누락'}`",
            f"- 내재가치 고정: `{getattr(result.freeze_token, 'token_hash', None) or '누락'}`",
        )
    )
    auxiliary = tuple(
        (label, data.get(key))
        for label, key in (
            ("Beta", "beta_snapshot_hash"),
            ("WACC", "wacc_snapshot_hash"),
            ("생산능력 평가", "capacity_commitment_assessment_hash"),
            ("생산능력 반영", "capacity_bridge_consumption_hash"),
            ("생산능력 시나리오", "capacity_scenario_binding_hash"),
            ("생산능력 가치평가", "capacity_valuation_binding_hash"),
            ("생산능력 주가수익비율", "capacity_per_binding_hash"),
            ("생산능력 정합성", "capacity_consistency_hash"),
            ("생산능력 감사", "capacity_audit_hash"),
            *(
                (
                    ("내재가치 고정 전 증권사 자료", "broker_research_snapshot_hash"),
                    ("증권사 자료 감사", "broker_research_audit_hash"),
                )
                if broker_configured
                else ()
            ),
        )
        if data.get(key) is not None
    )
    if auxiliary:
        lines.append(
            "- 보조 결속정보: "
            + " · ".join(f"{label} `{value}`" for label, value in auxiliary)
        )

    lines.extend(("", "## 대형 게이트 완료 요약"))
    for summary in result.major_gate_summaries:
        lines.extend(
            (
                "",
                f"### {summary.ordinal}. {summary.title} — {_stage_status_ko(summary.status)} "
                f"({summary.completed_stage_count}/{summary.expected_stage_count})",
                f"- 결과: {summary.decisive_result}",
                f"- 잔여위험: {summary.residual_risk} · 다음 단계: `{summary.next_action}`",
            )
        )
    if not result.major_gate_summaries:
        lines.extend(("", "### 누락", "- 5개 게이트 보고 계약을 확인할 수 없습니다."))
    if result.reporting_warnings:
        lines.extend(("", "### 보고 전달 경고", ""))
        lines.extend(f"- {item}" for item in result.reporting_warnings)
    lines.extend(
        (
            "",
            "## 최종보고서 편집 계약",
            f"- 본문 목표: {reporting_contract.main_body_target_pages[0]}–{reporting_contract.main_body_target_pages[1]}쪽",
            f"- 감사 부록 목표: {reporting_contract.audit_appendix_target_pages[0]}–{reporting_contract.audit_appendix_target_pages[1]}쪽",
            f"- 전체 상한: {reporting_contract.total_page_cap}쪽",
            f"- 이미지: {reporting_contract.visual_pages_included_in_main_body}장을 본문 {reporting_contract.main_body_target_pages[0]}–{reporting_contract.main_body_target_pages[1]}쪽 안에 포함합니다.",
            f"- 활자: 본문 ≥ {reporting_contract.body_min_pt}pt, 주 제목 ≥ {reporting_contract.primary_heading_min_pt}pt, 절 제목 ≥ {reporting_contract.section_heading_min_pt}pt. 조밀한 대형 표는 금지합니다.",
            "- 필수: 모든 주장의 출처를 `정보 출처 — 원문 직접 검증`의 HTTP(S) 원문 링크에 연결합니다.",
            "- 필수 산출물: 한국어 본문과 함께 투자결론·가치평가 요약 1장, 가치평가 가정·위험·출처 요약 1장을 생성합니다.",
            f"- 인공지능 관여 내용: 결정론적 결과와 분리된 독립 구역으로 표시하고 {reporting_contract.llm_insight_max_chars:,}자 이하로 제한합니다.",
        )
    )

    lines.extend(("", "## 압축 감사 부록 — 33단계 추적"))
    trace_index = {trace.stage: trace for trace in result.stage_traces}
    stage_number = {
        trace.stage: index
        for index, trace in enumerate(result.stage_traces, start=1)
    }
    for gate in reporting_contract.major_gates:
        compact = " · ".join(
            f"{stage_number[stage]} `{stage}`={_stage_status_ko(trace_index[stage].status)}"
            for stage in gate.stages
            if stage in trace_index
        )
        lines.append(f"- **{gate.gate_id}:** {compact or '미실행'}")
    lines.append(
        "- 단계별 정확한 사유와 출력 키는 불변 `control_plane_trace.json` 산출물에 보존됩니다."
    )

    persisted = data.get("final_report")
    lines.extend(("", "## 영구 저장된 리서치 보고서", ""))
    if isinstance(persisted, str) and persisted:
        lines.append(persisted.rstrip())
    else:
        lines.append("이 실행에는 영구 저장된 최종보고서가 없습니다.")
    return "\n".join(lines) + "\n"


def render_report_form_template() -> str:
    return """# PRISM 검증·통제 실행 보고서

- 실행 ID: `{{ run_id }}`
- 실행 모드: `LIVE_PRIMARY`
- 실행 상태: **{{ 검증·고정 완료 | 검증 미완료 | 차단 }}**
- 검증증명 해시: `{{ attestation_hash }}`

## 실행 검증

- 점검 결과: **{{ passed_checks }}/{{ total_checks }} 통과**
- 표준 단계: **{{ terminal_stage_count }}/33개 최종 추적 완료**
- 실패 점검만 표시: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | major_gate_reporting_contract | major_gate_delivery | direct_source_links | 없음 }} — {{ detail }}`

## 고정된 식별정보 사슬

- 증거: `{{ ledger_snapshot_hash }}`
- 가정: `{{ assumption_set_hash }}`
- 시나리오: `{{ scenario_set_hash }}`
- 가치평가: `{{ valuation_hash }}`
- 감사: `{{ audit_hash }}`
- 내재가치 고정: `{{ freeze_token_hash }}`
- 보조 결속정보: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | 해당 없음 }}`

## 대형 게이트 완료 요약

### {{ ordinal }}. {{ title }} — {{ 상태 }} ({{ completed/expected }})

- 결과: `{{ decisive_result }}`
- 잔여위험: `{{ residual_risk }}` · 다음 단계: `{{ next_action }}`

## 최종보고서 편집 계약

- 본문 목표: 3–4쪽
- 감사 부록 목표: 1–2쪽
- 전체 상한: 6쪽
- 이미지 2장은 별도 가산하지 않고 본문 3–4쪽 안에 포함
- 활자: 본문 ≥ 13pt, 주 제목 ≥ 22pt, 절 제목 ≥ 18pt. 조밀한 대형 표는 금지합니다.
- 필수: 모든 주장의 출처를 `정보 출처 — 원문 직접 검증`의 HTTP(S) 원문 링크에 연결합니다.
- 필수 이미지: `회사 강점·투자 결론·가치평가` 1장 + `가치평가 가정·위험·출처` 1장
- 인공지능 관여 내용: 결정론적 결과와 분리한 독립 구역으로 표시하며 1,000자 이하

## 압축 감사 부록 — 33단계 추적

- **{{ gate_id }}:** `{{ stage_number }} {{ stage }}={{ status }}` · …
- 단계별 정확한 사유와 출력 키는 불변 `control_plane_trace.json` 산출물에 보존됩니다.

## 영구 저장된 리서치 보고서

{{ 원문 검증 링크와 한국어 요약 이미지 2장을 포함한 불변 최종보고서 }}
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
