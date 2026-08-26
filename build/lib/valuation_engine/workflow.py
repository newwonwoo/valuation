from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .audit import audit_model, gate_report
from .config import load_intrinsic_company_config, load_market_comparison
from .engine import compare_to_market, run_valuation
from .ledger import stale_evidence_findings
from .provenance import build_oci_legacy_trace
from .records import AuditFinding, MarketObservation, RunManifest, RunStatus, iso_now
from .research import (
    ResearchContext,
    Researcher,
    RedTeam,
    default_fixture_red_team,
    default_fixture_researcher,
    run_research_loop,
)
from .scenario import ScenarioSet
from .state import StateStore, thesis_delta


MarketLoader = Callable[[], MarketObservation]


@dataclass(frozen=True)
class WorkflowResult:
    status: RunStatus
    run_id: str
    progress: tuple[str, ...]
    report: str
    audit: dict
    intrinsic_value_per_share: float | None = None
    market_price: float | None = None
    market_gap: float | None = None
    blocked_reasons: tuple[str, ...] = ()
    run_dir: str = ""


def run_analysis_command(
    command: str,
    *,
    config_path: str | Path,
    state_root: str | Path,
    researcher: Researcher = default_fixture_researcher,
    red_team: RedTeam = default_fixture_red_team,
    market_loader: MarketLoader | None = None,
    analysis_date: date | None = None,
    run_id: str | None = None,
) -> WorkflowResult:
    company = _resolve_company(command)
    shares, scenarios, intrinsic_raw = load_intrinsic_company_config(config_path)
    ticker = str(intrinsic_raw["company"]["ticker"])
    if company not in {intrinsic_raw["company"]["name"], "OCI홀딩스"}:
        raise ValueError("the v0.3 vertical slice currently supports the OCI fixture only")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = iso_now()
    analysis_date = analysis_date or date.today()
    store = StateStore(state_root)
    prior = store.load_current(ticker) or {}
    prior_thesis = str(prior.get("thesis", ""))
    progress = ["[1/10] Company State 로드"]

    trace = build_oci_legacy_trace(intrinsic_raw, run_id=run_id)
    progress.append("[2/10] Primary Evidence 수집 (offline regression fixture)")
    progress.append("[3/10] Rocket Insight")
    initial_hypotheses = trace.hypotheses
    research_output, redteam_output, rounds = run_research_loop(
        ResearchContext(company, ticker, prior_thesis, trace.ledger.active(), initial_hypotheses, 1),
        researcher,
        red_team,
        max_rounds=3,
    )
    progress.extend(("[4/10] Researcher A", "[5/10] Blind Red Team B"))
    unresolved = tuple(
        item.description for item in redteam_output.issues if item.blocking and not item.resolved
    )
    if unresolved:
        return _save_blocked(
            store, run_id, ticker, company, started, rounds, progress, unresolved,
            trace, research_output.thesis, redteam_output.strongest_counter_thesis,
            audit={"pass": False, "findings": [{"check": "research_loop", "passed": False, "blocking": True, "detail": reason} for reason in unresolved]},
        )

    trace.validate()
    progress.append("[6/10] Evidence → Assumption Bridge")
    ScenarioSet(tuple(scenarios)).validate()
    intrinsic = run_valuation(scenarios, shares)
    progress.append("[7/10] Scenario / Deterministic Valuation")
    core_audit = audit_model(scenarios, shares)
    stale_blocking, stale_warnings = stale_evidence_findings(trace.ledger, analysis_date)
    extra = [AuditFinding("stale_evidence", False, True, item) for item in stale_blocking]
    extra.extend(AuditFinding("stale_evidence", False, False, item) for item in stale_warnings)
    report = gate_report(core_audit, traceability_ok=True, extra_findings=extra)
    progress.append("[8/10] Audit Gate")
    if not report.passed:
        reasons = tuple(item.detail for item in report.findings if item.blocking and not item.passed)
        return _save_blocked(
            store, run_id, ticker, company, started, rounds, progress, reasons,
            trace, research_output.thesis, redteam_output.strongest_counter_thesis,
            audit=report.to_dict(),
        )

    progress.append("[9/10] Intrinsic Value")
    if market_loader is None:
        market_loader = market_loader_from_config(config_path)
    market = market_loader()  # Deliberately impossible to call before the passed gate above.
    market_gap = compare_to_market(intrinsic, market.price)
    progress.append("[10/10] Market Compare / Thesis Delta / Report")
    delta = thesis_delta(prior_thesis, research_output.thesis)
    final_report = _render_report(
        company, intrinsic.expected_value_per_share, market, market_gap,
        research_output.thesis, redteam_output.strongest_counter_thesis,
        delta, report.to_dict(), blocked=(),
    )
    finished = iso_now()
    manifest = RunManifest(
        run_id, ticker, company, started, finished, RunStatus.COMPLETED,
        rounds, True, prior.get("last_completed_run"), (),
    )
    artifacts = _artifacts(
        trace, research_output.thesis, redteam_output.strongest_counter_thesis,
        report.to_dict(), final_report,
        valuation={
            "scenarios": [asdict(item) for item in intrinsic.scenarios],
            "expected_equity_trn": intrinsic.expected_equity_trn,
            "expected_value_per_share": intrinsic.expected_value_per_share,
        },
        market={"price": market.price, "as_of": market.as_of, "source_ref": market.source_ref, "gap": market_gap},
        delta=delta,
    )
    run_dir = store.save_run(manifest, artifacts)
    current_state = {
        "schema_version": "0.3",
        "ticker": ticker,
        "company": company,
        "last_completed_run": run_id,
        "last_successful_valuation_run": run_id,
        "thesis": research_output.thesis,
        "intrinsic_value_per_share": intrinsic.expected_value_per_share,
        "audit_passed": True,
        "active_evidence_ids": [item.id for item in trace.ledger.active()],
        "active_hypothesis_ids": [item.id for item in research_output.hypotheses],
        "active_assumption_keys": [item.key for item in trace.assumptions],
    }
    store.promote_current(manifest, current_state)
    return WorkflowResult(
        RunStatus.COMPLETED, run_id, tuple(progress), final_report, report.to_dict(),
        intrinsic.expected_value_per_share, market.price, market_gap, (), str(run_dir),
    )


def market_loader_from_config(path: str | Path) -> MarketLoader:
    def load() -> MarketObservation:
        market = load_market_comparison(path)
        return MarketObservation(float(market["price"]), str(market["as_of"]), f"{path}#market_comparison")
    return load


def _save_blocked(
    store: StateStore,
    run_id: str,
    ticker: str,
    company: str,
    started: str,
    rounds: int,
    progress: list[str],
    reasons: tuple[str, ...],
    trace,
    thesis: str,
    counter_thesis: str,
    *,
    audit: dict,
) -> WorkflowResult:
    final_report = _render_report(company, None, None, None, thesis, counter_thesis, {}, audit, blocked=reasons)
    finished = iso_now()
    manifest = RunManifest(
        run_id, ticker, company, started, finished, RunStatus.VALUATION_BLOCKED,
        rounds, False, None, reasons,
    )
    run_dir = store.save_run(manifest, _artifacts(trace, thesis, counter_thesis, audit, final_report))
    return WorkflowResult(
        RunStatus.VALUATION_BLOCKED, run_id, tuple(progress), final_report, audit,
        None, None, None, reasons, str(run_dir),
    )


def _artifacts(trace, thesis: str, counter_thesis: str, audit: dict, report: str, *, valuation=None, market=None, delta=None) -> dict:
    return {
        "evidence_delta.json": trace.ledger.to_list(),
        "researcher.md": thesis,
        "redteam.md": counter_thesis,
        "bridge.json": [asdict(item) for item in trace.bridges],
        "assumptions.json": [asdict(item) for item in trace.assumptions],
        "valuation.json": valuation or {"suppressed": True},
        "audit.json": audit,
        "market_compare.json": market or {"not_loaded": True},
        "thesis_delta.json": delta or {},
        "final_report.md": report,
    }


def _render_report(company, intrinsic, market, gap, thesis, counter_thesis, delta, audit, *, blocked) -> str:
    if blocked:
        return "\n".join((
            "# VALUATION BLOCKED",
            "",
            "## 원인",
            *(f"- {item}" for item in blocked),
            "",
            "## Researcher A",
            thesis,
            "",
            "## Red Team B",
            counter_thesis,
            "",
            "목표가와 현재가 비교는 출력하지 않았습니다.",
        ))
    return f"""# {company} Research OS Report

## 결론 먼저
{thesis}

## 기존 Thesis 대비 변화
- 강화·신규: {', '.join(delta.get('strengthened_or_new', [])) or '없음'}
- 약화·폐기: {', '.join(delta.get('weakened_or_removed', [])) or '없음'}

## Red Team 결과
{counter_thesis}

## Expected / 미래가치 확률반영
{intrinsic:,.0f}원/주

## 현재가 비교
현재가 {market.price:,.0f}원 ({market.as_of}), intrinsic 대비 {gap:+.1%}

## 데이터 품질
Audit PASS. Probability는 UNCALIBRATED이며, 이 실행은 OCI v1.1 오프라인 회귀 fixture입니다.

## 분석 한계
실시간 DART·IR·정책 수집은 아직 연결되지 않았으므로 실제 투자판단용 최신 분석이 아닙니다.
"""


def _resolve_company(command: str) -> str:
    text = command.strip()
    if not text.startswith("분석시작"):
        raise ValueError("command must start with '분석시작'")
    company = text.removeprefix("분석시작").strip()
    if not company:
        raise ValueError("company is required")
    return company
