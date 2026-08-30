"""End-to-end proof that driving revenue from backlog changes what the model consumes.

Under ``explicit_fcff_dcf`` the disclosed order book sits in the ledger while the
model consumes an analyst FCFF path, so the evidence-composition guardrail sees a
zero filing-cited share. Under ``contracted_backlog_dcf`` the same disclosures
become valuation inputs. These tests value one company both ways and compare.

They also confirm the published-diagnostics contract holds for the new family, so
post-freeze reverse DCF and the sensitivity guardrail work on backlog segments
with no extra wiring.
"""

from __future__ import annotations

from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.backlog_evaluators import BacklogBurnDCFEvaluator
from valuation_engine.dcf_evaluators import ExplicitFCFFDCFEvaluator
from valuation_engine.evaluator_registry import EvaluatorRegistry, ModelKey
from valuation_engine.evidence_composition import build_evidence_composition_report
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer, MarketObservation
from valuation_engine.reverse_dcf import build_reverse_dcf_result
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    SegmentValuationPlan,
    execute_company_valuation,
)
from valuation_engine.valuation_sensitivity import build_valuation_sensitivity_report
from valuation_engine.records import CalibrationStatus


UNIT = "KRW_billion"
AS_OF = "2026-08-27"
RATE = Decimal("0.09")
TARGET = "KR:DART:00366438"

FILING = EvidenceSourceLayer.REALIZED_OR_FILING
UNDERWRITING = EvidenceSourceLayer.ANALYST_UNDERWRITING

# Disclosed order book drives revenue; judgement supplies conversion rates only.
BACKLOG_INPUTS: dict[str, tuple[str, str, EvidenceSourceLayer]] = {
    "opening_backlog": ("1000", UNIT, FILING),
    "opening_revenue": ("480", UNIT, FILING),
    "new_orders_year_1": ("600", UNIT, FILING),
    "new_orders_year_2": ("600", UNIT, FILING),
    "new_orders_year_3": ("600", UNIT, FILING),
    "backlog_burn_rate_year_1": ("0.5", "ratio", UNDERWRITING),
    "backlog_burn_rate_year_2": ("0.5", "ratio", UNDERWRITING),
    "backlog_burn_rate_year_3": ("0.5", "ratio", UNDERWRITING),
    "operating_margin_year_1": ("0.20", "ratio", UNDERWRITING),
    "operating_margin_year_2": ("0.20", "ratio", UNDERWRITING),
    "operating_margin_year_3": ("0.20", "ratio", UNDERWRITING),
    "operating_tax_rate": ("0.22", "ratio", UNDERWRITING),
    "depreciation_rate_of_revenue": ("0.01", "ratio", UNDERWRITING),
    "maintenance_capex_rate_of_revenue": ("0.015", "ratio", UNDERWRITING),
    "incremental_working_capital_rate": ("0.05", "ratio", UNDERWRITING),
    "terminal_growth": ("0.02", "ratio", UNDERWRITING),
    "terminal_roic": ("0.15", "ratio", UNDERWRITING),
}

# Same company, same disclosures available, but the model consumes a finished path.
EXPLICIT_INPUTS: dict[str, tuple[str, str, EvidenceSourceLayer]] = {
    "fcff_year_1": ("74.5", UNIT, UNDERWRITING),
    "fcff_year_2": ("80.55", UNIT, UNDERWRITING),
    "fcff_year_3": ("85.575", UNIT, UNDERWRITING),
    "terminal_growth": ("0.02", "ratio", UNDERWRITING),
    "terminal_roic": ("0.15", "ratio", UNDERWRITING),
}

COMMON_INPUTS: dict[str, tuple[str, str, EvidenceSourceLayer]] = {
    "ownership": ("1.0", "ratio", UNDERWRITING),
    "ev_adjustment": ("50", UNIT, FILING),
    "diluted_shares": ("1000000", "shares", FILING),
}


def _evidence(key: str, layer: EvidenceSourceLayer) -> EvidenceRecord:
    return EvidenceRecord(
        id=f"E_{key}",
        target=TARGET,
        metric=key,
        value=1.0,
        unit="dimensionless",
        source_layer=layer,
        effective_date=AS_OF,
        observed_date=AS_OF,
        source_name="source",
        source_ref="https://dart.fss.or.kr/example",
        source_grade="A",
        confidence=0.9 if layer is FILING else 0.6,
        segment="orders",
    )


def _build(
    inputs: dict[str, tuple[str, str, EvidenceSourceLayer]],
    *,
    context_only: tuple[str, ...] = (),
):
    merged = {**inputs, **COMMON_INPUTS}
    ledger = EvidenceLedger(
        tuple(_evidence(key, layer) for key, (_, _, layer) in merged.items())
        # Collected disclosures that this route does not consume. Real runs always
        # carry these; they are exactly what the composition guardrail separates.
        + tuple(_evidence(key, FILING) for key in context_only if key not in merged)
    )
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
        for key, (amount, unit, _) in merged.items()
    )
    compiled = CompiledAssumptionSet(
        target_id=TARGET,
        assumptions=assumptions,
        assumption_set_hash="assumption-set-hash",
    )
    scenario_set = BoundScenarioSet(
        target_id=TARGET,
        scenarios=(BoundScenario("Core", assumptions, None),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="scenario-set-hash",
    )
    return ledger, compiled, scenario_set


def _plan(method: str) -> CompanyValuationPlan:
    return CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                asset_id="ORDERS",
                segment_id="orders",
                model_key=ModelKey("contracted_backlog", method, "1"),
                ownership_key="ownership",
                ev_to_equity_adjustment_key="ev_adjustment",
            ),
        ),
        reporting_unit=UNIT,
        diluted_shares_key="diluted_shares",
    )


def _registry(evaluator) -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register(evaluator)
    return registry


def _backlog_evaluator() -> BacklogBurnDCFEvaluator:
    return BacklogBurnDCFEvaluator(
        archetype="contracted_backlog",
        method="backlog_burn_dcf",
        version="1",
        forecast_years=3,
        discount_rate=RATE,
        discount_rate_path_id="wacc:hash",
        beta_path_id="beta:hash",
    )


def _explicit_evaluator() -> ExplicitFCFFDCFEvaluator:
    return ExplicitFCFFDCFEvaluator(
        archetype="contracted_backlog",
        method="normalized_dcf",
        version="1",
        forecast_years=3,
        discount_rate=RATE,
        discount_rate_path_id="wacc:hash",
        beta_path_id="beta:hash",
    )


def _value(inputs, evaluator, method, *, context_only: tuple[str, ...] = ()):
    ledger, compiled, scenario_set = _build(inputs, context_only=context_only)
    valuation = execute_company_valuation(
        scenario_set,
        plan=_plan(method),
        registry=_registry(evaluator),
    )
    return ledger, compiled, valuation


# ------------------------------------------------------------------ the payoff


ORDER_BOOK_KEYS = (
    "opening_backlog",
    "opening_revenue",
    "new_orders_year_1",
    "new_orders_year_2",
    "new_orders_year_3",
)
ORDER_BOOK_EVIDENCE = frozenset(f"E_{key}" for key in ORDER_BOOK_KEYS)


def _composition(inputs, evaluator, method, *, context_only=()):
    ledger, compiled, _ = _value(
        inputs, evaluator, method, context_only=context_only
    )
    return build_evidence_composition_report(ledger=ledger, compiled=compiled)


def test_backlog_model_consumes_the_disclosed_order_book():
    report = _composition(BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf")
    assert ORDER_BOOK_EVIDENCE.issubset(report.valuation_input_evidence_ids)
    assert report.layer_count(FILING) == 7


def test_explicit_fcff_model_leaves_the_same_order_book_as_context_only():
    report = _composition(
        EXPLICIT_INPUTS,
        _explicit_evaluator(),
        "normalized_dcf",
        context_only=ORDER_BOOK_KEYS,
    )
    # Collected and hash-bound, but never cited by a committed assumption.
    assert report.ledger_active_count == 13
    assert not ORDER_BOOK_EVIDENCE.intersection(report.valuation_input_evidence_ids)
    assert report.layer_count(FILING) == 2


def test_driving_revenue_from_backlog_raises_the_filing_cited_share():
    backlog = _composition(BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf")
    explicit = _composition(
        EXPLICIT_INPUTS,
        _explicit_evaluator(),
        "normalized_dcf",
        context_only=ORDER_BOOK_KEYS,
    )
    assert (
        backlog.valuation_primary_backed_share
        > explicit.valuation_primary_backed_share
    )
    assert (
        backlog.valuation_underwriting_share < explicit.valuation_underwriting_share
    )


def test_both_routes_agree_on_value_so_the_difference_is_provenance_not_arithmetic():
    """The explicit path is the backlog path's own FCFF, so values must coincide."""
    _, _, backlog = _value(BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf")
    _, _, explicit = _value(EXPLICIT_INPUTS, _explicit_evaluator(), "normalized_dcf")
    difference = abs(
        backlog.scenarios[0].value_per_share - explicit.scenarios[0].value_per_share
    )
    assert difference < Decimal("0.01")


# ------------------------------------------------------------ downstream contract


def test_sensitivity_guardrail_measures_a_backlog_segment():
    _, _, valuation = _value(
        BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf"
    )
    report = build_valuation_sensitivity_report(valuation=valuation)
    scenario = report.scenarios[0]
    assert scenario.measured
    assert scenario.dominant is not None
    assert report.findings


def test_reverse_dcf_reconstructs_a_backlog_segment():
    _, _, valuation = _value(
        BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf"
    )
    price = valuation.scenarios[0].value_per_share * Decimal("0.8")
    result = build_reverse_dcf_result(
        valuation=valuation,
        market_price=price,
        market_as_of=AS_OF,
        market_currency=UNIT,
    )
    scenario = result.scenarios[0]
    assert scenario.reconstructed
    assert scenario.implied_terminal_growth is not None
    # A market discount to the frozen value implies weaker perpetual growth.
    assert scenario.implied_terminal_growth < scenario.model_terminal_growth
    assert scenario.implied_fcff_scale < Decimal("1")


def test_market_observation_never_enters_the_backlog_model():
    """Reverse DCF consumes the frozen valuation, not the evaluator inputs."""
    _, _, valuation = _value(
        BACKLOG_INPUTS, _backlog_evaluator(), "backlog_burn_dcf"
    )
    observation = MarketObservation(
        float(valuation.scenarios[0].value_per_share), AS_OF, "https://example.test"
    )
    before = valuation.valuation_hash
    build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal(str(observation.price)),
        market_as_of=observation.as_of,
        market_currency=UNIT,
    )
    assert valuation.valuation_hash == before
