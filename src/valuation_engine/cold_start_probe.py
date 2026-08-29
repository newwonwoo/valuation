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

_PASSING_STATUSES = {StageStatus.PASS, StageStatus.WARNING}


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
        {"PRD_DE": "202606", "DT": "112.4"},
        {"PRD_DE": "202607", "DT": "113.1"},
    ],
    "PROBE_KOSIS_IRON_ORE_INPUT": [
        {"PRD_DE": "202607", "DT": "98.7"},
    ],
    "PROBE_KOSIS_STEEL_OUTPUT": [
        {"PRD_DE": "202607", "DT": "104.2"},
    ],
    "PROBE_KOSIS_STEEL_INVENTORY": [
        {"PRD_DE": "202606", "DT": "121.9"},
        # Published after the probe cutoff: must never be selected.
        {"PRD_DE": "202612", "DT": "999.0"},
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
    verified: true
"""


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


def probe_runtime_spec():
    from .generic_live_providers import GenericKRRuntimeSpec

    return GenericKRRuntimeSpec(
        as_of=PROBE_AS_OF,
        industry_series_registry_path=_probe_series_registry_path(),
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
        transport=ScriptedTransport({}),
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
        blocking_stage: str | None = None
        blocking_reason = ""
        for trace in result.stage_traces:
            if trace.status in _PASSING_STATUSES:
                reached.append(trace.stage)
            else:
                blocking_stage = trace.stage
                blocking_reason = f"{trace.status.value}: {trace.rationale}"
                break
        return ColdStartOutcome(
            probed=True,
            reached=tuple(reached),
            blocking_stage=blocking_stage,
            blocking_reason=blocking_reason,
        )

    if state_root is not None:
        return run(state_root)
    with tempfile.TemporaryDirectory(prefix="cold-start-probe-") as root:
        return run(root)
