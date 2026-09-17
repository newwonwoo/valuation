from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from html import escape
from pathlib import Path
from typing import Any

from .ablation import AblationBatchResult, AblationStatus
from .control_plane import ExecutionMode, StageStatus, authorize_post_freeze
from .distributional_reporting import (
    DistributionalMarketComparison,
    DistributionalStreetComparison,
)
from .distributional_runtime import DistributionalPrimaryValuationResult
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .probability_forecasting import (
    ProbabilityForecastDraft,
    ProbabilityForecastHistoryStore,
    ScenarioProbabilityAssessment,
)
from .records import AuditReport, MarketObservation, RunManifest, RunStatus, iso_now
from .research_learning import ResearchLearningStore
from .source_reporting import build_source_link_index, render_source_link_section
from .state import StateStore


ZERO = Decimal("0")
_SUMMARY_VISUAL = "distributional_summary.svg"
_ASSUMPTIONS_VISUAL = "distributional_assumptions.svg"


def _intrinsic_range(
    valuation: DistributionalPrimaryValuationResult,
) -> tuple[str, Decimal, Decimal]:
    if valuation.pathwise_distribution is not None:
        return (
            "PATHWISE_P20_P80",
            valuation.pathwise_distribution.quantile(Decimal("0.20")),
            valuation.pathwise_distribution.quantile(Decimal("0.80")),
        )
    if valuation.ambiguity_intrinsic_range is not None:
        return (
            "GOVERNED_PRIOR_EXPECTED_VALUE_RANGE",
            valuation.ambiguity_intrinsic_range.minimum_expected_value,
            valuation.ambiguity_intrinsic_range.maximum_expected_value,
        )
    raise ValueError("distributional valuation has no intrinsic range")


def _svg_card(title: str, lines: tuple[str, ...]) -> str:
    rows = []
    y = 160
    for line in lines[:7]:
        rows.append(
            f'<text x="72" y="{y}" font-family="sans-serif" font-size="30">{escape(line)}</text>'
        )
        y += 58
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        '<rect width="1200" height="630" fill="white"/>'
        f'<text x="72" y="88" font-family="sans-serif" font-size="44" font-weight="700">{escape(title)}</text>'
        + "".join(rows)
        + "</svg>"
    )


def _distributional_visuals(
    data: dict[str, Any],
    valuation: DistributionalPrimaryValuationResult,
) -> tuple[tuple[str, str], tuple[str, str]]:
    company = str(data.get("company") or data.get("target_id") or "Target")
    _, low, high = _intrinsic_range(valuation)
    entry = (
        f"진입가격 {_fmt(valuation.entry_price)} {valuation.reporting_unit} 이하"
        if valuation.route_authorization.entry_price_authorized
        and valuation.entry_price is not None
        else "진입가격 보류"
    )
    if valuation.pathwise_distribution is not None:
        dist = valuation.pathwise_distribution
        summary_lines = (
            f"내재가치 범위 {_fmt(low)}~{_fmt(high)} {valuation.reporting_unit}",
            f"P50 {_fmt(dist.quantile(Decimal('0.50')))} · 평균 {_fmt(dist.mean)}",
            f"곤경 {_pct(dist.distress_probability)} · 추가희석 {_pct(dist.dilution_probability)}",
            entry,
        )
    else:
        summary_lines = (
            f"내재가치 범위 {_fmt(low)}~{_fmt(high)} {valuation.reporting_unit}",
            "복수 사전확률 × 완결 지급모델의 기대가치 범위",
            "단일 목표가·보정 성공확률 미산출",
            entry,
        )
    beta = data.get("live_beta_result")
    wacc = data.get("live_wacc_result")
    assumptions_lines = (
        "영업 드라이버 → 자금조달 → 구주주 지급을 하나의 경로로 계산",
        f"자산 베타 {getattr(beta, 'target_asset_beta', '미산출')}",
        f"WACC {getattr(getattr(wacc, 'wacc_result', None), 'wacc', '미산출')}",
        "부채·리스·증자·자산매각 중복계상 차단",
        "현재가·증권사 자료는 내재가치 동결 뒤에만 비교",
    )
    return (
        (_SUMMARY_VISUAL, _svg_card(f"{company} 가치평가 요약", summary_lines)),
        (_ASSUMPTIONS_VISUAL, _svg_card(f"{company} 가정·위험", assumptions_lines)),
    )


def render_canonical_distributional_report(
    data: dict[str, Any],
    *,
    require_verifiable_sources: bool,
) -> str:
    company = str(data.get("company") or data.get("target_id") or "Target")
    valuation = data.get("distributional_primary_result")
    audit = data.get("audit_report")
    if not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError("distributional primary result is required for report")
    if not isinstance(audit, AuditReport) or not audit.passed:
        raise ValueError("audit-passed distributional result is required for report")

    currency = valuation.reporting_unit
    kind, low, high = _intrinsic_range(valuation)
    market = data.get("market_comparison")
    street = data.get("street_comparison")
    observation = data.get("market_observation")
    current_price = (
        f"{_fmt(market.price)} {market.currency} ({market.as_of})"
        if isinstance(market, DistributionalMarketComparison)
        else (
            f"{Decimal(str(observation.price)):,.0f} {data.get('market_currency', currency)} ({observation.as_of})"
            if isinstance(observation, MarketObservation)
            else "미확보"
        )
    )

    if valuation.pathwise_distribution is not None:
        distribution = valuation.pathwise_distribution
        reference = distribution.quantile(Decimal("0.50"))
        probability_summary = (
            f"경로상 곤경 {_pct(distribution.distress_probability)} · "
            f"추가 희석 {_pct(distribution.dilution_probability)}"
        )
        judgment = "보정된 연속 경로 분포를 기준으로 내재가치와 하방위험을 함께 봅니다."
    else:
        reference = (low + high) / Decimal("2")
        probability_summary = "보정 성공확률 없음 · 복수 사전확률 집합만 사용"
        judgment = "보정된 성공확률이 없어 단일 목표가 대신 확률·지급모델 모호성 범위를 사용합니다."

    entry_text = "보류"
    if valuation.route_authorization.entry_price_authorized and valuation.entry_price is not None:
        entry_text = f"{_fmt(valuation.entry_price)} {currency} 이하"

    street_text = "미확보"
    if isinstance(street, DistributionalStreetComparison):
        street_text = (
            f"평균 {_fmt(street.mean_target_price)} {street.target_price_currency} · "
            f"범위 {_fmt(street.min_target_price)}~{_fmt(street.max_target_price)}"
        )

    thesis = str(data.get("current_thesis") or "").strip() or (
        "공시 근거와 동일 실행의 자금조달·희석·곤경 경로를 함께 반영해 "
        "구주주 기준 내재가치를 계산했습니다."
    )

    lines = [
        f"# {company} 투자보고서",
        "",
        "## 투자 요약",
        "",
        "| 핵심 판단 항목 | 내용 |",
        "| --- | --- |",
        f"| **투자판단** | {judgment} |",
        f"| **현재가** | {current_price} |",
        f"| **기준 내재가치** | {_fmt(reference)} {currency} |",
        f"| **가치평가 범위** | {_fmt(low)}~{_fmt(high)} {currency} ({kind}) |",
        f"| **시나리오 가능성** | {probability_summary} |",
        f"| **진입가격** | {entry_text} |",
        f"| **증권사 참고값** | {street_text} |",
        "",
        "### 한 문장 결론",
        "",
        thesis,
        "",
        "### 투자포인트",
        "",
        "- 영업 드라이버와 자금조달을 한 경로에서 연결해 부채·리스·증자·자산매각을 중복 계산하지 않습니다.",
        "- 주주가 실제로 받는 시점별 현금흐름을 기준으로 진입가격을 계산합니다.",
        "- 현재가와 증권사 목표가는 내재가치 동결 뒤에만 읽습니다.",
        "",
        "### 판단 변경 조건",
        "",
        "- 구조적 단절 이후의 실적·수요·단가·원가 데이터가 추가되면 분포와 보정 상태를 다시 검증합니다.",
        "- 차입·리스 만기, 재조달 한도, 자산매각 조건, 희석 조건이 달라지면 지급경로와 진입가격을 다시 계산합니다.",
        "- 보정되지 않은 사전확률은 성공확률이나 단일 목표가로 승격하지 않습니다.",
        "",
        "## 가치평가",
        "",
        "- 주평가법: capacity_yield_levered / driver_distributional_apv",
        f"- 내재가치 범위: {_fmt(low)}~{_fmt(high)} {currency}",
    ]

    if valuation.pathwise_distribution is not None:
        distribution = valuation.pathwise_distribution
        lines.extend(
            (
                f"- P20: {_fmt(distribution.quantile(Decimal('0.20')))} {currency}",
                f"- P50: {_fmt(distribution.quantile(Decimal('0.50')))} {currency}",
                f"- 평균: {_fmt(distribution.mean)} {currency}",
                f"- P80: {_fmt(distribution.quantile(Decimal('0.80')))} {currency}",
                f"- 곤경경로 비중: {_pct(distribution.distress_probability)}",
                f"- 추가 희석경로 비중: {_pct(distribution.dilution_probability)}",
            )
        )
        if (
            valuation.route_authorization.success_probability_claim_authorized
            and valuation.pathwise_entry is not None
        ):
            lines.append(
                f"- 검증된 진입가격: {_fmt(valuation.pathwise_entry.entry_price)} {currency} 이하 · "
                f"경로상 성공비율 {_pct(valuation.pathwise_entry.realized_success_probability)}"
            )
    else:
        intrinsic = valuation.ambiguity_intrinsic_range
        assert intrinsic is not None
        lines.extend(
            (
                f"- 확률벡터×지급모델 조합 기대가치 최소: {_fmt(intrinsic.minimum_expected_value)} {currency}",
                f"- 확률벡터×지급모델 조합 기대가치 최대: {_fmt(intrinsic.maximum_expected_value)} {currency}",
                "- 단일 확률가중 목표가: 미산출",
                "- 보정 성공확률: 미산출",
            )
        )
        if valuation.route_authorization.entry_price_authorized and valuation.entry_price is not None:
            lines.append(
                f"- 보수적 진입 상한: {_fmt(valuation.entry_price)} {currency} 이하 "
                "(허용된 전체 확률벡터×지급모델 조합 중 최소 기대현재가치)"
            )

    probability_assessment = data.get("scenario_probability_assessment")
    if isinstance(probability_assessment, ScenarioProbabilityAssessment):
        lines.extend(
            (
                "",
                "### 시나리오 발생 가능성 — 미보정 분석가 사전확률",
                "",
                "이 표는 참고용이며 분포형 APV의 기대가치·진입가격 가중치로 사용하지 않습니다.",
                "",
                "| 시나리오 | 표시 확률 | 근거 |",
                "| --- | ---: | --- |",
            )
        )
        for row in probability_assessment.rows:
            lines.append(f"| {row.scenario_id} | {_pct(row.displayed_probability)} | {row.rationale} |")

    beta = data.get("live_beta_result")
    wacc = data.get("live_wacc_result")
    lines.extend(
        (
            "",
            "## 핵심 가정과 위험",
            "",
            f"- 자산·영업 위험 베타: {getattr(beta, 'target_asset_beta', '미산출')}",
            f"- 가중평균자본비용: {getattr(getattr(wacc, 'wacc_result', None), 'wacc', '해당 없음')}",
            "- 비영업자산 매각은 매각대금과 제거 자산가치를 별도 대조해 잔존가치 이중계상을 막습니다.",
            "- limited liability는 보고서 숫자에 사후 0원 하한을 씌우지 않고 명시적 주주 지급·곤경 회수경로에서만 적용합니다.",
            "",
            "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
            "",
            "인공지능은 공시 근거에서 가정 후보와 반증 질문을 제안합니다. "
            "가정 확정, 확률 계산, 가치평가 산식, 감사 통과와 동결은 결정론적 엔진이 수행합니다.",
            "",
            "## 증권사·시장 비교",
            "",
        )
    )
    if isinstance(market, DistributionalMarketComparison):
        lines.append(
            f"- 현재가 {_fmt(market.price)} {market.currency}: 동결 내재가치 범위 대비 "
            f"{_signed(market.gap_to_low)}~{_signed(market.gap_to_high)} {market.currency}"
        )
    else:
        lines.append("- 현재가 비교: 미확보 또는 비적용")
    if isinstance(street, DistributionalStreetComparison):
        lines.append(
            f"- 증권사 목표가 평균 {_fmt(street.mean_target_price)} {street.target_price_currency}, "
            f"중앙값 {_fmt(street.median_target_price)}, "
            f"범위 {_fmt(street.min_target_price)}~{_fmt(street.max_target_price)} ({street.report_count}건)"
        )
    else:
        lines.append("- 증권사 비교: 미확보 또는 비적용")

    lines.extend(
        (
            "",
            "## 최종 요약 이미지",
            f"![{company} 가치평가 요약]({_SUMMARY_VISUAL})",
            "",
            f"![{company} 가치평가 가정·위험]({_ASSUMPTIONS_VISUAL})",
        )
    )

    source_links = build_source_link_index(data, require_all_http=require_verifiable_sources)
    lines.extend(("", *render_source_link_section(source_links)))

    blocking = tuple(item for item in audit.findings if item.blocking)
    passed = sum(item.passed for item in blocking)
    lines.extend(
        (
            "",
            "## 감사 요약",
            "",
            f"- 차단 감사 {passed}/{len(blocking)} 통과",
            "- 현재가·증권사 입력은 같은 실행의 intrinsic freeze 이후에만 접근했습니다.",
            "- 내부 해시·receipt는 투자자 본문이 아니라 불변 감사 산출물에만 보존합니다.",
        )
    )
    return "\n".join(lines) + "\n"


def canonical_distributional_save_state_adapter(*, state_root: str | Path) -> StageAdapter:
    root = Path(state_root)
    store = StateStore(root)
    learning_store = ResearchLearningStore(root)
    probability_store = ProbabilityForecastHistoryStore(root)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        reserved = {
            "saved_run_dir",
            "saved_current_state",
            "saved_report_markdown",
            "saved_report_visuals",
            "module_impact_summary",
            "final_report",
            "research_learning_record_path",
            "research_learning_record_hash",
            "research_learning_recorded_at",
            "probability_forecast_record_path",
            "probability_forecast_record_hash",
            "probability_forecast_recorded_at",
            "probability_forecast_ids",
        }
        collisions = tuple(sorted(reserved.intersection(context.data)))
        if collisions:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional SAVE_STATE reserved output keys already exist: "
                + ", ".join(collisions),
                blocking=True,
            )

        try:
            ticker = context.data.get("ticker")
            company = context.data.get("company")
            valuation = context.data.get("distributional_primary_result")
            audit = context.data.get("audit_report")
            token = context.data.get("intrinsic_freeze_token")
            batch = context.data.get("decision_impact_batch")
            drafts = context.data.get("probability_forecast_drafts", ())
            if not isinstance(ticker, str) or not ticker:
                raise ValueError("ticker is required")
            if not isinstance(company, str) or not company:
                raise ValueError("company is required")
            if not isinstance(valuation, DistributionalPrimaryValuationResult):
                raise ValueError("distributional primary result is required")
            if not isinstance(audit, AuditReport) or not audit.passed:
                raise ValueError("audit PASS is required")
            if token is None or getattr(token, "run_id", None) != context.run_id:
                raise ValueError("same-run IntrinsicFreezeToken is required")
            authorize_post_freeze(token, run_id=context.run_id)
            lineage = valuation.envelope.distribution_lineage
            if lineage is None:
                raise ValueError("distributional valuation lineage is missing")
            if (
                getattr(token, "valuation_hash", None) != valuation.envelope.envelope_hash
                or getattr(token, "distribution_hash", None) != lineage.distribution_hash
                or getattr(token, "route_authorization_hash", None)
                != lineage.route_authorization_hash
                or getattr(token, "entry_policy_version", None) != lineage.entry_policy_version
                or getattr(token, "entry_calculation_hash", None)
                != lineage.entry_calculation_hash
                or getattr(token, "ambiguity_set_hash", None) != lineage.ambiguity_set_hash
                or getattr(token, "payoff_model_set_hash", None) != lineage.payoff_model_set_hash
            ):
                raise ValueError("freeze token is not fully bound to distributional lineage")
            if not isinstance(batch, AblationBatchResult):
                raise ValueError("Decision Impact batch is required")
            if not isinstance(drafts, tuple) or not all(
                isinstance(item, ProbabilityForecastDraft) for item in drafts
            ):
                raise ValueError("probability forecast drafts must be typed")

            report = render_canonical_distributional_report(
                dict(context.data),
                require_verifiable_sources=(context.execution_mode is ExecutionMode.LIVE_PRIMARY),
            )
            visuals = _distributional_visuals(dict(context.data), valuation)
            prior = context.data.get("company_state", {})
            parent_run = prior.get("last_completed_run") if isinstance(prior, dict) else None
            now = iso_now()
            manifest = RunManifest(
                run_id=context.run_id,
                ticker=ticker,
                company=company,
                started_at=str(context.data.get("run_started_at", now)),
                finished_at=now,
                status=RunStatus.COMPLETED,
                round_count=int(context.data.get("research_round_count", 1)),
                audit_passed=True,
                parent_run_id=parent_run,
                blocked_reasons=(),
            )
            impact_summary = _impact_summary(batch)
            learning_ref = learning_store.save_batch(ticker=ticker, run_id=context.run_id, batch=batch)
            probability_ref = (
                probability_store.save_forecast_run(
                    ticker=ticker,
                    run_id=context.run_id,
                    drafts=drafts,
                )
                if drafts
                else None
            )
            artifacts = {
                "control_plane_trace.json": _jsonable(tuple(context.stage_traces)),
                "compiled_assumptions.json": _jsonable(context.data.get("compiled_assumption_set")),
                "scenario_set.json": _jsonable(context.data.get("bound_scenario_set")),
                "distributional_valuation.json": _jsonable(valuation),
                "audit.json": _jsonable(audit),
                "doctrine_coverage.json": _jsonable(context.data.get("doctrine_coverage", ())),
                "module_impact.json": {"summary": impact_summary, "batch": _jsonable(batch)},
                "street_compare.json": _jsonable(context.data.get("street_comparison")),
                "market_compare.json": _jsonable(context.data.get("market_comparison")),
                "freeze_token.json": _jsonable(token),
                "final_report.md": report,
                **{filename: svg for filename, svg in visuals},
            }
            run_dir = store.save_run(manifest, artifacts)
            kind, low, high = _intrinsic_range(valuation)
            current_state = {
                "schema_version": "0.6.14-distributional",
                "ticker": ticker,
                "company": company,
                "last_completed_run": context.run_id,
                "last_successful_valuation_run": context.run_id,
                "thesis": str(context.data.get("current_thesis", "")),
                "ledger_snapshot_hash": context.data.get("ledger_snapshot_hash"),
                "assumption_set_hash": context.data.get("assumption_set_hash"),
                "valuation_hash": valuation.envelope.envelope_hash,
                "audit_hash": context.data.get("audit_hash"),
                "valuation_scope": "FULL_INTRINSIC",
                "distribution_route": valuation.route.value,
                "distribution_hash": valuation.distribution_hash,
                "route_authorization_hash": valuation.route_authorization.authorization_hash,
                "intrinsic_reference_kind": kind,
                "intrinsic_reference_low": str(low),
                "intrinsic_reference_high": str(high),
                "entry_price": str(valuation.entry_price) if valuation.entry_price is not None else None,
                "entry_price_authorized": valuation.route_authorization.entry_price_authorized,
                "success_probability_claim_authorized": valuation.route_authorization.success_probability_claim_authorized,
                "entry_policy_version": lineage.entry_policy_version,
                "ambiguity_set_hash": lineage.ambiguity_set_hash,
                "payoff_model_set_hash": lineage.payoff_model_set_hash,
                "entry_calculation_hash": lineage.entry_calculation_hash,
                "decision_impact_hash": context.data.get("decision_impact_hash"),
                "research_learning_record_hash": learning_ref.content_hash,
                "probability_forecast_record_hash": (
                    probability_ref.content_hash if probability_ref is not None else None
                ),
                "freeze_token_hash": token.token_hash,
                "report_visuals": [filename for filename, _ in visuals],
            }
            store.promote_current(manifest, current_state)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional state persistence failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs: dict[str, object] = {
            "saved_run_dir": str(run_dir),
            "saved_current_state": current_state,
            "saved_report_markdown": report,
            "saved_report_visuals": tuple(filename for filename, _ in visuals),
            "module_impact_summary": impact_summary,
            "research_learning_record_path": learning_ref.path,
            "research_learning_record_hash": learning_ref.content_hash,
            "research_learning_recorded_at": learning_ref.recorded_at,
        }
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
            "immutable distributional valuation, audit, two report visuals, learning history and investor report persisted",
            outputs,
        )

    return run


def _impact_summary(batch: AblationBatchResult) -> dict[str, object]:
    return {
        "available": True,
        "measured": sorted(
            item.module_id for item in batch.module_observations if item.status is AblationStatus.MEASURED
        ),
        "not_measurable": sorted(
            item.module_id for item in batch.module_observations if item.status is AblationStatus.NOT_MEASURABLE
        ),
        "not_applicable": sorted(
            item.module_id for item in batch.module_observations if item.status is AblationStatus.NOT_APPLICABLE
        ),
        "failed": sorted(
            item.module_id for item in batch.module_observations if item.status is AblationStatus.FAILED
        ),
    }


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return "미산출"
    return f"{value:,.0f}" if value == value.to_integral() else f"{value:,.2f}".rstrip("0").rstrip(".")


def _signed(value: Decimal) -> str:
    return f"{value:+,.0f}" if value == value.to_integral() else f"{value:+,.2f}".rstrip("0").rstrip(".")


def _pct(value: Decimal | None) -> str:
    return "미산출" if value is None else f"{value * Decimal('100'):.1f}%"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "canonical_distributional_save_state_adapter",
    "render_canonical_distributional_report",
]
