from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from .control_plane import DoctrineCoverageEntry, StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .post_freeze import MarketComparisonBundle, StreetComparisonBundle
from .records import AuditReport, RunManifest, RunStatus, iso_now
from .state import StateStore, thesis_delta
from .valuation_execution import GenericValuationResult


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


def thesis_delta_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        state = context.data.get("company_state", {})
        if not isinstance(state, dict):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company_state must be a mapping before Thesis Delta",
                blocking=True,
            )
        previous = str(state.get("thesis", ""))
        current = _current_thesis(context.data)
        if not current:
            current = "No material thesis statement was produced in this run."
        return StageExecutionResult(
            StageStatus.PASS,
            "current thesis compared with the prior immutable successful state",
            {
                "current_thesis": current,
                "thesis_delta_result": thesis_delta(previous, current),
            },
        )

    return run


def render_generic_report(data: dict[str, Any]) -> str:
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

    lines = [
        f"# {company} PRISM Research & Valuation Report",
        "",
        "## Intrinsic Value",
    ]
    for item in valuation.scenarios:
        lines.append(
            f"- {item.scenario_id}: {_fmt(item.value_per_share)} {valuation.reporting_unit}/share"
        )
    if valuation.expected_value_per_share is None:
        lines.append("- Expected Value: 미산출 — 시나리오 확률이 CALIBRATED 상태가 아니므로 숫자 가중을 보류했습니다.")
    else:
        lines.append(
            f"- Expected Value: {_fmt(valuation.expected_value_per_share)} {valuation.reporting_unit}/share"
        )

    street = data.get("street_comparison")
    if isinstance(street, StreetComparisonBundle):
        lines.extend((
            "",
            "## Street Gap",
            f"- 리포트 수: {street.consensus.report_count}",
            f"- 평균 목표가: {_fmt(street.consensus.mean_target_price)} {street.consensus.target_price_currency}",
        ))
        for item in street.envelope.scenario_gaps:
            lines.append(
                f"- {item.scenario_id} 대비: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})"
            )
        if street.envelope.expected_gap is not None:
            item = street.envelope.expected_gap
            lines.append(f"- Expected 대비: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})")

    market = data.get("market_comparison")
    if isinstance(market, MarketComparisonBundle):
        lines.extend((
            "",
            "## Current Market Compare",
            f"- 현재가: {_fmt(market.observation.price)} {market.envelope.currency} ({market.observation.as_of})",
        ))
        for item in market.envelope.scenario_gaps:
            lines.append(
                f"- {item.scenario_id} 기대수익 간격: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})"
            )
        if market.envelope.expected_gap is not None:
            item = market.envelope.expected_gap
            lines.append(f"- Expected 기대수익 간격: {_fmt(item.gap_per_share)} ({item.gap_pct_of_reference:+.1%})")

    non_pass = tuple(
        item for item in coverage
        if item.status not in {StageStatus.PASS, StageStatus.WARNING, StageStatus.SKIPPED_NOT_APPLICABLE}
    )
    lines.extend((
        "",
        "## Audit & Coverage",
        f"- Audit: PASS ({len(audit.findings)} checks)",
        f"- Doctrine coverage: {len(coverage) - len(non_pass)}/{len(coverage)} terminally acceptable",
    ))
    for item in non_pass:
        lines.append(f"- {item.module_id}: {item.status.value} — {item.rationale}")

    delta = data.get("thesis_delta_result", {})
    if isinstance(delta, dict):
        lines.extend((
            "",
            "## Thesis Delta",
            f"- 강화·신규: {', '.join(delta.get('strengthened_or_new', [])) or '없음'}",
            f"- 약화·폐기: {', '.join(delta.get('weakened_or_removed', [])) or '없음'}",
        ))

    lines.extend((
        "",
        "## Run Integrity",
        f"- Assumption set: {data.get('assumption_set_hash', '')}",
        f"- Valuation: {data.get('valuation_hash', '')}",
        f"- Audit: {data.get('audit_hash', '')}",
        f"- Freeze token: {getattr(data.get('intrinsic_freeze_token'), 'token_hash', '')}",
    ))
    return "\n".join(lines) + "\n"


def save_state_adapter(*, state_root: str | Path) -> StageAdapter:
    store = StateStore(state_root)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            ticker = context.data.get("ticker")
            company = context.data.get("company")
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

            report = render_generic_report(context.data)
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
                "valuation.json": _jsonable(valuation),
                "audit.json": _jsonable(audit),
                "doctrine_coverage.json": _jsonable(context.data.get("doctrine_coverage", ())),
                "street_compare.json": _jsonable(context.data.get("street_comparison")),
                "market_compare.json": _jsonable(context.data.get("market_comparison")),
                "thesis_delta.json": _jsonable(context.data.get("thesis_delta_result", {})),
                "freeze_token.json": _jsonable(token),
                "final_report.md": report,
            }
            run_dir = store.save_run(manifest, artifacts)
            current_state = {
                "schema_version": "0.6",
                "ticker": ticker,
                "company": company,
                "last_completed_run": context.run_id,
                "last_successful_valuation_run": context.run_id,
                "thesis": _current_thesis(context.data),
                "assumption_set_hash": context.data.get("assumption_set_hash"),
                "valuation_hash": context.data.get("valuation_hash"),
                "audit_hash": context.data.get("audit_hash"),
                "scenario_values_per_share": {
                    item.scenario_id: str(item.value_per_share) for item in valuation.scenarios
                },
                "expected_value_per_share": (
                    str(valuation.expected_value_per_share)
                    if valuation.expected_value_per_share is not None
                    else None
                ),
                "freeze_token_hash": token.token_hash,
            }
            store.promote_current(manifest, current_state)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"state persistence failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "immutable run artifacts saved and audit-passed current state promoted",
            {
                "saved_run_dir": str(run_dir),
                "saved_current_state": current_state,
                "saved_report_markdown": report,
            },
        )

    return run


def final_report_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        report = context.data.get("saved_report_markdown")
        if not isinstance(report, str) or not report:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "saved report artifact is missing; SAVE_STATE must complete first",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "final report emitted from the same immutable payload saved in the run state",
            {"final_report": report},
        )

    return run
