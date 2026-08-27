from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
import re
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
from .report_localization import (
    calibration_label_ko,
    currency_label_ko,
    identifier_label_ko,
    method_label_ko,
    module_label_ko,
    scenario_label_ko,
    valuation_scope_label_ko,
)
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


def _fmt_money(value: Decimal | float | int, unit: str) -> str:
    number = Decimal(str(value))
    decimals = 0 if unit in {"KRW", "JPY"} else 2
    if decimals == 0:
        return f"{number:,.0f}"
    return f"{number:,.{decimals}f}".rstrip("0").rstrip(".")


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


def _korean_text_or(value: object, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text if re.search(r"[가-힣]", text) else fallback


def _measure_text(assumption: object) -> str:
    measure = getattr(assumption, "measure", None)
    amount = Decimal(str(getattr(measure, "amount", 0)))
    unit = str(getattr(measure, "unit", ""))
    if unit == "ratio":
        return f"{amount * 100:.1f}%"
    if unit == "KRW_billion":
        return f"{amount * 10:,.0f}억원"
    if unit == "USD_million":
        return f"{amount:,.0f}백만 달러"
    if unit == "years":
        return f"{amount:g}년"
    if unit == "shares":
        return f"{amount:,.0f}주"
    return f"{amount:g} {unit}".strip()


def _scenario_assumptions_line(scenario: object) -> str:
    labels = (
        ("1년차 기업잉여현금흐름", "fcff_year_1"),
        ("5년차 기업잉여현금흐름", "fcff_year_5"),
        ("영구성장률", "terminal_growth"),
        ("영구 투하자본이익률", "terminal_roic"),
    )
    values: list[str] = []
    for label, key in labels:
        try:
            assumption = scenario.get(key)  # type: ignore[attr-defined]
        except (AttributeError, KeyError):
            continue
        values.append(f"{label} {_measure_text(assumption)}")
    return " · ".join(values)


def _market_interpretation(
    market: MarketComparisonBundle | None,
) -> str:
    if market is None:
        return "현재 시장가격이 확보되지 않아 내재가치와의 차이는 제시하지 않습니다."
    preferred = next(
        (
            item
            for scenario_id in ("Core", "Base")
            for item in market.envelope.scenario_gaps
            if item.scenario_id == scenario_id
        ),
        None,
    )
    if preferred is None:
        return "현재가와 각 시나리오의 내재가치를 개별 비교해야 합니다."
    pct = preferred.gap_pct_of_reference
    if pct < 0:
        return f"기준 내재가치는 현재가보다 {abs(pct):.1%} 낮습니다. 상방 시나리오의 실현 조건 확인이 필요합니다."
    if pct > 0:
        return f"기준 내재가치는 현재가보다 {pct:.1%} 높습니다. 하방 위험과 가정 실현 여부를 함께 점검해야 합니다."
    return "현재가는 기준 내재가치와 같은 수준입니다. 추가 상승여력은 상방 가정의 실현 여부에 달려 있습니다."


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
    scenario_set = data.get("bound_scenario_set")
    calibration_status = getattr(
        getattr(scenario_set, "calibration_status", None), "value", "UNCALIBRATED"
    )
    calibration_applied = bool(
        getattr(scenario_set, "numeric_weighting_allowed", False)
    )
    market = data.get("market_comparison")
    market_bundle = market if isinstance(market, MarketComparisonBundle) else None
    thesis = _korean_text_or(
        _current_thesis(data),
        "공식 근거와 결정론적 가치평가를 바탕으로 시나리오별 내재가치를 비교했습니다.",
    )
    if len(thesis) > 420:
        thesis = thesis[:419].rstrip() + "…"
    entry_posture = (
        "실제 해결 이력 기반 확률 보정과 별도 진입 규칙이 모두 갖춰지기 전까지 "
        "구체적인 매수가는 제시하지 않습니다."
        if not calibration_applied
        else "확률가중 값은 참고할 수 있으나 별도 진입 규칙이 없어 구체적인 매수가는 제시하지 않습니다."
    )
    values = tuple(item.value_per_share for item in valuation.scenarios)
    value_range = (
        f"주당 {_fmt_money(min(values), valuation.reporting_unit)}~"
        f"{_fmt_money(max(values), valuation.reporting_unit)}"
        f"{currency_label_ko(valuation.reporting_unit)}"
        if values
        else "미산출"
    )
    lines = [
        f"# {company} 리서치·가치평가 보고서",
        "",
        "## 투자 요약",
        f"- **핵심 판단:** {thesis}",
        f"- **가치평가 범위:** {value_range}",
        f"- **현재가 해석:** {_market_interpretation(market_bundle)}",
        f"- **매수 판단:** {entry_posture}",
        "",
        (
            "## 부분 내재가치 — 평가 완료 사업부만 포함"
            if partial
            else "## 가치평가"
        ),
    ]
    if partial:
        lines.append(
            "- 평가범위: 평가 완료 사업부 소계이며 전체 기업가치가 아닙니다."
        )
    for item in valuation.scenarios:
        label = "평가완료 소계" if partial else "내재가치"
        lines.append(
            f"- **{scenario_label_ko(item.scenario_id)} 시나리오:** {label} 주당 "
            f"{_fmt_money(item.value_per_share, valuation.reporting_unit)}"
            f"{currency_label_ko(valuation.reporting_unit)}"
        )
    if valuation.expected_value_per_share is None:
        lines.append(
            "- **확률가중 기대값:** 미산출 — 실제 해결 이력 기반 보정이 끝나지 않아 수치 가중을 보류했습니다."
        )
    elif partial:
        lines.append(
            f"- **부분 확률가중 소계:** 주당 {_fmt_money(valuation.expected_value_per_share, valuation.reporting_unit)}"
            f"{currency_label_ko(valuation.reporting_unit)} — 전체 기업 공정가치로 사용하지 않습니다."
        )
    else:
        lines.append(
            f"- **확률가중 기대값:** 주당 {_fmt_money(valuation.expected_value_per_share, valuation.reporting_unit)}"
            f"{currency_label_ko(valuation.reporting_unit)}"
        )

    methods = tuple(data.get("selected_methods", ()))
    method_labels = tuple(dict.fromkeys(method_label_ko(item) for item in methods))
    beta_result = data.get("live_beta_result")
    wacc_result = data.get("live_wacc_result")
    beta = getattr(beta_result, "target_levered_beta", None)
    wacc = getattr(getattr(wacc_result, "wacc_result", None), "wacc", None)
    lines.extend((
        "",
        "## 핵심 가정과 위험",
        f"- **평가방법:** {', '.join(method_labels) if method_labels else '등록된 결정론적 가치평가법'}",
        f"- **위험 입력:** 계층형 베타 {beta:.3f} · 가중평균자본비용 {wacc:.3%}"
        if beta is not None and wacc is not None
        else "- **위험 입력:** 선택된 평가방법에서 별도 베타·가중평균자본비용을 요구하지 않습니다.",
        f"- **확률 보정:** {calibration_label_ko(calibration_status)} · 수치 가중 {'적용' if calibration_applied else '보류'}",
    ))
    for scenario in tuple(getattr(scenario_set, "scenarios", ()))[:3]:
        assumptions = _scenario_assumptions_line(scenario)
        if assumptions:
            lines.append(
                f"- **{scenario_label_ko(getattr(scenario, 'scenario_id', ''))} 가정:** {assumptions}"
            )
    capacity = data.get("capacity_commitment_assessment")
    projects = tuple(getattr(capacity, "core_inclusion_required_projects", ()))
    if projects:
        lines.append(
            "- **기준 시나리오 생산능력:** "
            + ", ".join(identifier_label_ko(item) for item in projects)
        )
    if not calibration_applied:
        lines.append(
            "- **핵심 제약:** 실제 해결 전망의 누적 이력이 부족해 시나리오 확률과 기대값을 투자판단에 사용할 수 없습니다."
        )

    if partial:
        lines.extend(("", "## 미평가 사업부 — 0원으로 간주하지 않음"))
        for item in valuation.unvalued_segments:
            missing = (
                f"; 누락 가정={', '.join(item.missing_assumptions)}"
                if item.missing_assumptions
                else ""
            )
            rationale = _korean_text_or(
                item.rationale,
                "가치평가에 필요한 근거 또는 가정이 부족합니다.",
            )
            lines.append(
                f"- {item.segment_id}: {rationale}{missing}"
            )
        lines.append("- 미평가 사업부는 0원으로 합산하지 않았습니다.")

    street = data.get("street_comparison")
    lines.extend(("", "## 증권사·시장 비교"))
    if isinstance(street, StreetComparisonBundle):
        lines.extend((
            f"- **증권사 평균 목표가:** {_fmt_money(street.consensus.mean_target_price, street.consensus.target_price_currency)}"
            f"{currency_label_ko(street.consensus.target_price_currency)} ({street.consensus.report_count}건)",
        ))
        reference_gap = next(
            (
                item
                for scenario_id in ("Core", "Base")
                for item in street.envelope.scenario_gaps
                if item.scenario_id == scenario_id
            ),
            None,
        )
        if reference_gap is not None and reference_gap.gap_pct_of_reference == 0:
            lines.append("- 증권사 평균 목표가는 기준 내재가치와 같습니다.")
        elif reference_gap is not None:
            lines.append(
                "- 증권사 평균 목표가는 기준 내재가치보다 "
                f"{abs(reference_gap.gap_pct_of_reference):.1%} "
                f"{'높습니다' if reference_gap.gap_pct_of_reference < 0 else '낮습니다'}."
            )
    elif data.get("street_comparison_withheld_reason"):
        lines.append("- **증권사 목표가:** 비교를 보류했습니다.")
    else:
        lines.append("- **증권사 목표가:** 확보되지 않았습니다.")

    if market_bundle is not None:
        lines.append(
            f"- **현재가:** {_fmt_money(market_bundle.observation.price, market_bundle.envelope.currency)}"
            f"{currency_label_ko(market_bundle.envelope.currency)} ({market_bundle.observation.as_of})"
        )
        for item in market_bundle.envelope.scenario_gaps:
            direction = "상승여력" if item.gap_per_share >= 0 else "하락위험"
            lines.append(
                f"- **{scenario_label_ko(item.scenario_id)} 대비 {direction}:** "
                f"{_fmt_money(abs(item.gap_per_share), market_bundle.envelope.currency)}"
                f"{currency_label_ko(market_bundle.envelope.currency)} "
                f"({abs(item.gap_pct_of_reference):.1%})"
            )
    elif data.get("market_comparison_withheld_reason"):
        lines.append("- **현재가:** 비교를 보류했습니다.")
    else:
        lines.append("- **현재가:** 확보되지 않았습니다.")

    lines.extend(("", *render_context_strength_linkage_section(data)))

    lines.extend((
        "",
        "## 최종 요약 이미지",
        f"![{company} 회사 강점·투자 결론·가치평가]({summary_visual})",
        "",
        f"![{company} 가치평가 가정·위험·출처]({assumptions_visual})",
    ))

    source_links = build_source_link_index(
        data,
        require_all_http=require_verifiable_sources,
    )
    lines.extend(("", *render_source_link_section(source_links)))

    non_pass = tuple(
        item for item in coverage
        if item.status not in {StageStatus.PASS, StageStatus.WARNING, StageStatus.SKIPPED_NOT_APPLICABLE}
    )
    lines.extend((
        "",
        "## 분석 범위와 유의사항",
        f"- **평가범위:** {valuation_scope_label_ko(valuation.scope.value)}",
        f"- **검증:** 결정론적 감사 {len(audit.findings)}개 점검 통과 · 원칙 준수 {len(coverage) - len(non_pass)}/{len(coverage)}개 허용 상태",
        "- 회사 공시 사실, 분석가 가정, 인공지능 연결 인사이트를 구분해 표시했습니다.",
        "- 증권사 목표가와 현재가는 내재가치 고정 이후 비교용으로만 불러왔으며 같은 실행의 가정을 바꾸지 않았습니다.",
    ))
    for item in non_pass:
        lines.append(
            f"- 추가 확인: {module_label_ko(item.module_id)} — "
            f"{_korean_text_or(item.rationale, '검토가 완료되지 않았습니다.')}"
        )
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
