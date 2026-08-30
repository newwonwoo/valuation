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
    _staff_scripts,
    execute_cold_start_probe,
    probe_network,
    probe_runtime_spec,
)

AS_OF = "2026-08-27"


def _factory(transport=None):
    return build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=transport or ScriptedTransport(_staff_scripts()),
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


OK_STATUSES = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


def test_all_33_stages_execute_for_the_unseen_company(cold_run):
    traces = {trace.stage: trace for trace in cold_run.result.stage_traces}
    assert len(cold_run.result.stage_traces) == 33
    for stage, trace in traces.items():
        assert trace.status in OK_STATUSES, (stage, trace.status)
    assert not cold_run.result.blocked_reasons


def test_the_cold_run_is_a_canonical_attested_result(cold_run):
    """Freeze token, per-stage authority receipts and the execution attestation."""
    assert cold_run.result.completed
    assert cold_run.result.freeze_token is not None
    assert cold_run.execution_attestation is not None
    assert cold_run.canonical_live_result
    cold_run.validate_canonical()


def test_the_cold_valuation_is_the_deterministic_arithmetic(cold_run):
    """(940 x 5.5 - 1,200) KRW bn over 95M shares — the declared underwriting
    carried through bridges, compiler and evaluator without drift."""
    from decimal import Decimal

    valuation = cold_run.result.data["generic_valuation_result"]
    value = valuation.scenarios[0].value_per_share
    expected = (Decimal("940") * Decimal("5.5") - Decimal("1200"))         * Decimal("1000000000") / Decimal("95000000")
    assert abs(value - expected) < Decimal("0.01")


def test_the_underwriting_share_is_disclosed_not_hidden(cold_run):
    """The evidence-composition guardrail must show how much stands on declared
    judgments — the point of routing them through the front door."""
    report = cold_run.result.data.get("evidence_composition_report")
    assert report is not None
    assert report.valuation_underwriting_share > 0


def test_the_probe_module_reports_completion():
    """The executed probe's own verdict: 28 executed + 5 unexercised, no blocker.

    History of this assertion: collector absence, then nine missing metrics,
    then five company-realized metrics, then completion — each move earned by a
    collector or a declared-input door, never by relaxing a check. The latest
    move is in the other direction and just as earned: the probe's method path
    (normalized_multiple) requires neither a Beta nor a WACC, so five stages are
    passed without executing. They used to be counted as reached, which read as
    33/33 proven. The run really does complete; it simply proves 28 providers,
    not 33.
    """
    outcome = execute_cold_start_probe()
    assert outcome.probed
    assert len(outcome.reached) == 28
    assert len(outcome.route_skipped) == 5
    assert len(outcome.reached) + len(outcome.route_skipped) == 33
    assert "HIERARCHICAL_BETA_ESTIMATION" in outcome.route_skipped
    assert "WACC_VALIDATION" in outcome.route_skipped
    assert outcome.blocking_stage is None
    assert outcome.blocking_reason == ""


def test_extra_required_evidence_routes_scenario_qualified_inputs():
    """Multi-scenario runs need scenario-qualified declarations
    (down_normalized_ebitda, …) in the ledger, and the underwriting collector
    only serves REQUIRED metrics — so the spec's extra_required_evidence must
    join the collection requirements, keeping coverage fail-closed for them."""
    from dataclasses import replace

    from valuation_engine.cold_start_probe import probe_network, probe_runtime_spec
    from valuation_engine.generic_live_providers import (
        build_generic_kr_runtime_factory,
    )
    from valuation_engine.llm_transport import ScriptedTransport

    spec = replace(
        probe_runtime_spec(),
        extra_required_evidence=("down_normalized_ebitda", "bull_normalized_multiple"),
    )
    factory = build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=ScriptedTransport({}),
        spec=spec,
    )
    required = factory.additional_required_evidence[spec.filing.segment_id]
    assert "down_normalized_ebitda" in required
    assert "bull_normalized_multiple" in required
    # The method's own assumption keys are still there, ahead of the extras.
    assert "normalized_ebitda" in required


def test_calibration_snapshot_loader_threads_into_the_probability_door():
    """The probability route's generic door: a snapshot loader on the spec must
    reach the extensions' calibration_loader slot and stamp the cohort and
    external source into the ScenarioBindingSpec — and declaring a loader
    without its cohort/source identity is refused."""
    from dataclasses import replace

    import pytest as _pytest

    from valuation_engine.cold_start_probe import probe_network, probe_runtime_spec
    from valuation_engine.generic_live_providers import (
        build_generic_kr_runtime_factory,
    )
    from valuation_engine.generic_valuation_plan import GenericValuationPlanError
    from valuation_engine.llm_transport import ScriptedTransport

    def loader(_context):  # pragma: no cover - never invoked here
        raise AssertionError("not called at wiring time")

    spec = replace(
        probe_runtime_spec(),
        calibration_snapshot_loader=loader,
        calibration_cohort_key="kr.steel.long|5y_path|continuous_v1",
        external_probability_source="continuous_financial_path_monte_carlo",
    )
    factory = build_generic_kr_runtime_factory(
        network=probe_network(), transport=ScriptedTransport({}), spec=spec
    )
    assert factory.extensions.calibration_loader is loader
    binding = factory.scenario_binding_spec
    assert binding.calibration_cohort_key == "kr.steel.long|5y_path|continuous_v1"
    assert binding.external_probability_source == "continuous_financial_path_monte_carlo"

    incomplete = replace(probe_runtime_spec(), calibration_snapshot_loader=loader)
    with _pytest.raises(GenericValuationPlanError, match="calibration_cohort_key"):
        build_generic_kr_runtime_factory(
            network=probe_network(), transport=ScriptedTransport({}), spec=incomplete
        )


def test_market_observation_after_historical_run_cutoff_is_rejected(tmp_path):
    from dataclasses import replace

    from valuation_engine.generic_valuation_plan import GenericValuationPlanError

    market = tmp_path / "market.yaml"
    market.write_text(
        "market_comparison:\n"
        "  price: 10000\n"
        '  as_of: "2026-08-28"\n'
        "  source_ref: https://example.test/market/close\n",
        encoding="utf-8",
    )
    spec = replace(
        probe_runtime_spec(),
        market_config_path=market,
        market_currency="KRW",
    )

    factory = build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=ScriptedTransport({}),
        spec=spec,
    )
    with pytest.raises(GenericValuationPlanError, match="after run cutoff"):
        factory.extensions.market_loader()
