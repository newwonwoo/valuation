"""The executed cold-start probe: run the real runtime on a company that isn't here.

``stage_capability.probe_cold_start`` answers whether the providers *assemble*.
This module answers the stronger question: what actually *executes*. It runs the
canonical attested runtime (``strict_live_runtime.run_prism``) against a
synthetic KR company served entirely by an in-memory OpenDART stub — no
network, no credentials, no company module — and reports, stage by stage, how
far the engine got and the engine's own words for why it stopped.

The synthetic company (한빛제강, a fictional steelmaker) is *data for the
probe*, not a company adapter: it carries filings metadata and standard
financial-statement rows only. No valuation answer, no hypothesis text, no
assumption value appears anywhere in the fixture — the probe's purpose is to
find where the generic pipeline genuinely ends, and a fixture that smuggled
answers would defeat it.

The probe's transport is scripted-empty: the run is expected to fail closed
*before* the LLM staff stages today (at evidence breadth), so no proposal text
exists to script. When source breadth grows and the run reaches RESEARCHER_A,
this probe will fail loudly rather than silently passing — the scripted
transport raises on any unscripted call — which is exactly the demand for the
next honest fixture.
"""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

from .control_plane import StageStatus
from .kr_opendart_provider import OpenDartFilingSelection, OpenDartNetwork
from .llm_transport import ScriptedTransport
from .stage_capability import ColdStartOutcome
from .valuation_plan_compiler import SegmentMethodChoice


PROBE_AS_OF = "2026-08-27"
PROBE_CORP_CODE = "00999902"
PROBE_STOCK_CODE = "900991"
PROBE_COMPANY_NAME = "한빛제강"
PROBE_RUN_ID = "COLD-START-PROBE"

_PASSING_STATUSES = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


def _corp_archive() -> bytes:
    xml = (
        "<result>"
        f"<list><corp_code>{PROBE_CORP_CODE}</corp_code>"
        f"<corp_name>{PROBE_COMPANY_NAME}</corp_name>"
        f"<stock_code>{PROBE_STOCK_CODE}</stock_code>"
        "<modify_date>20260801</modify_date></list>"
        "<list><corp_code>00999903</corp_code><corp_name>다른회사</corp_name>"
        "<stock_code>900992</stock_code><modify_date>20260801</modify_date></list>"
        "</result>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return buffer.getvalue()


_FILING_ROWS = [
    {"rcept_no": "20260514000777", "report_nm": "분기보고서 (2026.03)",
     "rcept_dt": "20260514", "corp_code": PROBE_CORP_CODE, "stock_code": PROBE_STOCK_CODE},
    {"rcept_no": "20260318000888", "report_nm": "사업보고서 (2025.12)",
     "rcept_dt": "20260318", "corp_code": PROBE_CORP_CODE, "stock_code": PROBE_STOCK_CODE},
]


def _fact_rows() -> list[dict]:
    def row(account_id: str, sj_div: str, amount: str) -> dict:
        return {
            "rcept_no": "20260318000888", "corp_code": PROBE_CORP_CODE,
            "bsns_year": "2025", "reprt_code": "11011",
            "sj_div": sj_div, "account_id": account_id,
            "thstrm_amount": amount, "currency": "KRW",
        }

    return [
        row("ifrs-full_Revenue", "IS", "5200000000000"),
        row("dart_OperatingIncomeLoss", "IS", "410000000000"),
        row("ifrs-full_ProfitLoss", "IS", "300000000000"),
        row("ifrs-full_Assets", "BS", "9100000000000"),
        row("ifrs-full_Liabilities", "BS", "4200000000000"),
    ]


#: Synthetic industry series the probe's registry declares as verified. The
#: values are fixture data proving the collection plumbing; a production
#: registry ships with zero verified rows until an operator checks real series.
_PROBE_SERIES = {
    "PROBE_KOSIS_STEEL_PPI": [
        {
            "PRD_DE": "202606", "DT": "112.4",
            "PUBLISHED_AT": "2026-07-08T00:00:00Z",
            "FIRST_SEEN_AT": "2026-07-10T09:00:00+09:00",
            "REVISION_AT": "2026-07-08T00:00:00Z",
        },
        {
            "PRD_DE": "202607", "DT": "113.1",
            "PUBLISHED_AT": "2026-08-08T00:00:00Z",
            "FIRST_SEEN_AT": "2026-08-10T09:00:00+09:00",
            "REVISION_AT": "2026-08-08T00:00:00Z",
        },
    ],
    "PROBE_KOSIS_IRON_ORE_INPUT": [
        {
            "PRD_DE": "202607", "DT": "98.7",
            "PUBLISHED_AT": "2026-08-08T00:00:00Z",
            "FIRST_SEEN_AT": "2026-08-10T09:00:00+09:00",
            "REVISION_AT": "2026-08-08T00:00:00Z",
        },
    ],
    "PROBE_KOSIS_STEEL_OUTPUT": [
        {
            "PRD_DE": "202607", "DT": "104.2",
            "PUBLISHED_AT": "2026-08-08T00:00:00Z",
            "FIRST_SEEN_AT": "2026-08-10T09:00:00+09:00",
            "REVISION_AT": "2026-08-08T00:00:00Z",
        },
    ],
    "PROBE_KOSIS_STEEL_INVENTORY": [
        {
            "PRD_DE": "202606", "DT": "121.9",
            "PUBLISHED_AT": "2026-07-08T00:00:00Z",
            "FIRST_SEEN_AT": "2026-07-10T09:00:00+09:00",
            "REVISION_AT": "2026-07-08T00:00:00Z",
        },
        # Published after the probe cutoff: must never be selected.
        {
            "PRD_DE": "202612", "DT": "999.0",
            "PUBLISHED_AT": "2027-01-08T00:00:00Z",
            "FIRST_SEEN_AT": "2027-01-10T09:00:00+09:00",
            "REVISION_AT": "2027-01-08T00:00:00Z",
        },
    ],
}

_PROBE_SERIES_ROWS = """
series:
  - series_id: PROBE_KOSIS_STEEL_PPI
    source_id: KR_KOSIS_API
    metric: benchmark_price
    layer: authorized_market_data
    unit: dimensionless
    geography: KR
    definition_id: DEF_PROBE_STEEL_PPI
    definition: >-
      Synthetic producer-price index for basic steel used only by the cold-start
      probe; index level, not a company-realized price.
    url_template: https://probe.invalid/kosis/PROBE_KOSIS_STEEL_PPI.json
    api_key_env: ""
    published_at_field: PUBLISHED_AT
    first_seen_at_field: FIRST_SEEN_AT
    revision_at_field: REVISION_AT
    verified: true
  - series_id: PROBE_KOSIS_IRON_ORE_INPUT
    source_id: KR_KOSIS_API
    metric: input_price
    layer: authorized_market_data
    unit: dimensionless
    geography: KR
    definition_id: DEF_PROBE_IRON_ORE
    definition: >-
      Synthetic iron-ore input price index used only by the cold-start probe.
    url_template: https://probe.invalid/kosis/PROBE_KOSIS_IRON_ORE_INPUT.json
    api_key_env: ""
    published_at_field: PUBLISHED_AT
    first_seen_at_field: FIRST_SEEN_AT
    revision_at_field: REVISION_AT
    verified: true
  - series_id: PROBE_KOSIS_STEEL_OUTPUT
    source_id: KR_KOSIS_API
    metric: output_price
    layer: authorized_market_data
    unit: dimensionless
    geography: KR
    definition_id: DEF_PROBE_STEEL_OUTPUT
    definition: >-
      Synthetic steel product output price index used only by the cold-start probe.
    url_template: https://probe.invalid/kosis/PROBE_KOSIS_STEEL_OUTPUT.json
    api_key_env: ""
    published_at_field: PUBLISHED_AT
    first_seen_at_field: FIRST_SEEN_AT
    revision_at_field: REVISION_AT
    verified: true
  - series_id: PROBE_KOSIS_STEEL_INVENTORY
    source_id: KR_KOSIS_API
    metric: inventory
    layer: realized_or_filing
    unit: dimensionless
    geography: KR
    definition_id: DEF_PROBE_STEEL_INVENTORY
    definition: >-
      Synthetic industry inventory index used only by the cold-start probe.
    url_template: https://probe.invalid/kosis/PROBE_KOSIS_STEEL_INVENTORY.json
    api_key_env: ""
    published_at_field: PUBLISHED_AT
    first_seen_at_field: FIRST_SEEN_AT
    revision_at_field: REVISION_AT
    verified: true
"""


_PROBE_UNDERWRITING = """
target_id: KR:DART:00999902
as_of: "2026-08-27"
source_ref: https://github.com/newwonwoo/valuation/blob/main/docs/GENERIC_LIVE_PROVIDERS.md#the-executed-cold-start-probe
declarations:
  cash_cost:
    value: 610000
    unit: KRW_per_ton
    rationale: mid-cycle cash cost per tonne declared for the synthetic probe company.
  product_yield:
    value: 0.94
    unit: ratio
    rationale: declared steady-state yield for the synthetic probe company.
  plant_runs:
    value: 2
    unit: count
    rationale: declared annual furnace run count for the synthetic probe company.
  turnaround:
    value: 21
    unit: days
    rationale: declared annual maintenance turnaround days for the probe company.
  normalized_ebitda:
    value: 940
    unit: KRW_billion
    rationale: mid-cycle EBITDA normalized from the probe filing's revenue and margin history.
  normalized_multiple:
    value: 5.5
    unit: multiple
    rationale: through-cycle EV/EBITDA multiple declared for the probe cohort.
  ownership:
    value: 1.0
    unit: ratio
    rationale: single wholly-owned operating segment in the probe structure.
  ev_adjustment:
    value: -1200
    unit: KRW_billion
    rationale: net debt bridge from the probe balance sheet (liabilities less cash equivalents).
  diluted_shares:
    value: 95000000
    unit: shares
    rationale: diluted share count declared for the synthetic probe company.
"""

_PROBE_MARKET = """
market_comparison:
  price: 61000
  as_of: "2026-08-27"
  source_ref: https://probe.invalid/market/close-20260827
"""

_PROBE_STREET = json.dumps({
    "authorization_basis": "explicit_permission",
    "reports": [
        {
            "broker": "Probe Research",
            "analyst": "Probe Analyst",
            "published_date": "2026-08-20",
            "target_price": 70000,
            "target_price_currency": "KRW",
            "valuation_method": "EV/EBITDA",
            "base_year": "2026E",
            "estimates": [
                {"metric": "ebitda", "period": "2026E", "value": 980,
                 "unit": "KRW_billion"}
            ],
            "source_ref": "https://probe.invalid/street/explicit-permission-export",
        }
    ],
})


def _uw_id(metric: str) -> str:
    return f"UW:KR:DART:{PROBE_CORP_CODE}:{metric}"


def _staff_scripts() -> dict[str, tuple[str, ...]]:
    """Deterministic proposals for the probe's staff seats.

    The scripts cite only Evidence IDs the run's own collectors deterministically
    produce (declared underwriting and industry series), and every bridge value
    equals its cited Evidence value — the compiler re-derives and would reject
    anything else. This proves the pipeline; proposal *quality* with a live
    model remains explicitly unproven.
    """
    hypothesis = {
        "rationale": (
            "Mid-cycle normalized earnings with declared underwriting form the "
            "basis for a through-cycle multiple valuation."
        ),
        "hypotheses": [{
            "id": "H:PROBE:MIDCYCLE",
            "statement": "Declared mid-cycle EBITDA of 940 KRW bn is sustainable",
            "causal_chain": [
                "benchmark steel prices and declared cash cost",
                "normalized mid-cycle margin",
                "enterprise value",
            ],
            "supporting_evidence_ids": [
                _uw_id("normalized_ebitda"),
                "INDSER:PROBE_KOSIS_STEEL_PPI:202607",
            ],
            "contradicting_evidence_ids": [],
            "kill_conditions": [
                "benchmark price index falls below 90 for two consecutive quarters"
            ],
            "next_checks": ["next quarterly filing"],
        }],
        "requested_evidence": [],
        "scanner_reinforcements": [],
        "context_strength_linkage": {
            "not_applicable_reason": (
                "No non-obvious environment-to-strength connection is observable "
                "in the collected evidence for this synthetic probe run."
            )
        },
    }
    red_team = {
        "counter_thesis": (
            "The declared mid-cycle EBITDA may embed peak-adjacent margins; the "
            "multiple may not hold in a downcycle with rising input prices."
        ),
        "issues": [{
            "id": "R:PROBE:CYCLE",
            "description": "single-scenario run cannot express downcycle asymmetry",
            "blocking": False,
            "requested_evidence": ["multi-scenario underwriting"],
        }],
        "requested_evidence": [],
    }

    def draft(key: str, variable: str, unit: str) -> dict:
        return {
            "assumption_key": key,
            "scenario_id": "Base",
            "hypothesis_id": "H:PROBE:MIDCYCLE",
            "evidence_ids": [_uw_id(key)],
            "affected_variable": variable,
            "direction": "unchanged",
            "value": {"normalized_ebitda": 940, "normalized_multiple": 5.5,
                      "ownership": 1.0, "ev_adjustment": -1200,
                      "diluted_shares": 95000000}[key],
            "unit": unit,
            "canonical_unit": unit,
            "transform_id": "identity_observation",
            "rationale": "declared underwriting carried through unchanged",
            "confidence": 0.6,
            "kill_condition": "underwriting revision or contradicting filing",
            "verification_event": "next annual filing",
            "economic_path_id": f"path:core:{key}",
        }

    bridge = {
        "rationale": "Evidence-backed pass-through of the declared underwriting set.",
        "drafts": [
            draft("normalized_ebitda", "margin", "KRW_billion"),
            draft("normalized_multiple", "multiple", "multiple"),
            draft("ownership", "segment_value", "ratio"),
            draft("ev_adjustment", "net_debt", "KRW_billion"),
            draft("diluted_shares", "share_count", "shares"),
        ],
    }
    return {
        "intelligence_officer": (json.dumps(hypothesis),),
        "red_team_officer": (json.dumps(red_team),),
        "bridge_analyst": (json.dumps(bridge),),
    }


def _probe_series_registry_path() -> str:
    directory = tempfile.mkdtemp(prefix="cold-start-series-")
    path = Path(directory) / "series_registry.yaml"
    path.write_text("version: 1\npurpose: cold-start probe fixture\n" + _PROBE_SERIES_ROWS,
                    encoding="utf-8")
    return str(path)


def probe_fetch_text(url: str) -> str:
    if "probe.invalid/kosis/" in url:
        series_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        return json.dumps(_PROBE_SERIES[series_id])
    if "list.json" in url:
        return json.dumps({"status": "000", "list": _FILING_ROWS})
    if "company.json" in url:
        # KSIC 24xxx: 1차 금속 제조업 -> commodity_price_taker / process_spread.
        return json.dumps(
            {"status": "000", "corp_code": PROBE_CORP_CODE,
             "corp_name": PROBE_COMPANY_NAME, "stock_code": PROBE_STOCK_CODE,
             "induty_code": "24122"}
        )
    if "fnlttSinglAcnt" in url:
        return json.dumps({"status": "000", "list": _fact_rows()})
    raise AssertionError(f"cold-start probe received an unexpected URL: {url}")


_FILING_BODY = """
<BODY>
<P>II. 사업의 내용</P>
<P>3. 생산 및 설비</P>
<TABLE>
<TR><TD>생산능력</TD><TD>5,400,000 백만원</TD></TR>
<TR><TD>생산실적</TD><TD>4,860,000 백만원</TD></TR>
<TR><TD>평균가동률</TD><TD>90.0 %</TD></TR>
</TABLE>
<P>4. 주요 제품 가격변동추이</P>
<TABLE>
<TR><TD>판매단가</TD><TD>852,000 원/톤</TD></TR>
</TABLE>
</BODY>
"""


def _document_archive() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("20260318000888.xml", _FILING_BODY)
    return buffer.getvalue()


def probe_fetch_bytes(url: str) -> bytes:
    if "corpCode.xml" in url:
        return _corp_archive()
    if "document.xml" in url:
        return _document_archive()
    raise AssertionError(f"cold-start probe received an unexpected binary URL: {url}")


def probe_network() -> OpenDartNetwork:
    return OpenDartNetwork(
        fetch_text=probe_fetch_text,
        fetch_bytes=probe_fetch_bytes,
        api_key="COLD-START-PROBE-KEY",
    )


def _probe_fixture_file(name: str, content: str) -> str:
    directory = tempfile.mkdtemp(prefix="cold-start-fixture-")
    path = Path(directory) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def probe_runtime_spec():
    from .generic_live_providers import GenericKRRuntimeSpec

    return GenericKRRuntimeSpec(
        as_of=PROBE_AS_OF,
        industry_series_registry_path=_probe_series_registry_path(),
        declared_underwriting_path=_probe_fixture_file(
            "underwriting.yaml", _PROBE_UNDERWRITING
        ),
        market_config_path=_probe_fixture_file("market.yaml", _PROBE_MARKET),
        street_export_path=_probe_fixture_file("street.json", _PROBE_STREET),
        market_currency="KRW",
        scenario_ids=("Base",),
        method_choices=(
            SegmentMethodChoice("core", "commodity_price_taker", "normalized_multiple"),
        ),
        filing=OpenDartFilingSelection(
            business_year="2025",
            report_code="11011",
            fiscal_period_end="2025-12-31",
            checked_at=PROBE_AS_OF,
            segment_id="core",
        ),
    )


def execute_cold_start_probe(state_root: str | None = None) -> ColdStartOutcome:
    """Run the canonical runtime on the synthetic company and report the truth."""
    from .cli_runtime import LiveAnalysisRequest
    from .generic_live_providers import build_generic_kr_runtime_factory
    from .strict_live_runtime import run_prism

    factory = build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=ScriptedTransport(_staff_scripts()),
        spec=probe_runtime_spec(),
    )

    def run(root: str) -> ColdStartOutcome:
        request = LiveAnalysisRequest(
            command=f"분석시작 {PROBE_COMPANY_NAME}",
            company_query=PROBE_COMPANY_NAME,
            state_root=root,
            run_id=PROBE_RUN_ID,
            jurisdiction="KR",
        )
        result = run_prism(factory(request)).result
        reached: list[str] = []
        route_skipped: list[str] = []
        blocking_stage: str | None = None
        blocking_reason = ""
        for trace in result.stage_traces:
            if trace.status is StageStatus.SKIPPED_NOT_APPLICABLE:
                # The probe's method path (normalized_multiple) needs neither a
                # Beta nor a WACC, so those stages are passed without running.
                # Recording them apart from `reached` keeps the report from
                # claiming a provider was proven by a run that never used it.
                route_skipped.append(trace.stage)
            elif trace.status in _PASSING_STATUSES:
                reached.append(trace.stage)
            else:
                blocking_stage = trace.stage
                blocking_reason = f"{trace.status.value}: {trace.rationale}"
                break
        return ColdStartOutcome(
            probed=True,
            reached=tuple(reached),
            route_skipped=tuple(route_skipped),
            blocking_stage=blocking_stage,
            blocking_reason=blocking_reason,
        )

    if state_root is not None:
        return run(state_root)
    with tempfile.TemporaryDirectory(prefix="cold-start-probe-") as root:
        return run(root)
