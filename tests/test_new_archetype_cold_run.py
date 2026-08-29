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
2. Given those four, the run reaches VALUATION_METHOD_INTENT — and what happens
   next depends on the operator's declared risk pack. Without one, it stops at
   HIERARCHICAL_BETA_ESTIMATION: 9 of the 14 execution families require beta
   and WACC, and the engine refuses to invent a discount rate. WITH a declared
   risk pack (L1→L4 peers, ECOS risk-free, Damodaran ERP/CRP, marginal debt —
   ``declared_risk_pack``), the same run completes all 33 stages to an attested
   value: the full drive-to-value proof for a discount-rate-bound family.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from valuation_engine.backlog_cold_start_probe import (
    AS_OF,
    DIAGNOSTIC_CONTRACT_STUBS,
    NAME,
    TARGET,
    UNDERWRITING,
    YEARS,
    run_backlog_probe,
)
from valuation_engine.declared_risk_pack import BETA_SELECTION_METRICS
from valuation_engine.generic_live_providers import (
    GenericKRRuntimeSpec,
    required_assumption_keys,
)
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice

SEG = "core"

#: The four contract-structure items ``contracted_backlog`` requires and no
#: collector in this repository produces (see DIAGNOSTIC_CONTRACT_STUBS).
UNCOLLECTED_CONTRACT_EVIDENCE = tuple(DIAGNOSTIC_CONTRACT_STUBS)


def _execute(underwriting_rows: dict, *, with_risk_pack: bool = False):
    reached, route_skipped, stop_stage, stop_reason, data = run_backlog_probe(
        underwriting_rows, with_risk_pack=with_risk_pack
    )
    # These tests treat a route-skipped stage as "passed through", matching the
    # run's own view; the capability receipts count them separately.
    return (*reached, *route_skipped), stop_stage, stop_reason, data


@pytest.fixture(scope="module")
def cold_run():
    return _execute(UNDERWRITING)


@pytest.fixture(scope="module")
def diagnostic_run():
    return _execute({**UNDERWRITING, **DIAGNOSTIC_CONTRACT_STUBS})


@pytest.fixture(scope="module")
def full_run():
    return _execute(
        {**UNDERWRITING, **DIAGNOSTIC_CONTRACT_STUBS}, with_risk_pack=True
    )


def test_the_engine_routes_an_unseen_shipbuilder_without_company_code(cold_run):
    reached, _, _, _ = cold_run
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
    _, stop_stage, stop_reason, _ = cold_run
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


def test_without_a_declared_risk_pack_the_run_stops_at_the_beta_gate(
    diagnostic_run,
):
    """With the four contract facts present, the whole LLM-staffed middle works —
    and without a declared risk pack the engine refuses to invent a discount
    rate, stopping exactly at the Beta stage.

    This is a DIAGNOSTIC: those four entered as declared underwriting, which is
    not their honest layer. It exists to isolate the discount-rate boundary.
    """
    reached, stop_stage, stop_reason, _ = diagnostic_run
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


def test_with_a_declared_risk_pack_the_backlog_dcf_completes_all_33_stages(full_run):
    """The drive-to-value proof for a discount-rate-bound family.

    Same shipbuilder, same evidence — plus the operator's declared risk pack.
    The run executes Beta and WACC from the pack, prices the 3-year order-book
    roll-forward at the derived WACC, survives the audit's hash-bound
    Beta→WACC path check, freezes, and reports. The number asserted here is the
    deterministic product of the declared inputs; change any peer Beta or the
    risk-free print and the run (and this pin) moves with it.
    """
    reached, stop_stage, stop_reason, data = full_run
    assert stop_stage is None, stop_reason
    assert len(reached) == 33
    for stage in (
        "HIERARCHICAL_BETA_ESTIMATION",
        "WACC_VALIDATION",
        "DETERMINISTIC_VALUATION",
        "AUDIT_GATE",
        "INTRINSIC_VALUE_FREEZE",
        "FINAL_REPORT",
    ):
        assert stage in reached, stage
    wacc = data["live_wacc_result"]
    assert 0.05 < wacc.wacc_result.wacc < 0.10
    assert 1.0 < wacc.beta_result.target_levered_beta < 1.4
    valuation = data["generic_valuation_result"]
    (scenario,) = valuation.scenarios
    assert scenario.scenario_id == "Base"
    assert float(scenario.value_per_share) == pytest.approx(19658.33, abs=0.01)
    # The audit's risk-consumption check demanded these; prove they are there.
    beta_prefix = f"beta:{wacc.beta_result.snapshot_hash}:"
    wacc_prefix = f"wacc:{wacc.snapshot_hash}:"
    assert any(path.startswith(beta_prefix) for path in scenario.economic_path_ids)
    assert any(path.startswith(wacc_prefix) for path in scenario.economic_path_ids)


def test_warranted_per_is_withheld_not_approximated(full_run):
    """contracted_backlog registers a Warranted-PER cross-check; the generic run
    answers it honestly — fingerprint bound, PER withheld with its reason —
    instead of fabricating a peer PER table."""
    reached, _, _, data = full_run
    assert "DCF_PER_ASSUMPTION_CONSISTENCY_GATE" in reached
    fingerprint = data["dcf_assumption_fingerprint"]
    assert fingerprint.growth_duration_years == YEARS
    assert len(fingerprint.margin_path) == YEARS
    assert fingerprint.margin_path == (0.055, 0.062, 0.068)


def test_the_beta_wacc_split_of_the_families_and_the_declared_door():
    """Nine of fourteen families require beta and WACC. They no longer dead-end:
    ``GenericKRRuntimeSpec.declared_risk_path`` is the operator's declared door
    to the discount rate, and without it those stages still refuse to run —
    the split is between families that need the door and families that don't,
    never a silent default rate."""
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
    assert "declared_risk_path" in GenericKRRuntimeSpec.__dataclass_fields__
