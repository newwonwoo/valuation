from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any

from .ablation import AblationBatchResult, AblationStatus
from .control_plane import ExecutionMode, StageStatus, authorize_post_freeze
from .distribution_route_policy import DistributionIntegrationRoute
from .distributional_runtime import DistributionalPrimaryValuationResult
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditReport, MarketObservation, RunManifest, RunStatus, iso_now
from .state import StateStore
from .street import StreetResearchReport, summarize_street_reports


@dataclass(frozen=True)
class DistributionalStreetComparison:
    report_count: int
    latest_report_date: str
    target_price_currency: str
    mean_target_price: Decimal
    median_target_price: Decimal
    min_target_price: Decimal
    max_target_price: Decimal
    intrinsic_reference_kind: str
    intrinsic_reference_low: Decimal
    intrinsic_reference_high: Decimal
    mean_target_gap_to_low: Decimal
    mean_target_gap_to_high: Decimal


@dataclass(frozen=True)
class DistributionalMarketComparison:
    price: Decimal
    as_of: str
    source_ref: str
    currency: str
    intrinsic_reference_kind: str
    intrinsic_reference_low: Decimal
    intrinsic_reference_high: Decimal
    gap_to_low: Decimal
    gap_to_high: Decimal
    entry_price: Decimal | None
    entry_price_authorized: bool


def _require_post_freeze(context: OrchestratorContext) -> None:
    if context.freeze_token is None:
        raise PermissionError("IntrinsicFreezeToken is required")
    authorize_post_freeze(context.freeze_token, run_id=context.run_id)


def _intrinsic_range(
    valuation: DistributionalPrimaryValuationResult,
) -> tuple[str, Decimal, Decimal]:
    if valuation.pathwise_distribution is not None:
        values = valuation.pathwise_distribution.path_values_per_share
        if not values:
            raise ValueError("pathwise distribution has no path values")
        low = valuation.pathwise_distribution.quantile(Decimal("0.20"))
        high = valuation.pathwise_distribution.quantile(Decimal("0.80"))
        return "PATHWISE_P20_P80", low, high
    if valuation.ambiguity_intrinsic_range is not None:
        return (
            "GOVERNED_PRIOR_EXPECTED_VALUE_RANGE",
            valuation.ambiguity_intrinsic_range.minimum_expected_value,
            valuation.ambiguity_intrinsic_range.maximum_expected_value,
        )
    raise ValueError("distributional valuation has no intrinsic range")


def distributional_street_gap_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            valuation = context.data.get("distributional_primary_result")
            reports = context.data.get("street_reports")
            if not isinstance(valuation, DistributionalPrimaryValuationResult):
                raise ValueError("distributional primary result is missing")
            if not isinstance(reports, tuple):
                raise ValueError("Street reports are missing")
            if not reports:
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    "no sell-side coverage was declared for this target",
                )
            if not all(isinstance(item, StreetResearchReport) for item in reports):
                raise TypeError("Street reports contain an invalid object")
            consensus = summarize_street_reports(reports)
            if consensus.target_price_currency != valuation.reporting_unit:
                raise ValueError("Street target currency differs from intrinsic reporting unit")
            kind, low, high = _intrinsic_range(valuation)
            mean_target = Decimal(str(consensus.mean_target_price))
            comparison = DistributionalStreetComparison(
                report_count=consensus.report_count,
                latest_report_date=consensus.latest_report_date,
                target_price_currency=consensus.target_price_currency,
                mean_target_price=mean_target,
                median_target_price=Decimal(str(consensus.median_target_price)),
                min_target_price=Decimal(str(consensus.min_target_price)),
                max_target_price=Decimal(str(consensus.max_target_price)),
                intrinsic_reference_kind=kind,
                intrinsic_reference_low=low,
                intrinsic_reference_high=high,
                mean_target_gap_to_low=mean_target - low,
                mean_target_gap_to_high=mean_target - high,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional Street comparison failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Street targets compared post-freeze with the frozen distributional intrinsic range",
            {"street_comparison": comparison},
        )

    return run


def distributional_market_compare_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            valuation = context.data.get("distributional_primary_result")
            observation = context.data.get("market_observation")
            currency = context.data.get("market_currency")
            if not isinstance(valuation, DistributionalPrimaryValuationResult):
                raise ValueError("distributional primary result is missing")
            if not isinstance(observation, MarketObservation):
                raise ValueError("MarketObservation is missing")
            if not isinstance(currency, str) or not currency:
                raise ValueError("market currency is missing")
            if currency != valuation.reporting_unit:
                raise ValueError("market currency differs from intrinsic reporting unit")
            kind, low, high = _intrinsic_range(valuation)
            price = Decimal(str(observation.price))
            comparison = DistributionalMarketComparison(
                price=price,
                as_of=observation.as_of,
                source_ref=observation.source_ref,
                currency=currency,
                intrinsic_reference_kind=kind,
                intrinsic_reference_low=low,
                intrinsic_reference_high=high,
                gap_to_low=low - price,
                gap_to_high=high - price,
                entry_price=valuation.entry_price,
                entry_price_authorized=valuation.route_authorization.entry_price_authorized,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional market comparison failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "current price compared post-freeze with the frozen distributional intrinsic range; reverse DCF is not applied to this route",
            {
                "market_comparison": comparison,
                "reverse_dcf_withheld_reason": "DISTRIBUTIONAL_APV_PRIMARY_ROUTE",
            },
        )

    return run


def render_distributional_report(data: dict[str, Any]) -> str:
    company = str(data.get("company") or data.get("target_id") or "Target")
    valuation = data.get("distributional_primary_result")
    audit = data.get("audit_report")
    if not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError("distributional primary result is required for report")
    if not isinstance(audit, AuditReport) or not audit.passed:
        raise ValueError("audit-passed distributional result is required for report")

    currency = valuation.reporting_unit
    kind, low, high = _intrinsic_range(valuation)
    lines = [
        f"# {company} 투자보고서",
        "",
        "## 가치평가 결론",
        f"- 평가경로: `capacity_yield_levered/driver_distributional_apv`",
        f"- 분포경로: {valuation.route.value}",
        f"- 내재가치 범위: 주당 {_fmt(low)}~{_fmt(high)} {currency} ({kind})",
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
        if valuation.route_authorization.success_probability_claim_authorized and valuation.pathwise_entry is not None:
            lines.append(
                f"- 진입가격: {_fmt(valuation.pathwise_entry.entry_price)} {currency} 이하 · "
                f"경로상 성공비율 {_pct(valuation.pathwise_entry.realized_success_probability)}"
            )
        else:
            lines.append("- 성공확률 표시는 보정·지급경로 권한 조건이 충족되지 않아 사용하지 않습니다.")
    else:
        intrinsic = valuation.ambiguity_intrinsic_range
        assert intrinsic is not None
        lines.extend(
            (
                "- 현재 자료는 보정된 성공확률이 아니라 복수의 근거 결속 사전확률 집합을 사용합니다.",
                f"- 확률·모델 조합 기대가치 범위: {_fmt(intrinsic.minimum_expected_value)}~"
                f"{_fmt(intrinsic.maximum_expected_value)} {currency}",
                "- 단일 확률가중 목표가와 성공확률은 제시하지 않습니다.",
            )
        )
        if valuation.route_authorization.entry_price_authorized and valuation.entry_price is not None:
            lines.append(
                f"- 보수적 진입 상한: {_fmt(valuation.entry_price)} {currency} 이하 "
                "(모든 허용 확률벡터×지급모델 조합 중 최소 기대현재가치)"
            )
        else:
            lines.append("- 구체 진입가격은 지급경로 감사 조건이 충족되지 않아 보류합니다.")

    market = data.get("market_comparison")
    street = data.get("street_comparison")
    lines.extend(("", "## 시장·증권사 비교"))
    if isinstance(market, DistributionalMarketComparison):
        lines.append(
            f"- 현재가: {_fmt(market.price)} {market.currency} ({market.as_of}) · "
            f"동결 내재가치 범위 대비 차이 {_signed(market.gap_to_low)}~{_signed(market.gap_to_high)} {market.currency}"
        )
    else:
        lines.append("- 현재가 비교: 확보되지 않았거나 적용 대상이 아닙니다.")
    if isinstance(street, DistributionalStreetComparison):
        lines.append(
            f"- 증권사 목표가: 평균 {_fmt(street.mean_target_price)} {street.target_price_currency} · "
            f"중앙값 {_fmt(street.median_target_price)} · 범위 {_fmt(street.min_target_price)}~{_fmt(street.max_target_price)} "
            f"({street.report_count}건, 가치평가 동결 후 참고)"
        )
    else:
        lines.append("- 증권사 비교: 확보되지 않았거나 적용 대상이 아닙니다.")

    blocking = tuple(item for item in audit.findings if item.blocking)
    passed = sum(item.passed for item in blocking)
    lines.extend(
        (
            "",
            "## 감사·불확실성",
            f"- 차단 감사: {passed}/{len(blocking)} 통과",
            f"- 분포 해시: `{valuation.distribution_hash}`",
            f"- 라우트 승인 해시: `{valuation.route_authorization.authorization_hash}`",
            f"- 동결 가치 해시: `{valuation.envelope.envelope_hash}`",
        )
    )
    if valuation.route_authorization.blocking_reasons:
        lines.append(
            "- 제한사항: " + ", ".join(valuation.route_authorization.blocking_reasons)
        )
    lines.extend(
        (
            "- 현재가와 증권사 목표가는 내재가치 동결 이후에만 읽으며, 앞선 가정·확률·분포를 바꾸지 않습니다.",
            "- 비영업자산 매각, 리스·차입금, 재조달, 증자와 곤경 회수는 동일 경로에서 중복 없이 반영합니다.",
        )
    )
    return "\n".join(lines) + "\n"


def distributional_save_state_adapter(*, state_root: str | Path) -> StageAdapter:
    root = Path(state_root)
    store = StateStore(root)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        reserved = {
            "saved_run_dir",
            "saved_current_state",
            "saved_report_markdown",
            "saved_report_visuals",
            "module_impact_summary",
            "final_report",
        }
        collisions = tuple(sorted(reserved.intersection(context.data)))
        if collisions:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "SAVE_STATE reserved output keys already exist: " + ", ".join(collisions),
                blocking=True,
            )

        try:
            ticker = context.data.get("ticker")
            company = context.data.get("company")
            valuation = context.data.get("distributional_primary_result")
            audit = context.data.get("audit_report")
            token = context.data.get("intrinsic_freeze_token")
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
            if getattr(token, "valuation_hash", None) != valuation.envelope.envelope_hash:
                raise ValueError("freeze token is not bound to the distributional envelope")

            report = render_distributional_report(dict(context.data))
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
            impact_summary = _impact_summary(context.data.get("decision_impact_batch"))
            artifacts = {
                "control_plane_trace.json": _jsonable(tuple(context.stage_traces)),
                "compiled_assumptions.json": _jsonable(context.data.get("compiled_assumption_set")),
                "scenario_set.json": _jsonable(context.data.get("bound_scenario_set")),
                "distributional_valuation.json": _jsonable(valuation),
                "audit.json": _jsonable(audit),
                "doctrine_coverage.json": _jsonable(context.data.get("doctrine_coverage", ())),
                "module_impact.json": {
                    "summary": impact_summary,
                    "batch": _jsonable(context.data.get("decision_impact_batch")),
                },
                "street_compare.json": _jsonable(context.data.get("street_comparison")),
                "market_compare.json": _jsonable(context.data.get("market_comparison")),
                "freeze_token.json": _jsonable(token),
                "final_report.md": report,
            }
            run_dir = store.save_run(manifest, artifacts)
            kind, low, high = _intrinsic_range(valuation)
            lineage = valuation.envelope.distribution_lineage
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
                "entry_policy_version": lineage.entry_policy_version if lineage is not None else None,
                "ambiguity_set_hash": lineage.ambiguity_set_hash if lineage is not None else None,
                "payoff_model_set_hash": lineage.payoff_model_set_hash if lineage is not None else None,
                "entry_calculation_hash": lineage.entry_calculation_hash if lineage is not None else None,
                "decision_impact_hash": context.data.get("decision_impact_hash"),
                "freeze_token_hash": token.token_hash,
            }
            store.promote_current(manifest, current_state)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional state persistence failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        return StageExecutionResult(
            StageStatus.PASS,
            "immutable distributional valuation, audit, post-freeze comparisons and investor report persisted",
            {
                "saved_run_dir": str(run_dir),
                "saved_current_state": current_state,
                "saved_report_markdown": report,
                "saved_report_visuals": (),
                "module_impact_summary": impact_summary,
            },
        )

    return run


def distributional_or_generic_adapter(
    *,
    generic_adapter: StageAdapter,
    distributional_adapter: StageAdapter,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.data.get("distributional_primary_result") is not None:
            return distributional_adapter(context)
        return generic_adapter(context)

    return run


def _impact_summary(value: object) -> dict[str, object]:
    if not isinstance(value, AblationBatchResult):
        return {"available": False, "measured": [], "not_measurable": [], "failed": []}
    return {
        "available": True,
        "measured": sorted(
            item.module_id for item in value.module_observations if item.status is AblationStatus.MEASURED
        ),
        "not_measurable": sorted(
            item.module_id for item in value.module_observations if item.status is AblationStatus.NOT_MEASURABLE
        ),
        "failed": sorted(
            item.module_id for item in value.module_observations if item.status is AblationStatus.FAILED
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
    "DistributionalMarketComparison",
    "DistributionalStreetComparison",
    "distributional_market_compare_adapter",
    "distributional_or_generic_adapter",
    "distributional_save_state_adapter",
    "distributional_street_gap_adapter",
    "render_distributional_report",
]
