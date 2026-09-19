from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from html import escape
from pathlib import Path
from typing import Any

from .ablation import AblationBatchResult, AblationStatus
from .control_plane import ExecutionMode, StageStatus, authorize_post_freeze
from .distributional_runtime import DistributionalPrimaryValuationResult
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .investor_report import (
    InvestorReportProfile,
    render_distributional_investor_report,
)
from .probability_forecasting import (
    ProbabilityForecastDraft,
    ProbabilityForecastHistoryStore,
)
from .records import AuditReport, RunManifest, RunStatus, iso_now
from .research_learning import ResearchLearningStore
from .state import StateStore


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
    profile = data.get("investor_report_profile")
    if not isinstance(profile, InvestorReportProfile):
        raise ValueError("typed investor report profile is required")
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
    assumptions_lines = (
        profile.conclusion,
        *(f"투자논리: {item[0]}" for item in profile.investment_points[:3]),
        f"하방 점검: {profile.downside_condition}",
    )
    return (
        (_SUMMARY_VISUAL, _svg_card(f"{company} 가치평가 요약", summary_lines)),
        (_ASSUMPTIONS_VISUAL, _svg_card(f"{company} 투자논리·점검사항", assumptions_lines)),
    )


def render_canonical_distributional_report(
    data: dict[str, Any],
    *,
    require_verifiable_sources: bool,
) -> str:
    valuation = data.get("distributional_primary_result")
    audit = data.get("audit_report")
    if not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError("distributional primary result is required for report")
    if not isinstance(audit, AuditReport) or not audit.passed:
        raise ValueError("audit-passed distributional result is required for report")

    profile = data.get("investor_report_profile")
    if not isinstance(profile, InvestorReportProfile):
        raise ValueError("typed investor report profile is required for public report")
    return render_distributional_investor_report(data, profile)


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
            profile = context.data.get("investor_report_profile")
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
            if not isinstance(profile, InvestorReportProfile):
                raise ValueError("typed investor report profile is required")
            profile.validate()
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
                "investor_report_profile.json": _jsonable(profile),
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
