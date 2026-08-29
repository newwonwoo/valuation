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


def probe_fetch_text(url: str) -> str:
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


def probe_fetch_bytes(url: str) -> bytes:
    if "corpCode.xml" in url:
        return _corp_archive()
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
