"""The second cold-start probe: a shipbuilder on the backlog-DCF route.

The steel probe (:mod:`cold_start_probe`) proves the beta-free route; its
method path never runs HIERARCHICAL_BETA_ESTIMATION, WACC_VALIDATION or the
Warranted-PER chain, so those providers stay honestly unexercised in its
receipt. This module is the complementary route: the fictional shipbuilder
대양중공업 (KSIC 31111 → contracted_backlog / backlog_burn_dcf) with a declared
underwriting file AND a declared risk pack, driving the discount-rate chain —
Beta, WACC, the DCF fingerprint, the audit's hash-bound risk-path check — to an
attested value. The capability validator runs both routes and merges them, so
"proven" means executed on SOME route, never inferred.

Fixture doctrine matches the steel probe: filings metadata, standard financial
rows, statutory 수주 tables, and operator declarations only. The four
contract-structure stubs are marked DIAGNOSTIC in their rationale because
routing filing facts through the underwriting door is not their honest layer —
they exist so the probe exercises the stages beyond collection; the honest
collector for them is a named, open gap.
"""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

import yaml

from .cli_runtime import LiveAnalysisRequest
from .control_plane import StageStatus
from .generic_live_providers import (
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
)
from .kr_opendart_provider import OpenDartFilingSelection, OpenDartNetwork
from .stage_capability import ColdStartOutcome
from .strict_live_runtime import run_prism
from .valuation_plan_compiler import SegmentMethodChoice


AS_OF = "2026-08-27"
CORP = "00888801"
STOCK = "900881"
NAME = "대양중공업"
SEG = "core"
YEARS = 3
TARGET = f"KR:DART:{CORP}"
RCEPT = "20260320000456"

#: The four contract-structure items ``contracted_backlog`` requires and no
#: collector in this repository produces.
UNCOLLECTED_CONTRACT_EVIDENCE = (
    "revenue_recognition",
    "cancellation_terms",
    "contract_liabilities",
    "lead_time",
)

_PASSING = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


# --------------------------------------------------------------- OpenDART stub


def _corp_archive() -> bytes:
    xml = (
        "<result>"
        f"<list><corp_code>{CORP}</corp_code><corp_name>{NAME}</corp_name>"
        f"<stock_code>{STOCK}</stock_code><modify_date>20260801</modify_date></list>"
        "<list><corp_code>00888802</corp_code><corp_name>딴회사</corp_name>"
        "<stock_code>900882</stock_code><modify_date>20260801</modify_date></list>"
        "</result>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return buffer.getvalue()


_FILINGS = [
    {"rcept_no": "20260812000123", "report_nm": "반기보고서 (2026.06)",
     "rcept_dt": "20260812", "corp_code": CORP, "stock_code": STOCK},
    {"rcept_no": RCEPT, "report_nm": "사업보고서 (2025.12)",
     "rcept_dt": "20260320", "corp_code": CORP, "stock_code": STOCK},
]


def _facts() -> list[dict]:
    def row(account_id: str, sj_div: str, amount: str) -> dict:
        return {"rcept_no": RCEPT, "corp_code": CORP, "bsns_year": "2025",
                "reprt_code": "11011", "sj_div": sj_div, "account_id": account_id,
                "thstrm_amount": amount, "currency": "KRW"}

    return [
        row("ifrs-full_Revenue", "IS", "3800000000000"),
        row("dart_OperatingIncomeLoss", "IS", "190000000000"),
        row("ifrs-full_ProfitLoss", "IS", "140000000000"),
        row("ifrs-full_Assets", "BS", "7400000000000"),
        row("ifrs-full_Liabilities", "BS", "5100000000000"),
    ]


#: A shipbuilder's 수주 상황 table — the metrics this archetype actually lives on.
_FILING_BODY = """
<BODY>
<P>II. 사업의 내용</P>
<P>2. 수주 상황</P>
<TABLE>
<TR><TD>수주총액</TD><TD>4,150,000 백만원</TD></TR>
<TR><TD>수주잔고</TD><TD>12,600,000 백만원</TD></TR>
</TABLE>
<P>3. 생산 및 설비</P>
<TABLE>
<TR><TD>생산능력</TD><TD>4,000,000 백만원</TD></TR>
<TR><TD>생산실적</TD><TD>3,800,000 백만원</TD></TR>
<TR><TD>평균가동률</TD><TD>95.0 %</TD></TR>
</TABLE>
</BODY>
"""


def _fetch_text(url: str) -> str:
    if "list.json" in url:
        return json.dumps({"status": "000", "list": _FILINGS})
    if "company.json" in url:
        # KSIC 31111 선박 및 보트 건조업 -> contracted_backlog.
        return json.dumps({"status": "000", "corp_code": CORP, "corp_name": NAME,
                           "stock_code": STOCK, "induty_code": "31111"})
    if "fnlttSinglAcnt" in url:
        return json.dumps({"status": "000", "list": _facts()})
    raise AssertionError(f"unexpected URL: {url}")


def _fetch_bytes(url: str) -> bytes:
    if "corpCode.xml" in url:
        return _corp_archive()
    if "document.xml" in url:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr(f"{RCEPT}.xml", _FILING_BODY)
        return buffer.getvalue()
    raise AssertionError(f"unexpected binary URL: {url}")


# ------------------------------------------------------- declared underwriting

UNDERWRITING: dict[str, tuple[float | int, str, str]] = {
    "opening_backlog": (12600, "KRW_billion", "order book carried into the model year, declared against the filing's 수주잔고 table."),
    "opening_revenue": (3800, "KRW_billion", "prior-year revenue base declared from the filing's income statement."),
    "new_orders_year_1": (4200, "KRW_billion", "declared year-1 order intake for this synthetic shipbuilder cold run."),
    "new_orders_year_2": (4400, "KRW_billion", "declared year-2 order intake for this synthetic shipbuilder cold run."),
    "new_orders_year_3": (4600, "KRW_billion", "declared year-3 order intake for this synthetic shipbuilder cold run."),
    "backlog_burn_rate_year_1": (0.31, "ratio", "declared year-1 share of opening backlog converted to revenue."),
    "backlog_burn_rate_year_2": (0.32, "ratio", "declared year-2 share of opening backlog converted to revenue."),
    "backlog_burn_rate_year_3": (0.33, "ratio", "declared year-3 share of opening backlog converted to revenue."),
    "operating_margin_year_1": (0.055, "ratio", "declared year-1 operating margin on delivered vessels."),
    "operating_margin_year_2": (0.062, "ratio", "declared year-2 operating margin as higher-priced orders deliver."),
    "operating_margin_year_3": (0.068, "ratio", "declared year-3 operating margin at steady-state mix."),
    "operating_tax_rate": (0.22, "ratio", "declared effective operating tax rate for this cold-run structure."),
    "depreciation_rate_of_revenue": (0.028, "ratio", "declared depreciation as a share of revenue for the yard asset base."),
    "maintenance_capex_rate_of_revenue": (0.031, "ratio", "declared maintenance capex as a share of revenue for the yards."),
    "incremental_working_capital_rate": (0.015, "ratio", "declared incremental working capital per unit of revenue growth."),
    "terminal_growth": (0.01, "ratio", "declared terminal growth rate for the post-forecast order cycle."),
    "terminal_roic": (0.09, "ratio", "declared terminal return on invested capital for the yard business."),
    "ownership": (1.0, "ratio", "single wholly-owned yard segment in this cold-run structure."),
    "ev_adjustment": (-2100, "KRW_billion", "net debt bridge declared from the cold-run balance sheet."),
    "diluted_shares": (71000000, "shares", "diluted share count declared for this synthetic shipbuilder."),
}

#: DIAGNOSTIC ONLY. These four are filing/contract facts, not analyst judgments;
#: routing them through the underwriting file is exactly the layer laundering the
#: engine exists to prevent. They exist here solely so the SECOND boundary (beta)
#: can be reached and pinned, and the test that uses them says so in its name.
DIAGNOSTIC_CONTRACT_STUBS: dict[str, tuple[float | int, str, str]] = {
    "revenue_recognition": (1.0, "ratio", "DIAGNOSTIC stub standing in for the percentage-of-completion policy note."),
    "cancellation_terms": (0.03, "ratio", "DIAGNOSTIC stub standing in for the contractual cancellation ceiling."),
    "contract_liabilities": (2450, "KRW_billion", "DIAGNOSTIC stub standing in for customer advances held as contract liabilities."),
    "lead_time": (28, "months", "DIAGNOSTIC stub standing in for average contracted delivery lead time."),
}


def _underwriting_yaml(rows: dict) -> str:
    payload = {
        "target_id": TARGET,
        "as_of": AS_OF,
        "source_ref": "https://github.com/newwonwoo/valuation/blob/main/docs/GENERIC_LIVE_PROVIDERS.md#cold-run",
        "declarations": {
            key: {"value": value, "unit": unit, "rationale": why}
            for key, (value, unit, why) in rows.items()
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _uw_id(metric: str) -> str:
    return f"UW:{TARGET}:{metric}"


# ------------------------------------------------------------- scripted staff


def _scripts() -> dict[str, tuple[str, ...]]:
    hypothesis = {
        "rationale": ("A contracted order book converts to revenue on a declared burn "
                      "schedule, so near-term revenue is drawn down from a stock rather "
                      "than forecast freely."),
        "hypotheses": [{
            "id": "H:DAEYANG:BACKLOG",
            "statement": "The declared 12,600 KRW bn order book burns down at the declared rate",
            "causal_chain": ["contracted order book", "delivery-schedule revenue",
                             "operating margin on delivered hulls", "enterprise value"],
            "supporting_evidence_ids": [_uw_id("opening_backlog"), _uw_id("new_orders_year_1")],
            "contradicting_evidence_ids": [],
            "kill_conditions": ["order cancellations exceed 10% of opening backlog"],
            "next_checks": ["next half-year filing's 수주잔고 table"],
        }],
        "requested_evidence": [],
        "scanner_reinforcements": [],
        "context_strength_linkage": {"not_applicable_reason": (
            "No non-obvious environment-to-strength connection is observable in the "
            "collected evidence for this synthetic cold run.")},
    }
    red_team = {
        "counter_thesis": ("A shipbuilder's backlog is priced at order date, so a "
                           "steel-plate cost spike compresses the margin the declared "
                           "burn schedule assumes."),
        "issues": [{"id": "R:DAEYANG:COST", "blocking": False,
                    "description": "single-scenario run cannot express input-cost asymmetry",
                    "requested_evidence": ["multi-scenario underwriting"]}],
        "requested_evidence": [],
    }

    def variable(key: str) -> str:
        if key == "ownership":
            return "segment_value"
        if key == "diluted_shares":
            return "share_count"
        if key == "ev_adjustment":
            return "net_debt"
        if "burn_rate" in key:
            return "utilization"
        if "margin" in key or "rate" in key or key == "terminal_roic":
            return "margin"
        return "quantity"

    def draft(key: str) -> dict:
        value, unit, _ = UNDERWRITING[key]
        return {"assumption_key": key, "scenario_id": "Base",
                "hypothesis_id": "H:DAEYANG:BACKLOG", "evidence_ids": [_uw_id(key)],
                "affected_variable": variable(key), "direction": "unchanged",
                "value": value, "unit": unit, "canonical_unit": unit,
                "transform_id": "identity_observation",
                "rationale": "declared underwriting carried through unchanged",
                "confidence": 0.6,
                "kill_condition": "underwriting revision or contradicting filing",
                "verification_event": "next periodic filing",
                "economic_path_id": f"path:{SEG}:{key}"}

    bridge = {"rationale": "Evidence-backed pass-through of the declared order-book set.",
              "drafts": [draft(key) for key in UNDERWRITING]}
    return {"intelligence_officer": (json.dumps(hypothesis),),
            "red_team_officer": (json.dumps(red_team),),
            "bridge_analyst": (json.dumps(bridge),)}


class _RepeatingTransport:
    """Repeats each role's scripted answer so a rejection surfaces as the engine's
    own contract error rather than a transport-exhaustion error."""

    def __init__(self, scripts: dict[str, tuple[str, ...]]) -> None:
        self._scripts = scripts
        self._counts: dict[str, int] = {}

    def complete(self, *, role: str, prompt: str) -> str:
        index = self._counts.get(role, 0)
        self._counts[role] = index + 1
        answers = self._scripts.get(role) or ("",)
        return answers[min(index, len(answers) - 1)]


def _tmpfile(name: str, content: str) -> str:
    directory = tempfile.mkdtemp(prefix="daeyang-")
    path = Path(directory) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _risk_peer(peer_id: str, beta: float, debt: float, equity: float) -> dict:
    return {
        "peer_id": peer_id,
        "beta": {"benchmark": "코스피", "beta": beta, "observations": 250,
                 "start_date": "2025-08-20", "end_date": "2026-08-20"},
        "capital": {"debt": debt, "equity_market_value": equity, "tax_rate": 0.24,
                    "as_of": "2026-06-30",
                    "source_ref": f"https://probe.invalid/capital/{peer_id}"},
        "beta_source_ref": f"https://probe.invalid/krx/beta/{peer_id}",
    }


def _risk_pack_yaml() -> str:
    payload = {
        "target_id": TARGET,
        "as_of": AS_OF,
        "source_ref": "https://probe.invalid/risk-pack/daeyang",
        "cash_flow_currency": "KRW",
        "risk_free_rate": {"time": "20260820", "value": 3.10, "unit": "연%",
                           "name": "국고채 10년",
                           "source_ref": "https://ecos.bok.or.kr/api/rf-10y"},
        "country_risk": {"country": "Korea", "as_of": "2026-08-01",
                         "mature_market_erp": 0.0508,
                         "country_risk_premium": 0.0057,
                         "total_equity_risk_premium": 0.0565,
                         "adjusted_default_spread": 0.0030,
                         "corporate_tax_rate": 0.24, "rating": "AA"},
        "marginal_debt": {
            "series": {"time": "20260820", "value": 4.35, "unit": "연%",
                       "name": "회사채 AA- 3년",
                       "source_ref": "https://ecos.bok.or.kr/api/corp-aa-minus-3y"},
            "credit_rating": "AA-", "maturity": "3Y",
            "rating_source_ref": "https://probe.invalid/rating/issuer"},
        "beta_levels": {
            "L1_BROAD_SECTOR": {
                "selection_rationale": "KOSPI 대형 산업재 상장사 — 광의 섹터 사전확률로 사용.",
                "risk_driver_features": ["industrial cyclicality"],
                "peers": [_risk_peer("PEER-IND-1", 1.02, 4200, 9800),
                          _risk_peer("PEER-IND-2", 0.96, 3100, 11200)]},
            "L2_INDUSTRY": {
                "selection_rationale": "국내 상장 조선업 동종사 — 수주-인도 사이클 공유.",
                "risk_driver_features": ["order cycle"],
                "peers": [_risk_peer("PEER-SHIP-1", 1.24, 5200, 7400),
                          _risk_peer("PEER-SHIP-2", 1.18, 4800, 8100)]},
            "L3_RISK_DRIVER_SUBINDUSTRY": {
                "selection_rationale": "상선 중심 야드 — 잔고 회전이 유사한 하위군.",
                "risk_driver_features": ["backlog duration", "operating leverage"],
                "peers": [_risk_peer("PEER-YARD-1", 1.31, 6100, 6900)]},
            "L4_ECONOMIC_TWINS": {
                "selection_rationale": "수주잔고 3년치 이상 — 경제적 쌍둥이 조건 일치.",
                "risk_driver_features": ["capacity intensity", "lead time"],
                "peers": [_risk_peer("PEER-TWIN-1", 1.27, 5600, 7200)]},
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


_MARKET_YAML = """
market_comparison:
  price: 28500
  as_of: "2026-08-27"
  source_ref: https://probe.invalid/market/close-daeyang-20260827
"""

_STREET_JSON = json.dumps({
    "authorization_basis": "explicit_permission",
    "reports": [{
        "broker": "Probe Research", "analyst": "Probe Analyst",
        "published_date": "2026-08-19", "target_price": 34000,
        "target_price_currency": "KRW", "valuation_method": "DCF",
        "base_year": "2026E",
        "estimates": [{"metric": "ebitda", "period": "2026E", "value": 320,
                       "unit": "KRW_billion"}],
        "source_ref": "https://probe.invalid/street/daeyang-export",
    }],
})


def run_backlog_probe(underwriting_rows: dict, *, with_risk_pack: bool = False,
                      state_root: str | None = None):
    extras = {}
    if with_risk_pack:
        extras = {
            "declared_risk_path": _tmpfile("risk_pack.yaml", _risk_pack_yaml()),
            "market_config_path": _tmpfile("market.yaml", _MARKET_YAML),
            "street_export_path": _tmpfile("street.json", _STREET_JSON),
            "market_currency": "KRW",
        }
    spec = GenericKRRuntimeSpec(
        as_of=AS_OF,
        forecast_years=YEARS,
        declared_underwriting_path=_tmpfile(
            "underwriting.yaml", _underwriting_yaml(underwriting_rows)
        ),
        scenario_ids=("Base",),
        method_choices=(
            SegmentMethodChoice(SEG, "contracted_backlog", "backlog_burn_dcf"),
        ),
        filing=OpenDartFilingSelection(
            business_year="2025", report_code="11011",
            fiscal_period_end="2025-12-31", checked_at=AS_OF, segment_id=SEG),
        **extras,
    )
    factory = build_generic_kr_runtime_factory(
        network=OpenDartNetwork(fetch_text=_fetch_text, fetch_bytes=_fetch_bytes,
                                api_key="COLD-RUN-KEY"),
        transport=_RepeatingTransport(_scripts()),
        spec=spec,
    )
    def _run(root: str):
        request = LiveAnalysisRequest(
            command=f"분석시작 {NAME}", company_query=NAME, state_root=root,
            run_id="COLD-RUN-DAEYANG", jurisdiction="KR")
        return run_prism(factory(request)).result

    if state_root is not None:
        result = _run(state_root)
    else:
        with tempfile.TemporaryDirectory(prefix="daeyang-run-") as root:
            result = _run(root)
    reached: list[str] = []
    route_skipped: list[str] = []
    stop_stage = None
    stop_reason = ""
    for trace in result.stage_traces:
        if trace.status is StageStatus.SKIPPED_NOT_APPLICABLE:
            route_skipped.append(trace.stage)
        elif trace.status in _PASSING:
            reached.append(trace.stage)
        else:
            stop_stage = trace.stage
            stop_reason = f"{trace.status.value}: {trace.rationale}"
            break
    return tuple(reached), tuple(route_skipped), stop_stage, stop_reason, result.data




def execute_backlog_cold_start_probe(state_root: str | None = None) -> ColdStartOutcome:
    """Run the shipbuilder route end to end and report what actually executed."""
    reached, route_skipped, stop_stage, stop_reason, _ = run_backlog_probe(
        {**UNDERWRITING, **DIAGNOSTIC_CONTRACT_STUBS},
        with_risk_pack=True,
        state_root=state_root,
    )
    return ColdStartOutcome(
        probed=True,
        reached=reached,
        route_skipped=route_skipped,
        blocking_stage=stop_stage,
        blocking_reason=stop_reason or "",
    )
