from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.backlog_evaluators import (
    EXECUTION_FAMILY,
    BacklogBurnDCFEvaluator,
    BacklogBurnRegistration,
    live_backlog_burn_registry_loader,
)
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    ValueKind,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.risk import BetaLevelName, BetaUpdate, HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.scenario_binding import BoundScenario
from valuation_engine.wacc import WACCResult


UNIT = "KRW_billion"
AS_OF = "2026-08-27"
RATE = Decimal("0.09")

BASE_INPUTS: dict[str, tuple[str, str]] = {
    "opening_backlog": ("1000", UNIT),
    "opening_revenue": ("480", UNIT),
    "new_orders_year_1": ("600", UNIT),
    "new_orders_year_2": ("600", UNIT),
    "new_orders_year_3": ("600", UNIT),
    "backlog_burn_rate_year_1": ("0.5", "ratio"),
    "backlog_burn_rate_year_2": ("0.5", "ratio"),
    "backlog_burn_rate_year_3": ("0.5", "ratio"),
    "operating_margin_year_1": ("0.20", "ratio"),
    "operating_margin_year_2": ("0.20", "ratio"),
    "operating_margin_year_3": ("0.20", "ratio"),
    "operating_tax_rate": ("0.22", "ratio"),
    "depreciation_rate_of_revenue": ("0.01", "ratio"),
    "maintenance_capex_rate_of_revenue": ("0.015", "ratio"),
    "incremental_working_capital_rate": ("0.05", "ratio"),
    "terminal_growth": ("0.02", "ratio"),
    "terminal_roic": ("0.15", "ratio"),
}


def _scenario(overrides: dict[str, tuple[str, str]] | None = None) -> BoundScenario:
    values = dict(BASE_INPUTS)
    values.update(overrides or {})
    assumptions = tuple(
        CompiledAssumption(
            key=key,
            scenario_id="Core",
            measure=Measure(Decimal(amount), unit, AS_OF),
            bridge_id=f"BR_{key}",
            evidence_ids=(f"E_{key}",),
            hypothesis_id="H1",
            economic_path_id=f"path:{key}",
            transform_id="identity_observation",
            input_evidence_hash="hash",
        )
        for key, (amount, unit) in values.items()
    )
    return BoundScenario("Core", assumptions, None)


def _evaluator(**kwargs) -> BacklogBurnDCFEvaluator:
    defaults = dict(
        archetype="contracted_backlog",
        method="backlog_burn_dcf",
        version="1",
        forecast_years=3,
        discount_rate=RATE,
        discount_rate_path_id="wacc:abc",
        beta_path_id="beta:def",
    )
    defaults.update(kwargs)
    return BacklogBurnDCFEvaluator(**defaults)


# ------------------------------------------------------------------ order-book identity


def test_revenue_is_drawn_from_opening_backlog():
    rows = _evaluator().backlog_path(_scenario())
    assert rows[0].opening_backlog == Decimal("1000")
    assert rows[0].revenue == Decimal("500")
    assert rows[1].revenue == Decimal("550")
    assert rows[2].revenue == Decimal("575")


def test_backlog_roll_forward_identity_holds_every_year():
    for row in _evaluator().backlog_path(_scenario()):
        assert row.closing_backlog == row.opening_backlog + row.new_orders - row.revenue


def test_each_year_opens_on_the_prior_closing_backlog():
    rows = _evaluator().backlog_path(_scenario())
    for previous, current in zip(rows, rows[1:]):
        assert current.opening_backlog == previous.closing_backlog


def test_burn_rate_above_one_is_rejected():
    """A year cannot recognise more revenue than the backlog standing at its start."""
    with pytest.raises(ValueError, match="cannot recognise more revenue"):
        _evaluator().evaluate(
            _scenario({"backlog_burn_rate_year_2": ("1.2", "ratio")}), segment_id="seg"
        )


def test_non_positive_burn_rate_is_rejected():
    with pytest.raises(ValueError):
        _evaluator().evaluate(
            _scenario({"backlog_burn_rate_year_1": ("0", "ratio")}), segment_id="seg"
        )


def test_negative_new_orders_are_rejected():
    with pytest.raises(ValueError, match="new orders cannot be negative"):
        _evaluator().evaluate(
            _scenario({"new_orders_year_1": ("-10", UNIT)}), segment_id="seg"
        )


def test_non_positive_opening_backlog_is_rejected():
    with pytest.raises(ValueError, match="opening backlog must be positive"):
        _evaluator().evaluate(_scenario({"opening_backlog": ("0", UNIT)}), segment_id="seg")


# ------------------------------------------------------------------------- cash bridge


def test_fcff_matches_the_declared_cash_bridge():
    rows = _evaluator().backlog_path(_scenario())
    first = rows[0]
    # 500 revenue x 20% margin -> 100 operating profit; NOPAT 78; +5 depreciation;
    # -7.5 maintenance capex; revenue rose 480 -> 500 so 20 x 5% = 1.0 working capital.
    assert first.operating_profit == Decimal("100.00")
    assert first.fcff == Decimal("74.5000")


def test_working_capital_is_charged_only_on_revenue_increases():
    flat = _scenario(
        {
            "opening_revenue": ("500", UNIT),
            "backlog_burn_rate_year_2": ("0.4", "ratio"),
        }
    )
    rows = _evaluator().backlog_path(flat)
    # Year 2 revenue (440) falls below year 1 (500), so no working capital is charged.
    revenue = rows[1].revenue
    nopat = revenue * Decimal("0.20") * Decimal("0.78")
    expected = nopat + revenue * Decimal("0.01") - revenue * Decimal("0.015")
    assert rows[1].fcff == expected


def test_tax_rate_at_or_above_one_is_rejected():
    with pytest.raises(ValueError, match="operating tax rate"):
        _evaluator().evaluate(
            _scenario({"operating_tax_rate": ("1.0", "ratio")}), segment_id="seg"
        )


def test_negative_cost_rates_are_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        _evaluator().evaluate(
            _scenario({"maintenance_capex_rate_of_revenue": ("-0.01", "ratio")}),
            segment_id="seg",
        )


def test_money_units_are_converted_not_assumed():
    scenario = _scenario({"new_orders_year_1": ("600000", "KRW_million")})
    rows = _evaluator().backlog_path(scenario)
    assert rows[0].new_orders == Decimal("600")


def test_non_money_backlog_is_rejected():
    with pytest.raises(ValueError, match="money measure"):
        _evaluator().evaluate(
            _scenario({"opening_backlog": ("1000", "ratio")}), segment_id="seg"
        )


# ------------------------------------------------------------------- terminal contract


def test_terminal_requires_a_self_sustaining_order_book():
    """Final-year book-to-bill below the floor contradicts perpetual growth."""
    with pytest.raises(ValueError, match="self-sustaining order book"):
        _evaluator().evaluate(
            _scenario({"new_orders_year_3": ("100", UNIT)}), segment_id="seg"
        )


def test_declared_floor_allows_a_depleting_order_book():
    evaluator = _evaluator(terminal_book_to_bill_floor=Decimal("0"))
    result = evaluator.evaluate(
        _scenario({"new_orders_year_3": ("100", UNIT)}), segment_id="seg"
    )
    assert result.value.amount > 0


def test_terminal_growth_at_or_above_discount_rate_is_rejected():
    with pytest.raises(ValueError, match="WACC must exceed terminal growth"):
        _evaluator().evaluate(
            _scenario({"terminal_growth": ("0.09", "ratio")}), segment_id="seg"
        )


def test_terminal_reinvestment_identity_is_enforced():
    with pytest.raises(ValueError, match="reinvestment"):
        _evaluator().evaluate(
            _scenario(
                {"terminal_growth": ("0.05", "ratio"), "terminal_roic": ("0.04", "ratio")}
            ),
            segment_id="seg",
        )


def test_non_positive_final_fcff_is_rejected():
    with pytest.raises(ValueError, match="positive final-year FCFF"):
        _evaluator().evaluate(
            _scenario({"operating_margin_year_3": ("-0.5", "ratio")}), segment_id="seg"
        )


# ---------------------------------------------------------------------------- valuation


def test_enterprise_value_is_the_discounted_path_plus_gordon_tail():
    result = _evaluator().evaluate(_scenario(), segment_id="seg")
    rows = _evaluator().backlog_path(_scenario())
    one = Decimal("1")
    explicit = sum(
        (row.fcff / (one + RATE) ** row.year for row in rows), Decimal("0")
    )
    terminal = (
        rows[-1].fcff
        * (one + Decimal("0.02"))
        / (RATE - Decimal("0.02"))
        / (one + RATE) ** 3
    )
    assert abs(result.value.amount - (explicit + terminal)) < Decimal("1e-20")
    assert result.value_kind is ValueKind.ENTERPRISE_VALUE


def test_diagnostics_are_published_for_downstream_consumers():
    result = _evaluator().evaluate(_scenario(), segment_id="seg")
    diagnostics = result.diagnostics
    assert diagnostics is not None
    assert diagnostics.execution_family == EXECUTION_FAMILY
    assert diagnostics.forecast_years == 3
    assert len(diagnostics.fcff_path) == 3
    assert diagnostics.value_unit == UNIT
    assert abs(diagnostics.enterprise_value - result.value.amount) < Decimal("1e-24")


def test_economic_paths_are_unique_and_carry_the_risk_chain():
    result = _evaluator().evaluate(_scenario(), segment_id="seg")
    paths = result.economic_path_ids
    assert len(paths) == len(set(paths))
    assert "wacc:abc:seg" in paths
    assert "beta:def:seg" in paths
    assert "path:opening_backlog" in paths


def test_required_assumption_keys_cover_the_whole_contract():
    keys = set(_evaluator().required_assumption_keys)
    assert keys == set(BASE_INPUTS)


def test_assumption_prefix_is_applied_to_every_key():
    keys = _evaluator(assumption_prefix="seg_").required_assumption_keys
    assert all(key.startswith("seg_") for key in keys)


# -------------------------------------------------------------------------- registration


def test_registration_validates_its_bounds():
    with pytest.raises(ValueError):
        BacklogBurnRegistration("a", "b", "1", 0).validate()
    with pytest.raises(ValueError):
        BacklogBurnRegistration("a", "b", "1", 3, terminal_book_to_bill_floor=Decimal("3")).validate()
    with pytest.raises(ValueError):
        BacklogBurnRegistration("a", "b", "1", 3, assumption_prefix="has space").validate()


# ------------------------------------------------------------------------------- loader


def _wacc_result() -> LiveWACCStageResult:
    structure = LiveCapitalStructureObservation(
        equity_weight=0.8,
        debt_weight=0.2,
        tax_rate=0.22,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of=AS_OF,
        source_refs=("https://example.test/structure",),
        rationale="test",
    )
    update = BetaUpdate(
        level=BetaLevelName.L1_BROAD_SECTOR,
        sample_size=2,
        group_mean_asset_beta=0.8,
        group_dispersion_variance=0.01,
        measurement_variance=0.0,
        likelihood_variance=0.01,
        prior_mean=0.8,
        prior_variance=0.01,
        posterior_mean=0.8,
        posterior_variance=0.01,
    )
    beta = LiveBetaStageResult(
        estimate=HierarchicalBetaEstimate(0.8, 0.01, (update,)),
        target_asset_beta=0.8,
        target_levered_beta=0.95,
        target_capital_structure=structure,
        peer_ids=("PEER",),
        source_refs=("https://example.test/beta",),
        selection_evidence_ids=("E1",),
        snapshot_hash="betahash",
    )
    return LiveWACCStageResult(
        beta_result=beta,
        wacc_result=WACCResult(0.10, 0.03, 0.8, 0.2, float(RATE)),
        terminal_consistency=None,
        source_refs=("https://example.test/wacc",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash="wacchash",
    )


def _context(data: dict) -> OrchestratorContext:
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data, [], None)


def _registration() -> BacklogBurnRegistration:
    return BacklogBurnRegistration("contracted_backlog", "backlog_burn_dcf", "1", 3)


def test_loader_builds_an_evaluator_bound_to_the_live_discount_rate():
    loader = live_backlog_burn_registry_loader(registrations=(_registration(),))
    registry = loader(_context({"live_wacc_result": _wacc_result()}))
    evaluator = registry.get(ModelKey("contracted_backlog", "backlog_burn_dcf", "1"))
    assert evaluator.discount_rate == RATE
    assert evaluator.discount_rate_path_id == "wacc:wacchash"


def test_loader_requires_live_wacc():
    loader = live_backlog_burn_registry_loader(registrations=(_registration(),))
    with pytest.raises(ValueError, match="LiveWACCStageResult"):
        loader(_context({}))


def test_loader_rejects_pre_freeze_market_leakage():
    loader = live_backlog_burn_registry_loader(registrations=(_registration(),))
    with pytest.raises(PermissionError, match="Street/market"):
        loader(
            _context({"live_wacc_result": _wacc_result(), "current_market_price": 1000})
        )


def test_loader_rejects_duplicate_model_keys():
    with pytest.raises(ValueError, match="duplicate"):
        live_backlog_burn_registry_loader(
            registrations=(_registration(), _registration())
        )


def test_loader_rejects_a_binding_outside_this_execution_family():
    with pytest.raises(ValueError):
        live_backlog_burn_registry_loader(
            registrations=(
                BacklogBurnRegistration("contracted_backlog", "normalized_dcf", "1", 3),
            )
        )


def test_loader_requires_registrations():
    with pytest.raises(ValueError):
        live_backlog_burn_registry_loader(registrations=())


def test_loader_composes_on_top_of_a_base_registry():
    def base(_context) -> EvaluatorRegistry:
        registry = EvaluatorRegistry()
        registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
        return registry

    loader = live_backlog_burn_registry_loader(
        registrations=(_registration(),), base_loader=base
    )
    registry = loader(_context({"live_wacc_result": _wacc_result()}))
    assert registry.get(ModelKey("commodity_price_taker", "normalized_multiple", "1"))
    assert registry.get(ModelKey("contracted_backlog", "backlog_burn_dcf", "1"))
