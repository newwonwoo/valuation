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
