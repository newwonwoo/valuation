from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
from decimal import Decimal
from enum import Enum
from datetime import date, datetime
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
from .orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
)
from .post_freeze import MarketComparisonBundle, StreetComparisonBundle
from .probability_forecasting import (
    ProbabilityForecastDraft,
    ProbabilityForecastHistoryStore,
    ScenarioProbabilityAssessment,
)
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
from .street import StreetResearchReport
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
    if isinstance(value, (date, datetime)):
        return value.isoformat()
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


def _probability_percent(value: Decimal | float | int) -> str:
    return f"{Decimal(str(value)) * 100:.0f}%"


def _street_method_label_ko(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized == "dcf":
        return "현금흐름할인법"
    if normalized == "per":
        return "주가수익비율법"
    if normalized == "per-based target framework":
        return "주가수익비율 기반 목표가"
    if normalized == "broker target-price framework":
        return "증권사 목표가 산정 방식"
    match = re.fullmatch(r"(\d{4})e per ([0-9.]+)x", normalized)
    if match:
        return f"예상 주가수익비율 {match.group(2)}배"
    return value


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
    return _amount_unit_text(amount, unit)


def _amount_unit_text(amount: Decimal, unit: str) -> str:
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
    if unit == "multiple":
        return f"{amount:g}배"
    return f"{amount:g} {unit}".strip()


def _scenario_assumptions_line(scenario: object) -> str:
    values: list[str] = []
    assumptions = tuple(getattr(scenario, "assumptions", ()))
    by_key = {
        str(getattr(item, "key", "")): item
        for item in assumptions
        if getattr(item, "key", None)
    }
    ebitda_keys = tuple(
        key for key in by_key if key.endswith("normalized_ebitda")
    )
    segment_labels = {
        "manufacturing": "제조",
        "trading": "수출입",
        "recycling": "기타",
    }
    for ebitda_key in ebitda_keys:
        prefix = ebitda_key.removesuffix("normalized_ebitda")
        multiple = by_key.get(f"{prefix}normalized_multiple") or by_key.get(
            f"{prefix}normalized_ebitda_multiple"
        )
        segment_id = prefix.rstrip("_")
        label = segment_labels.get(segment_id, segment_id or "핵심")
        detail = f"{label} EBITDA {_measure_text(by_key[ebitda_key])}"
        if multiple is not None:
            detail += f" × {_measure_text(multiple)}"
        values.append(detail)
    nav_asset_keys = tuple(
        key for key in by_key if key.endswith("gross_asset_value")
    )
    for asset_key in nav_asset_keys:
        prefix = asset_key.removesuffix("gross_asset_value")
        liabilities = by_key.get(f"{prefix}liabilities")
        if liabilities is None:
            continue
        asset_measure = by_key[asset_key].measure
        liability_measure = liabilities.measure.convert_to(asset_measure.unit)
        nav_amount = asset_measure.amount - liability_measure.amount
        segment_id = prefix.rstrip("_")
        label = segment_labels.get(segment_id, segment_id or "핵심")
        values.append(
            f"{label} 유형자산 NAV "
            f"{_amount_unit_text(nav_amount, asset_measure.unit)}"
        )
    ownerships = tuple(
        item for key, item in by_key.items() if key.endswith("ownership")
    )
    common_ownership: Decimal | None = None
    if ownerships:
        ownership_values = tuple(
            item.measure.convert_to("ratio").amount for item in ownerships
        )
        if len(set(ownership_values)) == 1:
            common_ownership = ownership_values[0]
            values.append(
                f"공통 지배주주 귀속률 {common_ownership * 100:.4f}%"
            )
        else:
            values.extend(
                f"{key.removesuffix('_ownership')} 귀속률 "
                f"{item.measure.convert_to('ratio').amount * 100:.4f}%"
                for key, item in by_key.items()
                if key.endswith("ownership")
            )
    adjustments = tuple(
        item
        for key, item in by_key.items()
        if key.endswith("ev_adjustment")
    )
    if adjustments:
        first_measure = adjustments[0].measure
        total = sum(
            (
                item.measure.convert_to(first_measure.unit).amount
                for item in adjustments
            ),
            Decimal(0),
        )
        values.append(
            f"EV→지분 조정 {_amount_unit_text(total, first_measure.unit)}"
        )
    shares = by_key.get("diluted_shares")
    if shares is not None:
        share_count = Decimal(str(shares.measure.amount))
        values.append(f"주당 분모 {share_count / Decimal('1000000'):,.3f}백만주")
        if len(ebitda_keys) + len(nav_asset_keys) > 1 and common_ownership is not None:
            value_terms: list[str] = []
            if ebitda_keys:
                value_terms.append("부문 EBITDA×배수 합")
            if nav_asset_keys:
                value_terms.append("유형자산 NAV")
            if adjustments:
                value_terms.append("EV→지분 조정")
            values.append(
                f"산식 [{'+'.join(value_terms)}]"
                f"×{common_ownership * 100:.4f}%÷{share_count:,.0f}주"
            )

    for year in (1, 5):
        key = f"fcff_year_{year}"
        try:
            base = scenario.get(key)  # type: ignore[attr-defined]
        except (AttributeError, KeyError):
            continue
        base_measure = getattr(base, "measure", None)
        base_amount = Decimal(str(getattr(base_measure, "amount", 0)))
        unit = str(getattr(base_measure, "unit", ""))
        try:
            incremental = scenario.get(f"uhv_fcff_year_{year}")  # type: ignore[attr-defined]
        except (AttributeError, KeyError):
            values.append(f"{year}년차 DCF 사용 FCFF {_measure_text(base)}")
            continue
        incremental_measure = getattr(incremental, "measure", None)
        incremental_amount = Decimal(
            str(getattr(incremental_measure, "amount", 0))
        )
        total = base_amount + incremental_amount
        values.append(
            f"{year}년차 DCF 사용 FCFF {_amount_unit_text(total, unit)} "
            f"(기존 {_amount_unit_text(base_amount, unit)} + "
            f"증분 {_amount_unit_text(incremental_amount, unit)})"
        )

    try:
        growth = scenario.get("terminal_growth")  # type: ignore[attr-defined]
        roic = scenario.get("terminal_roic")  # type: ignore[attr-defined]
    except (AttributeError, KeyError):
        return " · ".join(values)
    growth_amount = Decimal(str(growth.measure.amount))
    roic_amount = Decimal(str(roic.measure.amount))
    values.append(f"영구성장률 {_measure_text(growth)}")
    if roic_amount > 0:
        reinvestment = growth_amount / roic_amount
        values.append(
            f"영구 ROIC {_measure_text(roic)}"
            f" (성장률 검산·재투자율 {reinvestment * 100:.1f}%)"
        )
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
    street = data.get("street_comparison")
    street_bundle = street if isinstance(street, StreetComparisonBundle) else None
    thesis = _korean_text_or(
        _current_thesis(data),
        "공식 근거와 결정론적 가치평가를 바탕으로 시나리오별 내재가치를 비교했습니다.",
    )
    thesis = re.sub(r"[.!?]\s+", " · ", thesis).rstrip(".!? ")
    if len(thesis) > 280:
        thesis = thesis[:279].rstrip() + "…"
    if thesis[-1:] not in {".", "!", "?", "…"}:
        thesis += "."
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
    reference_scenario = next(
        (
            item
            for scenario_id in ("Core", "Base")
            for item in valuation.scenarios
            if item.scenario_id == scenario_id
        ),
        valuation.scenarios[0] if valuation.scenarios else None,
    )
    reference_value = (
        "미산출"
        if reference_scenario is None
        else (
            f"주당 {_fmt_money(reference_scenario.value_per_share, valuation.reporting_unit)}"
            f"{currency_label_ko(valuation.reporting_unit)}"
        )
    )
    current_price = (
        "미확보"
        if market_bundle is None
        else (
            f"{_fmt_money(market_bundle.observation.price, market_bundle.envelope.currency)}"
            f"{currency_label_ko(market_bundle.envelope.currency)}"
            f" ({market_bundle.observation.as_of})"
        )
    )
    street_reference = (
        "미확보"
        if street_bundle is None
        else (
            f"{_fmt_money(street_bundle.consensus.mean_target_price, street_bundle.consensus.target_price_currency)}"
            f"{currency_label_ko(street_bundle.consensus.target_price_currency)}"
            f" ({street_bundle.consensus.report_count}건, 가치평가 확정 후 참고)"
        )
    )
    probability_assessment = data.get("scenario_probability_assessment")
    probability_summary = "미산출"
    if isinstance(probability_assessment, ScenarioProbabilityAssessment):
        probability_summary = " · ".join(
            f"{scenario_label_ko(item.scenario_id)} {_probability_percent(item.displayed_probability)}"
            for item in probability_assessment.rows
        ) + " (미보정·기대값 미적용)"
    reference_label = "평가 완료 사업부 소계" if partial else "기준 내재가치"
    range_label = "평가 완료 사업부 범위" if partial else "가치평가 범위"
    lines = [
        f"# {company} 투자보고서",
        "",
        "## 투자 요약",
        "",
        "| 핵심 판단 항목 | 내용 |",
        "| --- | --- |",
        f"| **투자판단** | 판단 유보 — {entry_posture} |",
        f"| **현재가** | {current_price} |",
        f"| **{reference_label}** | {reference_value} |",
        f"| **{range_label}** | {value_range} |",
        f"| **시나리오 가능성** | {probability_summary} |",
        f"| **증권사 참고값** | {street_reference} |",
        "",
        "### 한 문장 결론",
        "",
        thesis,
        "",
        "### 투자포인트",
        "",
        f"- **가치동인:** {thesis}",
        f"- **현재가 대비:** {_market_interpretation(market_bundle)}",
        (
            "- **남은 제약:** 실제 해결 이력 기반 확률 보정이 없어 시나리오 기대값과 구체 매수가를 사용하지 않습니다."
            if not calibration_applied
            else "- **남은 제약:** 확률가중 값과 별개로 검증된 진입 규칙이 없어 구체 매수가를 사용하지 않습니다."
        ),
        "",
        "### 판단 변경 조건",
        "",
        "- **상방 확인:** 기준·상방 가정의 핵심 동인이 공시 실적과 현금흐름으로 전환되면 판단 근거가 강화됩니다.",
        "- **하방 훼손:** 핵심 가정이 미달하거나 하방 시나리오의 조건이 현실화되면 가치평가 신뢰도와 행동 여력이 낮아집니다.",
        f"- **행동 가능 조건:** {entry_posture}",
        "",
        (
            "## 가치평가 — 부분 내재가치 — 평가 완료 사업부만 포함"
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
    if isinstance(probability_assessment, ScenarioProbabilityAssessment):
        lines.extend(
            (
                "",
                "### 시나리오 발생 가능성 — 미보정 분석가 사전확률",
                "",
                "| 시나리오 | 상대점수 | 표시 확률 | 판단 근거 |",
                "| --- | ---: | ---: | --- |",
            )
        )
        for item in probability_assessment.rows:
            lines.append(
                f"| {scenario_label_ko(item.scenario_id)} | {_fmt(item.relative_score)} | "
                f"{_probability_percent(item.displayed_probability)} | {item.rationale} |"
            )
        lines.extend(
            (
                "",
                "- **산출식:** 각 시나리오의 명시적 상대점수를 전체 점수로 나눠 정규화하고, 표시는 5% 단위로 반올림했습니다.",
                "- **사용 제한:** 분석가 사전확률이며 실제 해소 이력으로 보정되지 않았으므로 기대가치·매수판단에는 사용하지 않습니다.",
                f"- **기준일·기간:** {probability_assessment.as_of_date} · {probability_assessment.horizon}",
            )
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
    forecast_drafts = data.get("probability_forecast_drafts", ())
    if isinstance(forecast_drafts, tuple) and forecast_drafts and all(
        isinstance(item, ProbabilityForecastDraft) for item in forecast_drafts
    ):
        lines.extend(
            (
                "",
                "### 사전에 기록한 사건 예측 — 보정 이력 적립용",
                "",
                "| 사건 | 미보정 확률 | 해소기한 | 해소 기준 |",
                "| --- | ---: | --- | --- |",
            )
        )
        for item in forecast_drafts:
            lines.append(
                f"| {item.event_definition} | {item.displayed_band} | "
                f"{item.evaluation_deadline.isoformat()} | {item.resolution_rule} |"
            )
        lines.append(
            "- 위 예측은 분석 당시 값과 이후 변경 이력을 함께 저장하며, 사후 공시를 보고 과거 확률을 다시 쓰지 않습니다."
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
                "- PRISM 기준 내재가치는 증권사 평균 목표가보다 "
                f"{abs(reference_gap.gap_pct_of_reference):.1%} "
                f"{'낮습니다' if reference_gap.gap_pct_of_reference < 0 else '높습니다'}."
            )

        street_reports = data.get("street_reports", ())
        if (
            reference_scenario is not None
            and isinstance(street_reports, tuple)
            and street_reports
            and all(isinstance(item, StreetResearchReport) for item in street_reports)
        ):
            reference_amount = Decimal(str(reference_scenario.value_per_share))
            lines.extend(
                (
                    "",
                    "### 증권사별 목표가와 PRISM의 차이",
                    "",
                    "| 증권사 | 목표가 | 적용 기준 | PRISM 기준가 대비 |",
                    "| --- | ---: | --- | ---: |",
                )
            )
            for report in street_reports:
                premium = Decimal(str(report.target_price)) / reference_amount - 1
                lines.append(
                    f"| {identifier_label_ko(report.broker)} | "
                    f"{_fmt_money(report.target_price, report.target_price_currency)}"
                    f"{currency_label_ko(report.target_price_currency)} | "
                    f"{report.base_year}년 기준 · {_street_method_label_ko(report.valuation_method)} | "
                    f"{premium:+.1%} |"
                )

            selected_method_ids = tuple(
                item
                for item in data.get("selected_methods", ())
                if isinstance(item, str)
            )
            selected_method_labels = tuple(
                method_label_ko(item)
                for item in selected_method_ids
            )
            prism_method = ", ".join(selected_method_labels) or "결정론적 내재가치 평가"
            street_basis = " · ".join(
                f"{identifier_label_ko(report.broker)}: {report.base_year}년 "
                f"{_street_method_label_ko(report.valuation_method)}"
                for report in street_reports
            )
            prism_uses_dcf = any("dcf" in item.casefold() for item in selected_method_ids)
            street_uses_per_only = all(
                "per" in report.valuation_method.casefold()
                for report in street_reports
            )
            if prism_uses_dcf and street_uses_per_only:
                method_difference = (
                    f"PRISM은 {prism_method}으로 현금흐름을 현재가치화하지만, "
                    "증권사는 미래 이익에 목표 주가수익비율을 적용합니다."
                )
            else:
                method_difference = (
                    f"PRISM은 {prism_method}을 사용하며, 증권사별 평가방법은 표와 같습니다. "
                    "현금흐름·이익·할인율·적용 배수의 기준이 다르면 목표가도 달라집니다."
                )
            lines.extend(
                (
                    "",
                    "### 왜 차이가 나는가",
                    "",
                    f"- **평가방법:** {method_difference}",
                    f"- **기준시점:** {street_basis}. 평가 기준과 기준연도가 다르므로 목표가를 PRISM 기준가와 동일한 숫자로 볼 수 없습니다.",
                )
            )
            if len(street_reports) >= 2:
                lowest = min(street_reports, key=lambda item: item.target_price)
                highest = max(street_reports, key=lambda item: item.target_price)
                broker_spread = Decimal(str(highest.target_price)) - Decimal(
                    str(lowest.target_price)
                )
                broker_spread_pct = broker_spread / Decimal(str(lowest.target_price))
                lines.append(
                    f"- **증권사 간 차이:** {identifier_label_ko(highest.broker)} 목표가는 "
                    f"{identifier_label_ko(lowest.broker)}보다 "
                    f"{_fmt_money(broker_spread, highest.target_price_currency)}"
                    f"{currency_label_ko(highest.target_price_currency)} "
                    f"({broker_spread_pct:.1%}) 높습니다. 두 보고서의 기준연도와 평가방법이 달라 "
                    "목표가 차이를 단순 평균으로 해석하면 안 됩니다."
                )
            capacity_assessment = data.get("capacity_commitment_assessment")
            if getattr(
                capacity_assessment,
                "core_inclusion_required_projects",
                (),
            ):
                lines.append(
                    "- **증설 처리:** PRISM은 공시된 자본적지출과 가동 정상화 경로를 현금흐름에 직접 반영하고, 정확한 추가 생산능력이 미공시된 부분은 확정 이익으로 앞당기지 않았습니다."
                )
            if any(not report.estimates for report in street_reports):
                lines.append(
                    "- **분해 한계:** 현재 확보된 증권사 자료에는 목표가·평가방법·기준연도는 있으나 모든 세부 이익 추정치가 구조화되어 있지 않아, 차이를 이익 전망과 적용 배수로 완전히 분해할 수는 없습니다."
                )
            if all(
                Decimal(str(report.target_price)) > reference_amount
                for report in street_reports
            ):
                lines.append(
                    "- **해석:** 증권사 목표가를 지지하려면 PRISM보다 낙관적인 미래 이익·현금흐름이 실현되거나 목표 배수가 유지되어야 합니다. 차이 자체가 계산 오류를 뜻하지는 않습니다."
                )
            else:
                lines.append(
                    "- **해석:** 증권사 목표가는 각 보고서의 현금흐름·이익·할인율·배수 가정을 반영한 참고값입니다. PRISM 결과와의 차이 자체가 계산 오류를 뜻하지는 않습니다."
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
    blocking_findings = tuple(item for item in audit.findings if item.blocking)
    blocking_passed = sum(item.passed for item in blocking_findings)
    nonblocking_failed = sum(
        not item.passed and not item.blocking for item in audit.findings
    )
    lines.extend((
        "",
        "## 분석 범위와 유의사항",
        f"- **평가범위:** {valuation_scope_label_ko(valuation.scope.value)}",
        f"- **계산 확인:** 차단 점검 {blocking_passed}/{len(blocking_findings)}개 통과 · "
        f"비차단 확인 필요 {nonblocking_failed}건 · "
        f"분석 원칙 {len(coverage) - len(non_pass)}/{len(coverage)}개 충족",
        "- 회사 공시 사실, 분석가 가정, 인공지능 연결 인사이트를 구분해 표시했습니다.",
        "- 증권사 목표가와 현재가는 가치평가를 마친 뒤 참고했으며, 앞서 계산한 가정을 바꾸는 데 사용하지 않았습니다.",
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


def finalize_live_primary_run_artifacts(
    result: ControlledRunResult,
    *,
    state_root: str | Path,
    stage_registry_path: str | Path,
) -> ControlledRunResult:
    """Finalize reader and trace artifacts only after all 33 stages are terminal."""
    if result.blocked_reasons or result.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        return result
    if not result.stage_traces:
        return result
    if result.stage_traces[-1].stage != "FINAL_REPORT":
        raise ValueError("completed LIVE_PRIMARY run requires a terminal FINAL_REPORT trace")
    from .report_form import render_controlled_run_report

    full_report = render_controlled_run_report(
        result,
        stage_registry_path=stage_registry_path,
    )
    ticker = result.data.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("completed LIVE_PRIMARY run requires ticker for final persistence")
    StateStore(state_root).finalize_completed_run_artifacts(
        ticker=ticker,
        run_id=result.run_id,
        final_report=full_report,
        control_plane_trace=_jsonable(result.stage_traces),
    )
    data = dict(result.data)
    data["saved_report_markdown"] = full_report
    data["final_report"] = full_report
    return replace(result, data=data)


def save_state_adapter(
    *,
    state_root: str | Path,
    learning_store: ResearchLearningStore | None = None,
    probability_history_store: ProbabilityForecastHistoryStore | None = None,
) -> StageAdapter:
    root = Path(state_root)
    store = StateStore(root)
    if learning_store is not None and learning_store.root.resolve() != root.resolve():
        raise ValueError("state and research-learning stores must share one root")
    if (
        probability_history_store is not None
        and probability_history_store.root.resolve() != root.resolve()
    ):
        raise ValueError("state and probability-history stores must share one root")

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
        if probability_history_store is not None:
            reserved_output_keys.update(
                {
                    "probability_forecast_record_path",
                    "probability_forecast_record_hash",
                    "probability_forecast_recorded_at",
                    "probability_forecast_ids",
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
        probability_ref = None
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
                "scenario_probability_assessment.json": _jsonable(
                    context.data.get("scenario_probability_assessment")
                ),
                "probability_forecast_drafts.json": _jsonable(
                    context.data.get("probability_forecast_drafts", ())
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
                "reverse_dcf.json": _jsonable(context.data.get("reverse_dcf_context")),
                "evidence_composition.json": _jsonable(
                    context.data.get("evidence_composition_report")
                ),
                "valuation_sensitivity.json": _jsonable(
                    context.data.get("valuation_sensitivity_report")
                ),
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
            forecast_drafts = context.data.get("probability_forecast_drafts", ())
            if not isinstance(forecast_drafts, tuple) or not all(
                isinstance(item, ProbabilityForecastDraft)
                for item in forecast_drafts
            ):
                raise ValueError("probability forecast drafts must be typed")
            if probability_history_store is not None and forecast_drafts:
                probability_ref = probability_history_store.save_forecast_run(
                    ticker=ticker,
                    run_id=context.run_id,
                    drafts=forecast_drafts,
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
                "scenario_probability_assessment_hash": context.data.get(
                    "scenario_probability_assessment_hash"
                ),
                "probability_forecast_record_hash": (
                    probability_ref.content_hash
                    if probability_ref is not None
                    else None
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
                if probability_ref is not None:
                    try:
                        expected_probability = (
                            Path("calibration")
                            / "forecast-runs"
                            / ticker
                            / f"{context.run_id}.json"
                        )
                        _rollback_exact_path(
                            root, probability_ref.path, expected_probability
                        )
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            f"probability-history rollback: {rollback_exc}"
                        )
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
        if probability_ref is not None:
            outputs.update(
                {
                    "probability_forecast_record_path": probability_ref.path,
                    "probability_forecast_record_hash": probability_ref.content_hash,
                    "probability_forecast_recorded_at": probability_ref.recorded_at,
                    "probability_forecast_ids": probability_ref.forecast_ids,
                }
            )
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
