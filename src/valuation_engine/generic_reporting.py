from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
import shutil
from typing import Any

from .ablation import AblationBatchResult, AblationStatus, LoadoutAction
from .context_strength_reporting import (
    context_strength_linkage_artifact,
    context_strength_linkage_state,
    render_context_strength_linkage_section,
)
from .control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    authorize_post_freeze,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .post_freeze import MarketComparisonBundle, StreetComparisonBundle
from .records import AuditReport, RunManifest, RunStatus, iso_now
from .research_learning import ResearchLearningStore
from .state import StateStore, thesis_delta
from .source_reporting import build_source_link_index, render_source_link_section
from .valuation_execution import (
    GenericValuationResult,
    IntrinsicValuationScope,
)
from .visual_reporting import render_report_visuals, report_visual_filenames


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _fmt(value: Decimal | float | int) -> str:
    number = Decimal(str(value))
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _current_thesis(data: dict[str, Any]) -> str:
    direct = data.get("current_thesis")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    intelligence = data.get("intelligence_proposal")
    rationale = getattr(intelligence, "rationale", "")
    return rationale.strip() if isinstance(rationale, str) else ""


def _impact_batch(data: dict[str, Any]) -> AblationBatchResult | None:
    value = data.get("decision_impact_batch")
    return value if isinstance(value, AblationBatchResult) else None


def _module_impact_summary(data: dict[str, Any]) -> dict[str, Any]:
    batch = _impact_batch(data)
    if batch is None:
        return {
            "available": False,
            "measured": [],
            "not_measurable": [],
            "not_applicable": [],
            "failed": [],
            "research_effort": {
                "source_queries": 0,
                "documents_reviewed": 0,
                "llm_calls": 0,
                "elapsed_seconds": 0.0,
            },
            "downrank_candidates": [],
            "recommendations": [],
        }

    buckets: dict[AblationStatus, list[str]] = {status: [] for status in AblationStatus}
    source_queries = 0
    documents_reviewed = 0
    llm_calls = 0
    elapsed_seconds = 0.0
    materiality: dict[str, bool | None] = {}
    for observation in batch.module_observations:
        buckets[observation.status].append(observation.module_id)
        source_queries += observation.effort.source_queries
        documents_reviewed += observation.effort.documents_reviewed
        llm_calls += observation.effort.llm_calls
        elapsed_seconds += observation.effort.elapsed_seconds
        materiality[observation.module_id] = (
            observation.assessment.material if observation.assessment is not None else None
        )

    recommendations = tuple(batch.loadout_recommendations)
    downrank = sorted(
        item.module_id
        for item in recommendations
        if item.action is LoadoutAction.PROPOSE_DOWNRANK
    )
    return {
        "available": True,
        "measured": sorted(buckets[AblationStatus.MEASURED]),
        "not_measurable": sorted(buckets[AblationStatus.NOT_MEASURABLE]),
        "not_applicable": sorted(buckets[AblationStatus.NOT_APPLICABLE]),
        "failed": sorted(buckets[AblationStatus.FAILED]),
        "materiality": materiality,
        "research_effort": {
            "source_queries": source_queries,
            "documents_reviewed": documents_reviewed,
            "llm_calls": llm_calls,
            "elapsed_seconds": elapsed_seconds,
        },
        "downrank_candidates": downrank,
        "recommendations": _jsonable(recommendations),
    }


def _compact_list(values: list[str], *, limit: int = 8) -> str:
    if not values:
        return "없음"
    head = values[:limit]
    suffix = f" 외 {len(values) - limit}개" if len(values) > limit else ""
    return ", ".join(head) + suffix


def _research_effort_line(summary: dict[str, Any]) -> str:
    effort = summary["research_effort"]
    return (
        f"출처 조회 {effort['source_queries']}회, "
        f"문서 검토 {effort['documents_reviewed']}건, "
        f"대규모 언어모델 호출 {effort['llm_calls']}회, "
        f"소요시간 {float(effort['elapsed_seconds']):.1f}초"
    )


def thesis_delta_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        state = context.data.get("company_state", {})
        if not isinstance(state, dict):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "투자논지 변화 비교 전에 company_state가 매핑 형식이어야 합니다",
                blocking=True,
            )
        previous = str(state.get("thesis", ""))
        current = _current_thesis(context.data)
        if not current:
            current = "이번 실행에서는 중대한 투자논지가 새로 생성되지 않았습니다."
        outputs: dict[str, Any] = {
            "thesis_delta_result": thesis_delta(previous, current),
        }
        if "current_thesis" not in context.data:
            outputs["current_thesis"] = current
        return StageExecutionResult(
            StageStatus.PASS,
            "현재 투자논지를 직전 불변 성공 상태와 비교했습니다",
            outputs,
        )

    return run


def render_generic_report(
    data: dict[str, Any],
    *,
    require_verifiable_sources: bool = False,
) -> str:
    company = str(data.get("company", data.get("target_id", "Target")))
    valuation = data.get("generic_valuation_result")
    audit = data.get("generic_audit_report")
    coverage = data.get("doctrine_coverage", ())
    if not isinstance(valuation, GenericValuationResult):
        raise ValueError("GenericValuationResult is required for final report")
    if not isinstance(audit, AuditReport) or not audit.passed:
        raise ValueError("audit-passed generic report is required")
    if not isinstance(coverage, tuple) or not all(isinstance(item, DoctrineCoverageEntry) for item in coverage):
        raise ValueError("typed doctrine coverage is required")

    partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    summary_visual, assumptions_visual = report_visual_filenames(data)
    lines = [
        f"# {company} PRISM 리서치·가치평가 보고서",
        "",
        "## 최종 요약 이미지",
        f"![{company} 회사 강점·투자 결론·가치평가]({summary_visual})",
        "",
        f"![{company} 가치평가 가정·위험·출처]({assumptions_visual})",
        "",
        *render_context_strength_linkage_section(data),
        "",
        (
            "## 부분 내재가치 — 평가 완료 사업부만 포함"
            if partial
            else "## 내재가치"
        ),
    ]
    if partial:
        lines.append(
            "- 평가범위: `PARTIAL_INTRINSIC` — 아래 숫자는 평가 완료 사업부 소계이며 전체 기업가치가 아닙니다."
        )
    for item in valuation.scenarios:
        label = "평가완료 소계" if partial else "내재가치"
        lines.append(
            f"- {item.scenario_id} {label}: 주당 {_fmt(item.value_per_share)} {valuation.reporting_unit}"
        )
    if valuation.expected_value_per_share is None:
        lines.append("- 확률가중 기대값: 미산출 — 시나리오 확률이 보정 완료 상태가 아니므로 수치 가중을 보류했습니다.")
    elif partial:
        lines.append(
            f"- 부분 확률가중 소계: 주당 {_fmt(valuation.expected_value_per_share)} {valuation.reporting_unit} — 전체 기업 공정가치로 사용 금지"
        )
    else:
        lines.append(
            f"- 확률가중 기대값: 주당 {_fmt(valuation.expected_value_per_share)} {valuation.reporting_unit}"
        )

    scenario_set = data.get("bound_scenario_set")
    calibration_status = getattr(
        getattr(scenario_set, "calibration_status", None), "value", "UNCALIBRATED"
    )
    calibration_applied = bool(
        getattr(scenario_set, "numeric_weighting_allowed", False)
    )
    lines.extend((
        "",
        "## 확률 보정 상태",
        f"- 보정 상태: `{calibration_status}` · 수치 가중: {'적용' if calibration_applied else '보류'}",
        f"- 계보: 데이터셋 `{getattr(scenario_set, 'calibration_dataset_hash', None) or '없음'}` · 스냅샷 `{getattr(scenario_set, 'calibration_snapshot_hash', None) or '없음'}`",
    ))

    if partial:
        lines.extend(("", "## 미평가 사업부 — 0원 처리 금지 (`UNVALUED_NOT_ZERO`)"))
        for item in valuation.unvalued_segments:
            missing = (
                f"; 누락 가정={', '.join(item.missing_assumptions)}"
                if item.missing_assumptions
                else ""
            )
            lines.append(
                f"- {item.segment_id} ({item.asset_id}): {item.status.value} — "
                f"{item.resolution_status}: {item.rationale}{missing}"
            )
        lines.append("- 미평가 사업부는 0원으로 합산하지 않았습니다.")

    street = data.get("street_comparison")
    if isinstance(street, StreetComparisonBundle):
        lines.extend((
            "",
            "## 증권사 목표가 비교",
            f"- 반영 리포트: {street.consensus.report_count}건",
            f"- 평균 목표가: {_fmt(street.consensus.mean_target_price)} {street.consensus.target_price_currency}",
        ))
        for item in street.envelope.scenario_gaps:
            lines.append(
                f"- {item.scenario_id} 대비: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})"
            )
        if street.envelope.expected_gap is not None:
            item = street.envelope.expected_gap
            lines.append(f"- 확률가중 기대값 대비: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})")
    elif data.get("street_comparison_withheld_reason"):
        lines.extend((
            "",
            "## 증권사 목표가 비교",
            f"- 비교 보류: {data['street_comparison_withheld_reason']}",
        ))

    market = data.get("market_comparison")
    if isinstance(market, MarketComparisonBundle):
        lines.extend((
            "",
            "## 현재 시장가격 비교",
            f"- 현재가: {_fmt(market.observation.price)} {market.envelope.currency} ({market.observation.as_of})",
        ))
        for item in market.envelope.scenario_gaps:
            lines.append(
                f"- {item.scenario_id} 기대수익 간격: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})"
            )
        if market.envelope.expected_gap is not None:
            item = market.envelope.expected_gap
            lines.append(f"- 확률가중 기대값 대비 수익 간격: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})")
    elif data.get("market_comparison_withheld_reason"):
        lines.extend((
            "",
            "## 현재 시장가격 비교",
            f"- 비교 보류: {data['market_comparison_withheld_reason']}",
        ))

    source_links = build_source_link_index(
        data,
        require_all_http=require_verifiable_sources,
    )
    lines.extend(("", *render_source_link_section(source_links)))

    impact = _module_impact_summary(data)
    lines.extend((
        "",
        "## 모듈 영향·조사 효율성",
        f"- 측정 완료: {_compact_list(impact['measured'])} · 미측정(NOT_MEASURABLE): {_compact_list(impact['not_measurable'])}",
        f"- 비적용: {_compact_list(impact['not_applicable'])} · 실패: {_compact_list(impact['failed'])}",
        f"- 조사비용: {_research_effort_line(impact)}",
        f"- 하향 검토 후보: {_compact_list(impact['downrank_candidates'])} · 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.",
    ))

    non_pass = tuple(
        item for item in coverage
        if item.status not in {StageStatus.PASS, StageStatus.WARNING, StageStatus.SKIPPED_NOT_APPLICABLE}
    )
    lines.extend((
        "",
        "## 감사·준수 범위",
        f"- 감사: 통과 ({len(audit.findings)}개 점검)",
        f"- 원칙 준수: {len(coverage) - len(non_pass)}/{len(coverage)}개 최종 허용 상태",
    ))
    for item in non_pass:
        lines.append(f"- {item.module_id}: {item.status.value} — {item.rationale}")

    delta = data.get("thesis_delta_result", {})
    if isinstance(delta, dict):
        lines.extend((
            "",
            "## 투자논지 변화",
            f"- 강화·신규: {', '.join(delta.get('strengthened_or_new', [])) or '없음'}",
            f"- 약화·폐기: {', '.join(delta.get('weakened_or_removed', [])) or '없음'}",
        ))

    lines.extend((
        "",
        "## 실행 무결성",
        f"- 평가범위: `{valuation.scope.value}` · 내재가치 고정: `{getattr(data.get('intrinsic_freeze_token'), 'token_hash', '')}`",
        f"- 해시 사슬: 원장 `{data.get('ledger_snapshot_hash', '')}` · 가정 `{data.get('assumption_set_hash', '')}` · 가치평가 `{data.get('valuation_hash', '')}` · 감사 `{data.get('audit_hash', '')}`",
        f"- 확률 보정: 데이터셋 `{data.get('probability_calibration_dataset_hash', '') or '미적용'}` · 스냅샷 `{data.get('probability_calibration_snapshot_hash', '') or '미적용'}`",
    ))
    return "\n".join(lines) + "\n"


def _rollback_exact_path(root: Path, target_value: object, expected_relative: Path) -> None:
    if not isinstance(target_value, str) or not target_value:
        return
    resolved_root = root.resolve()
    resolved_target = Path(target_value).resolve()
    expected_target = (resolved_root / expected_relative).resolve()
    if resolved_target != expected_target:
        raise ValueError(f"rollback target mismatch: {resolved_target}")
    if resolved_target.is_dir():
        shutil.rmtree(resolved_target)
    else:
        resolved_target.unlink(missing_ok=True)


def save_state_adapter(
    *,
    state_root: str | Path,
    learning_store: ResearchLearningStore | None = None,
) -> StageAdapter:
    root = Path(state_root)
    store = StateStore(root)
    if learning_store is not None and learning_store.root.resolve() != root.resolve():
        raise ValueError("state and research-learning stores must share one root")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        reserved_output_keys = {
            "saved_run_dir",
            "saved_current_state",
            "saved_report_markdown",
            "saved_report_visuals",
            "module_impact_summary",
            "final_report",
        }
        if learning_store is not None:
            reserved_output_keys.update(
                {
                    "research_learning_record_path",
                    "research_learning_record_hash",
                    "research_learning_recorded_at",
                }
            )
        collisions = tuple(
            sorted(reserved_output_keys.intersection(context.data))
        )
        if collisions:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "SAVE_STATE reserved output keys already exist before persistence: "
                + ", ".join(collisions),
                blocking=True,
            )

        ticker = context.data.get("ticker")
        company = context.data.get("company")
        learning_ref = None
        run_dir: Path | None = None
        try:
            valuation = context.data.get("generic_valuation_result")
            audit = context.data.get("generic_audit_report")
            token = context.data.get("intrinsic_freeze_token")
            if not isinstance(ticker, str) or not ticker:
                raise ValueError("ticker is required")
            if not isinstance(company, str) or not company:
                raise ValueError("company is required")
            if not isinstance(valuation, GenericValuationResult):
                raise ValueError("GenericValuationResult is required")
            if not isinstance(audit, AuditReport) or not audit.passed:
                raise ValueError("audit PASS is required")
            if token is None or getattr(token, "run_id", None) != context.run_id:
                raise ValueError("same-run IntrinsicFreezeToken is required")
            authorize_post_freeze(token, run_id=context.run_id)

            report_visuals = render_report_visuals(context.data)
            report = render_generic_report(
                context.data,
                require_verifiable_sources=(
                    context.execution_mode is ExecutionMode.LIVE_PRIMARY
                ),
            )
            impact_summary = _module_impact_summary(context.data)
            linkage_artifact = context_strength_linkage_artifact(context.data)
            linkage_state = context_strength_linkage_state(context.data)
            prior = context.data.get("company_state", {})
            parent_run = prior.get("last_completed_run") if isinstance(prior, dict) else None
            now = iso_now()
            rounds = int(context.data.get("research_round_count", 1))
            manifest = RunManifest(
                run_id=context.run_id,
                ticker=ticker,
                company=company,
                started_at=str(context.data.get("run_started_at", now)),
                finished_at=now,
                status=RunStatus.COMPLETED,
                round_count=rounds,
                audit_passed=True,
                parent_run_id=parent_run,
                blocked_reasons=(),
            )
            artifacts = {
                "control_plane_trace.json": _jsonable(tuple(context.stage_traces)),
                "compiled_assumptions.json": _jsonable(context.data.get("compiled_assumption_set")),
                "scenario_set.json": _jsonable(context.data.get("bound_scenario_set")),
                "calibration_certificate.json": _jsonable(
                    context.data.get("probability_calibration_certificate")
                ),
                "valuation.json": _jsonable(valuation),
                "audit.json": _jsonable(audit),
                "doctrine_coverage.json": _jsonable(context.data.get("doctrine_coverage", ())),
                "module_impact.json": {
                    "summary": impact_summary,
                    "batch": _jsonable(_impact_batch(context.data)),
                },
                "context_strength_linkages.json": _jsonable(linkage_artifact),
                "street_compare.json": _jsonable(context.data.get("street_comparison")),
                "market_compare.json": _jsonable(context.data.get("market_comparison")),
                "thesis_delta.json": _jsonable(context.data.get("thesis_delta_result", {})),
                "freeze_token.json": _jsonable(token),
                "final_report.md": report,
                **{visual.filename: visual.svg for visual in report_visuals},
            }
            if learning_store is not None:
                batch = _impact_batch(context.data)
                if batch is None:
                    raise ValueError("Decision Impact batch is required for research-learning save")
                learning_ref = learning_store.save_batch(
                    ticker=ticker,
                    run_id=context.run_id,
                    batch=batch,
                )
            run_dir = store.save_run(manifest, artifacts)
            learning_hash = (
                learning_ref.content_hash
                if learning_ref is not None
                else context.data.get("research_learning_record_hash")
            )
            partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
            current_state = {
                "schema_version": "0.6.13",
                "ticker": ticker,
                "company": company,
                "last_completed_run": context.run_id,
                "last_successful_valuation_run": context.run_id,
                "thesis": _current_thesis(context.data),
                "ledger_snapshot_hash": context.data.get("ledger_snapshot_hash"),
                "assumption_set_hash": context.data.get("assumption_set_hash"),
                "valuation_hash": context.data.get("valuation_hash"),
                "audit_hash": context.data.get("audit_hash"),
                "probability_calibration_status": getattr(
                    context.data.get("probability_calibration_status"), "value", None
                ),
                "probability_weighting_allowed": bool(
                    context.data.get("probability_weighting_allowed", False)
                ),
                "probability_calibration_dataset_hash": context.data.get(
                    "probability_calibration_dataset_hash"
                ),
                "probability_calibration_snapshot_hash": context.data.get(
                    "probability_calibration_snapshot_hash"
                ),
                "decision_impact_hash": context.data.get("decision_impact_hash"),
                "research_learning_record_hash": learning_hash,
                **linkage_state,
                "valuation_scope": valuation.scope.value,
                "full_company_intrinsic_available": valuation.full_company_intrinsic_available,
                "unvalued_segments": _jsonable(valuation.unvalued_segments),
                "scenario_values_scope": valuation.scope.value,
                "scenario_values_per_share": {
                    item.scenario_id: str(item.value_per_share) for item in valuation.scenarios
                },
                "expected_value_per_share": (
                    str(valuation.expected_value_per_share)
                    if valuation.expected_value_per_share is not None and not partial
                    else None
                ),
                "partial_expected_value_per_share": (
                    str(valuation.expected_value_per_share)
                    if valuation.expected_value_per_share is not None and partial
                    else None
                ),
                "freeze_token_hash": token.token_hash,
                "report_visuals": [visual.filename for visual in report_visuals],
            }
            store.promote_current(manifest, current_state)
        except Exception as exc:
            rollback_errors: list[str] = []
            if isinstance(ticker, str) and ticker:
                if run_dir is not None:
                    try:
                        expected_run = Path("runs") / ticker / context.run_id
                        _rollback_exact_path(root, str(run_dir), expected_run)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"run rollback: {rollback_exc}")
                if learning_ref is not None:
                    try:
                        expected_learning = Path("learning") / ticker / "module-impact" / f"{context.run_id}.json"
                        _rollback_exact_path(root, learning_ref.path, expected_learning)
                    except Exception as rollback_exc:
                        rollback_errors.append(f"learning rollback: {rollback_exc}")
            detail = f"state persistence failed: {type(exc).__name__}: {exc}"
            if rollback_errors:
                detail += " | " + " | ".join(rollback_errors)
            return StageExecutionResult(
                StageStatus.BLOCKED,
                detail,
                blocking=True,
            )
        outputs = {
            "saved_run_dir": str(run_dir),
            "saved_current_state": current_state,
            "saved_report_markdown": report,
            "saved_report_visuals": tuple(visual.filename for visual in report_visuals),
            "module_impact_summary": impact_summary,
        }
        if learning_ref is not None:
            outputs.update({
                "research_learning_record_path": learning_ref.path,
                "research_learning_record_hash": learning_ref.content_hash,
                "research_learning_recorded_at": learning_ref.recorded_at,
            })
        return StageExecutionResult(
            StageStatus.PASS,
            "불변 실행 산출물·한국어 보고서·요약 이미지 2장을 저장하고 감사 통과 상태로 승격했습니다",
            outputs,
        )

    return run


def final_report_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        report = context.data.get("saved_report_markdown")
        if not isinstance(report, str) or not report:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "저장된 보고서가 없습니다. 먼저 SAVE_STATE를 완료해야 합니다",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "동일한 불변 실행 데이터에서 한국어 최종보고서와 요약 이미지 2장을 생성했습니다",
            {"final_report": report},
        )

    return run
