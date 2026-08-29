"""The cold start: an unseen company through the real canonical runtime.

한빛제강 exists nowhere in this repository — no module, no spec row, no fixture
company file. Its identity, filings and classification are served by a stub
OpenDART network built inside this test. Everything else is the engine.

What these tests prove, and equally what they refuse to overstate:

- the full provider set assembles and the canonical LIVE_PRIMARY config
  validates with zero company-bound code;
- the attested runtime executes the early pipeline for that company and then
  fails closed at PRIMARY_EVIDENCE_COLLECTION, naming exactly the archetype
  evidence (realized_price, production, cash_cost, ...) that the core DART
  financial-fact collector cannot supply.

That block is the honest current boundary of the cold start: the run starts,
routes, plans — and stops where source breadth actually ends, with the engine's
own words, not a hand-written status.
"""

from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.control_plane import StageStatus
from valuation_engine.generic_live_providers import (
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
    required_assumption_keys,
)
from valuation_engine.kr_opendart_provider import OpenDartFilingSelection, OpenDartNetwork
from valuation_engine.llm_transport import ScriptedTransport
from valuation_engine.strict_live_runtime import run_prism
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice


from valuation_engine.cold_start_probe import (
    PROBE_COMPANY_NAME as NAME,
    PROBE_CORP_CODE as CORP,
    PROBE_STOCK_CODE as STOCK,
    execute_cold_start_probe,
    probe_network,
    probe_runtime_spec,
)

AS_OF = "2026-08-27"


def _factory(transport=None):
    return build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=transport or ScriptedTransport({}),
        spec=probe_runtime_spec(),
    )


def _request(state_root) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command=f"분석시작 {NAME}",
        company_query=NAME,
        state_root=state_root,
        run_id="COLDSTART-TEST",
        jurisdiction="KR",
    )


# ------------------------------------------------------------------- assembly


def test_the_full_provider_set_assembles_for_an_unseen_company(tmp_path):
    config = _factory()(_request(tmp_path))
    config.validate()
    providers = config.providers
    # Every required seat is filled — this is what probe_cold_start asks for.
    for slot in (
        "company_resolver", "industry_snapshot_loader", "freshness_loader",
        "segment_decomposer", "industry_dna_router", "intelligence_officer",
        "red_team_officer", "bridge_analyst", "evaluator_registry_loader",
        "valuation_plan_inputs_loader",
    ):
        assert getattr(providers, slot) is not None, slot
    assert providers.collectors
    assert providers.scanner_runners


def test_generic_modules_never_import_a_company_bound_module():
    """Source-level check: the generic path carries no company import.

    (sys.modules cannot be used here — other tests in the same session import
    the company modules legitimately.)
    """
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "valuation_engine"
    bound = ("sanil_live_primary", "skhynix_live_primary",
             "skhynix_continuous_live_primary", "skhynix_continuous_probability",
             "skhynix_public_report", "required_company_live")
    for module in ("generic_live_providers", "generic_kr_industry",
                   "generic_llm_staff", "generic_scanners",
                   "generic_valuation_plan", "cold_start_probe", "llm_transport"):
        text = (package / f"{module}.py").read_text(encoding="utf-8")
        for name in bound:
            assert name not in text, f"{module}.py references {name}"


def test_the_scenario_binding_keys_follow_the_method_choice():
    keys = required_assumption_keys(
        method_choices=probe_runtime_spec().method_choices, forecast_years=5
    )
    assert "normalized_ebitda" in keys
    assert "normalized_multiple" in keys
    assert {"ownership", "ev_adjustment", "diluted_shares"}.issubset(keys)


def test_resolution_works_from_the_company_name_alone(tmp_path):
    config = _factory()(_request(tmp_path))
    identity = config.providers.company_resolver(config.company_request)
    assert identity.target_id == f"KR:DART:{CORP}"
    assert identity.ticker == STOCK


# ------------------------------------------------------------------- cold run


@pytest.fixture(scope="module")
def cold_run(tmp_path_factory):
    state_root = tmp_path_factory.mktemp("coldstate")
    config = _factory()(_request(state_root))
    return run_prism(config)


EXPECTED_REACHED = (
    "COMPANY_RESOLUTION",
    "LOAD_COMPANY_STATE",
    "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT",
    "SOURCE_FRESHNESS_PRECHECK",
    "SEGMENT_DECOMPOSITION",
    "INDUSTRY_DNA_ROUTE",
    "MODULE_REQUIREMENT_PLAN",
)


def test_the_canonical_runtime_executes_the_early_pipeline(cold_run):
    traces = {trace.stage: trace for trace in cold_run.result.stage_traces}
    for stage in EXPECTED_REACHED:
        assert stage in traces, f"{stage} never executed"
        assert traces[stage].status in {StageStatus.PASS, StageStatus.WARNING}, (
            stage,
            traces[stage].status,
        )


def test_the_run_fails_closed_exactly_at_evidence_breadth(cold_run):
    traces = {trace.stage: trace for trace in cold_run.result.stage_traces}
    collection = traces.get("PRIMARY_EVIDENCE_COLLECTION")
    assert collection is not None
    assert collection.status is StageStatus.RECOVERY_REQUIRED
    assert cold_run.result.blocked_reasons
    reason = " ".join(cold_run.result.blocked_reasons)
    # The engine names the archetype evidence the core collector cannot supply.
    assert "PRIMARY_EVIDENCE_COLLECTION" in reason or "evidence" in reason.lower()


def test_a_blocked_cold_run_publishes_no_intrinsic_value(cold_run):
    assert cold_run.result.freeze_token is None
    assert cold_run.execution_attestation is None
    assert not cold_run.canonical_live_result


def test_the_probe_module_reports_the_same_boundary():
    """The boundary is now evidence breadth by name, no longer collector absence.

    The filing-KPI collector extracts production/capacity/utilization from the
    statutory tables; what remains missing is exactly the market-side evidence
    (realized/benchmark prices, cash cost, inventory) that needs the industry
    source indexers — and the engine names every one of those metrics itself.
    """
    outcome = execute_cold_start_probe()
    assert outcome.probed
    assert outcome.reached == EXPECTED_REACHED
    assert outcome.blocking_stage == "PRIMARY_EVIDENCE_COLLECTION"
    assert "required primary evidence missing" in outcome.blocking_reason
    for metric in ("realized_price", "benchmark_price", "cash_cost", "inventory"):
        assert metric in outcome.blocking_reason
    # Collector absence is no longer the story.
    assert "no runnable collector" not in outcome.blocking_reason
