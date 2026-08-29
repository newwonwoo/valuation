"""A company sharing nothing with any fixture here: does the engine generalize?

대양중공업 is a fictional SHIPBUILDER, chosen to differ from the steel cold-start
probe on every axis that could hide a hard-coding:

    axis              steel probe (한빛제강)       this run (대양중공업)
    KSIC              24122 1차 금속               31111 선박 건조
    archetype         commodity_price_taker        contracted_backlog
    method            normalized_multiple          backlog_burn_dcf
    evaluator         NormalizedMultipleEvaluator  BacklogBurnDCFEvaluator
    assumption keys   9                            20 (3-year roll-forward)
    needs beta/WACC   no                           YES
    filing KPIs used  생산능력/가동률/판매단가       수주총액/수주잔고

No engine code is company-specific for this to work, and these tests pin the two
places the run honestly ends rather than inventing a number:

1. ``contracted_backlog`` demands six evidence items (archetype_module_registry).
   The filing collector supplies orders and backlog from the disclosed 수주 table;
   the four *contract-structure* items have no collector, so collection fails
   closed and NAMES them. That is the honest boundary, not a defect to paper over.
2. Given those four, the run reaches VALUATION_METHOD_INTENT and stops at
   HIERARCHICAL_BETA_ESTIMATION: 9 of the 14 execution families require beta and
   WACC, and the generic cold-start factory wires no risk-provider pack. The
   steel probe completed 33/33 only because normalized_multiple is one of the
   five families needing neither — a narrower claim than "33/33" sounds.
"""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

import pytest
import yaml

from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.control_plane import StageStatus
from valuation_engine.generic_live_providers import (
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
    required_assumption_keys,
)
from valuation_engine.kr_opendart_provider import (
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.strict_live_runtime import run_prism
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice


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

_UW: dict[str, tuple[float | int, str, str]] = {
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
_DIAGNOSTIC_CONTRACT_STUBS: dict[str, tuple[float | int, str, str]] = {
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
        value, unit, _ = _UW[key]
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
              "drafts": [draft(key) for key in _UW]}
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


def _execute(underwriting_rows: dict):
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
    )
    factory = build_generic_kr_runtime_factory(
        network=OpenDartNetwork(fetch_text=_fetch_text, fetch_bytes=_fetch_bytes,
                                api_key="COLD-RUN-KEY"),
        transport=_RepeatingTransport(_scripts()),
        spec=spec,
    )
    with tempfile.TemporaryDirectory(prefix="daeyang-run-") as root:
        request = LiveAnalysisRequest(
            command=f"분석시작 {NAME}", company_query=NAME, state_root=root,
            run_id="COLD-RUN-DAEYANG", jurisdiction="KR")
        result = run_prism(factory(request)).result
    reached: list[str] = []
    stop_stage = None
    stop_reason = ""
    for trace in result.stage_traces:
        if trace.status in _PASSING:
            reached.append(trace.stage)
        else:
            stop_stage = trace.stage
            stop_reason = trace.rationale
            break
    return tuple(reached), stop_stage, stop_reason


@pytest.fixture(scope="module")
def cold_run():
    return _execute(_UW)


@pytest.fixture(scope="module")
def diagnostic_run():
    return _execute({**_UW, **_DIAGNOSTIC_CONTRACT_STUBS})


def test_the_engine_routes_an_unseen_shipbuilder_without_company_code(cold_run):
    reached, _, _ = cold_run
    # Resolution, classification, archetype routing and module planning all work
    # on a KSIC and archetype no fixture in this repository has ever used.
    assert reached[:7] == (
        "COMPANY_RESOLUTION",
        "LOAD_COMPANY_STATE",
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT",
        "SOURCE_FRESHNESS_PRECHECK",
        "SEGMENT_DECOMPOSITION",
        "INDUSTRY_DNA_ROUTE",
        "MODULE_REQUIREMENT_PLAN",
    )


def test_the_cold_run_fails_closed_and_names_the_uncollected_contract_evidence(cold_run):
    _, stop_stage, stop_reason = cold_run
    assert stop_stage == "PRIMARY_EVIDENCE_COLLECTION"
    for metric in UNCOLLECTED_CONTRACT_EVIDENCE:
        assert metric in stop_reason, metric
    # orders and backlog came from the filing's own 수주 table, so they are NOT
    # among the gaps — the filing KPI collector really did the work.
    assert "core:required_evidence:orders" not in stop_reason
    assert "core:required_evidence:backlog" not in stop_reason


def test_the_backlog_route_demands_a_twenty_key_roll_forward():
    keys = required_assumption_keys(
        method_choices=(
            SegmentMethodChoice(SEG, "contracted_backlog", "backlog_burn_dcf"),
        ),
        forecast_years=YEARS,
    )
    assert len(keys) == 20
    assert "opening_backlog" in keys and "backlog_burn_rate_year_3" in keys
    # Nothing from the steel probe's multiple route leaks in.
    assert "normalized_multiple" not in keys


def test_diagnostic_run_reaches_the_beta_gate_the_generic_factory_cannot_serve(
    diagnostic_run,
):
    """With the four contract facts present, the whole LLM-staffed middle works.

    This is a DIAGNOSTIC: those four entered as declared underwriting, which is
    not their honest layer. It exists to prove the next boundary is the discount
    rate, not something further upstream.
    """
    reached, stop_stage, stop_reason = diagnostic_run
    for stage in (
        "PRIMARY_EVIDENCE_COLLECTION",
        "EVIDENCE_LEDGER",
        "RESEARCHER_A",
        "BLIND_RED_TEAM_B",
        "EVIDENCE_TO_ASSUMPTION_BRIDGE",
        "SCENARIO_BUILD",
        "VALUATION_METHOD_INTENT",
    ):
        assert stage in reached, stage
    assert stop_stage == "HIERARCHICAL_BETA_ESTIMATION"
    assert "Hierarchical Beta" in stop_reason
    assert "LIVE_PRIMARY provider" in stop_reason


def test_only_the_beta_free_families_can_cold_start_today():
    """The honest scope of the steel probe's 33/33: five of fourteen families.

    Nine families require beta and WACC, and ``GenericKRRuntimeSpec`` has no
    risk-provider slot to satisfy them. Pinning the split here keeps the
    capability accounting from drifting back into an unqualified '33/33'.
    """
    registry = yaml.safe_load(
        Path("config/valuation_method_capability_registry.yaml").read_text(
            encoding="utf-8"
        )
    )["execution_families"]
    beta_free = {
        name for name, row in registry.items() if not row.get("requires_beta")
    }
    assert beta_free == {
        "normalized_multiple",
        "normalized_ebitda_multiple",
        "ffo_multiple",
        "net_asset_value",
        "sotp",
    }
    assert len(registry) - len(beta_free) == 9
    # The generic cold-start spec exposes no beta/WACC provider slot at all.
    assert not any(
        "beta" in field or "wacc" in field
        for field in GenericKRRuntimeSpec.__dataclass_fields__
    )
