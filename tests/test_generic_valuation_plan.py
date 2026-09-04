"""Plan inputs and evaluator registry compose from context, not from a company file."""

from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.control_plane import ExecutionMode
from valuation_engine.generic_valuation_plan import (
    GenericValuationPlanError,
    composed_generic_registry_loader,
    conventional_valuation_plan_inputs_loader,
)
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.live_primary_adapters import SegmentDescriptor
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.risk_adapters import LiveWACCStageResult
from valuation_engine.valuation_execution import ParentAdjustmentPlan
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice
from valuation_engine.wacc import WACCResult


def _segment(segment_id: str = "core") -> SegmentDescriptor:
    return SegmentDescriptor(
        segment_id=segment_id, name="Core", revenue_recognition="delivery",
        price_formation="contracted", asset_ownership="owned",
        capital_intensity="high", regulation_intensity="medium",
        customer_structure="industrial", reinvestment_model="capacity",
        cashflow_duration="cycle", evidence_ids=("E1",),
    )


def _context(data: dict) -> OrchestratorContext:
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data, [], None)


def test_plan_inputs_bind_every_segment_under_the_conventions():
    loader = conventional_valuation_plan_inputs_loader(reporting_unit="KRW")
    inputs = loader(_context({"segment_descriptors": (_segment(), _segment("units"))}))
    assert inputs.reporting_unit == "KRW"
    assert inputs.diluted_shares_key == "diluted_shares"
    assert {item.segment_id for item in inputs.segment_bindings} == {"core", "units"}
    for item in inputs.segment_bindings:
        assert item.ownership_key == "ownership"
        assert item.ev_to_equity_adjustment_key == "ev_adjustment"


def test_plan_inputs_fail_without_segments():
    loader = conventional_valuation_plan_inputs_loader(reporting_unit="KRW")
    with pytest.raises(GenericValuationPlanError, match="segment descriptors"):
        loader(_context({}))


def test_plan_inputs_preserve_parent_adjustments_outside_segment_ownership():
    adjustment = ParentAdjustmentPlan(
        asset_id="parent_noncontrolling_interest",
        assumption_key="parent_noncontrolling_interest_adjustment",
    )
    loader = conventional_valuation_plan_inputs_loader(
        reporting_unit="KRW_billion",
        parent_adjustments=(adjustment,),
    )

    inputs = loader(_context({"segment_descriptors": (_segment(),)}))

    assert inputs.parent_adjustments == (adjustment,)
    assert inputs.segment_bindings[0].ownership_key == "ownership"


def test_registry_composes_a_wacc_free_family_without_wacc():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice("core", "commodity_price_taker", "normalized_multiple"),
        ),
        forecast_years=5,
    )
    registry = loader(_context({}))
    evaluator = registry.get(ModelKey("commodity_price_taker", "normalized_multiple", "1"))
    assert evaluator.required_assumption_keys == ("normalized_ebitda", "normalized_multiple")


def test_registry_requires_live_wacc_for_a_dcf_family():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice("core", "capacity_manufacturing", "driver_dcf"),
        ),
        forecast_years=5,
    )
    with pytest.raises(GenericValuationPlanError, match="LiveWACCStageResult"):
        loader(_context({}))


def test_registry_builds_dcf_from_the_runs_own_wacc():
    wacc = LiveWACCStageResult(
        beta_result=None,
        wacc_result=WACCResult(
            cost_of_equity=0.12, after_tax_cost_of_debt=0.04,
            equity_weight=0.7, debt_weight=0.3, wacc=0.096,
        ),
        terminal_consistency=None,
        source_refs=("https://example.test/rates",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash="hash",
    )
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice("core", "capacity_manufacturing", "driver_dcf"),
        ),
        forecast_years=5,
    )
    registry = loader(_context({"live_wacc_result": wacc}))
    evaluator = registry.get(ModelKey("capacity_manufacturing", "driver_dcf", "1"))
    assert evaluator.discount_rate == Decimal("0.096")


def test_an_unsupported_family_fails_closed_at_composition_time():
    with pytest.raises(GenericValuationPlanError, match="not.*supported|supported"):
        composed_generic_registry_loader(
            method_choices=(
                SegmentMethodChoice("core", "probabilistic_pipeline", "rnpv"),
            ),
            forecast_years=5,
        )


def _live_wacc(wacc: float = 0.08) -> LiveWACCStageResult:
    from valuation_engine.risk_adapters import (
        LiveBetaStageResult,
        LiveCapitalStructureObservation,
        TargetCapitalStructureMethod,
    )
    from valuation_engine.risk import HierarchicalBetaEstimate

    structure = LiveCapitalStructureObservation(
        equity_weight=0.7, debt_weight=0.3, tax_rate=0.24,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of="2026-06-30", source_refs=("https://x/structure",),
        rationale="test structure",
    )
    beta = LiveBetaStageResult(
        estimate=HierarchicalBetaEstimate(
            asset_beta=0.8, posterior_variance=0.01, updates=(),
        ),
        target_asset_beta=0.8, target_levered_beta=1.0,
        target_capital_structure=structure, peer_ids=("P1",),
        source_refs=("https://x/beta",), selection_evidence_ids=("E1",),
        snapshot_hash="beta-hash",
    )
    return LiveWACCStageResult(
        beta_result=beta,
        wacc_result=WACCResult(
            cost_of_equity=0.10, after_tax_cost_of_debt=0.035,
            equity_weight=0.7, debt_weight=0.3, wacc=wacc,
        ),
        terminal_consistency=None,
        source_refs=("https://x/rates",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash="wacc-hash",
    )


@pytest.mark.parametrize(
    "archetype, method, family, needs_wacc",
    [
        ("regulated_rate_base", "ddm", "gordon_ddm", True),
        ("regulated_rate_base", "rate_base_roe", "rate_base_roe", True),
        ("asset_yield_nav", "ffo_multiple", "ffo_multiple", False),
        ("asset_yield_nav", "nav", "net_asset_value", False),
        ("financial_balance_sheet", "pb_roe", "justified_pb_roe", True),
        ("financial_balance_sheet", "residual_income", "residual_income", True),
        ("contracted_backlog", "normalized_ebitda", "normalized_ebitda_multiple", False),
        ("reserve_depletion", "reserve_npv", "finite_life_npv", True),
    ],
)
def test_every_delegated_family_composes_from_registry_pairs(
    archetype, method, family, needs_wacc
):
    """The composer now delegates the equity/NAV and finite-life families to
    their existing exact loaders — each real (archetype, method) pair from the
    capability registry must build its evaluator, and the discounted ones must
    consume the run's own WACC result rather than any composer-held rate."""
    loader = composed_generic_registry_loader(
        method_choices=(SegmentMethodChoice("core", archetype, method),),
        forecast_years=3,
    )
    data = {"live_wacc_result": _live_wacc()} if needs_wacc else {}
    registry = loader(_context(data))
    evaluator = registry.get(ModelKey(archetype, method, "1"))
    assert evaluator.required_assumption_keys
    if needs_wacc and hasattr(evaluator, "cost_of_equity"):
        assert evaluator.cost_of_equity == Decimal("0.1")
    # Keys the run demands come from the same prototype the evaluator declares.
    from valuation_engine.generic_live_providers import required_assumption_keys

    keys = required_assumption_keys(
        method_choices=(SegmentMethodChoice("core", archetype, method),),
        forecast_years=3,
    )
    for key in evaluator.required_assumption_keys:
        assert key in keys


def test_rnpv_stays_an_explicit_gap_naming_the_family():
    with pytest.raises(GenericValuationPlanError, match="calibrated_single_event_rnpv"):
        composed_generic_registry_loader(
            method_choices=(
                SegmentMethodChoice("core", "probabilistic_pipeline", "rnpv"),
            ),
            forecast_years=3,
        )


def test_registry_scopes_duplicate_exact_model_key_by_segment():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice(
                "trading", "process_spread", "normalized_multiple"
            ),
            SegmentMethodChoice(
                "recycling", "process_spread", "normalized_multiple"
            ),
        ),
        forecast_years=5,
    )
    registry = loader(_context({}))
    key = ModelKey("process_spread", "normalized_multiple", "1")
    assert registry.get(key, segment_id="trading").required_assumption_keys == (
        "trading_normalized_ebitda",
        "trading_normalized_multiple",
    )
    assert registry.get(key, segment_id="recycling").required_assumption_keys == (
        "recycling_normalized_ebitda",
        "recycling_normalized_multiple",
    )
    assert registry.keys_for_segment("trading") == (key,)
    assert registry.keys_for_segment("recycling") == (key,)
    assert registry.keys_for_segment("manufacturing") == ()
    with pytest.raises(KeyError, match="no exact evaluator"):
        registry.get(key)


def test_unique_model_key_keeps_historical_global_registry_contract():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice(
                "core", "process_spread", "normalized_multiple"
            ),
        ),
        forecast_years=5,
    )
    registry = loader(_context({}))
    key = ModelKey("process_spread", "normalized_multiple", "1")
    assert not registry.has_scoped_registrations()
    assert registry.get(key).required_assumption_keys == (
        "normalized_ebitda",
        "normalized_multiple",
    )


def test_registry_scopes_duplicate_equity_model_key_by_segment():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice("office", "asset_yield_nav", "ffo_multiple"),
            SegmentMethodChoice("retail", "asset_yield_nav", "ffo_multiple"),
        ),
        forecast_years=3,
    )
    registry = loader(_context({}))
    key = ModelKey("asset_yield_nav", "ffo_multiple", "1")
    assert registry.get(key, segment_id="office").required_assumption_keys == (
        "office_normalized_forward_ffo",
        "office_ffo_multiple",
    )
    assert registry.get(key, segment_id="retail").required_assumption_keys == (
        "retail_normalized_forward_ffo",
        "retail_ffo_multiple",
    )
    with pytest.raises(KeyError, match="no exact evaluator"):
        registry.get(key)


def test_registry_scopes_duplicate_finite_life_model_key_by_segment():
    loader = composed_generic_registry_loader(
        method_choices=(
            SegmentMethodChoice("mine_a", "reserve_depletion", "reserve_npv"),
            SegmentMethodChoice("mine_b", "reserve_depletion", "reserve_npv"),
        ),
        forecast_years=3,
    )
    registry = loader(_context({"live_wacc_result": _live_wacc()}))
    key = ModelKey("reserve_depletion", "reserve_npv", "1")
    assert registry.get(key, segment_id="mine_a").required_assumption_keys == (
        "mine_a_cashflow_year_0",
        "mine_a_cashflow_year_1",
        "mine_a_cashflow_year_2",
        "mine_a_cashflow_year_3",
    )
    assert registry.get(key, segment_id="mine_b").required_assumption_keys == (
        "mine_b_cashflow_year_0",
        "mine_b_cashflow_year_1",
        "mine_b_cashflow_year_2",
        "mine_b_cashflow_year_3",
    )
    with pytest.raises(KeyError, match="no exact evaluator"):
        registry.get(key)
