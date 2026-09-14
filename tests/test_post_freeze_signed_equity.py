from decimal import Decimal

import pytest

from valuation_engine.post_freeze import compare_generic_to_market, compare_generic_to_street
from valuation_engine.records import MarketObservation
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.street import StreetResearchReport, analyze_street_gap
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue


def valuation(values=("-20", "0", "100"), expected=None):
    return GenericValuationResult(
        scenarios=tuple(ScenarioPerShareValue(s, Decimal(v)*10, "KRW", Decimal(10),
            Decimal(v), s, (s,)) for s, v in zip(("Down", "Base", "Bull"), values)),
        equity_aggregation=ScenarioEquityAggregation((), None, expected is not None),
        expected_value_per_share=Decimal(expected) if expected is not None else None,
        reporting_unit="KRW", valuation_hash="frozen-signed-valuation")


def reports():
    return (StreetResearchReport(broker="Test", analyst="Test", published_date="2026-08-01",
        target_price=50, target_price_currency="KRW", valuation_method="DCF", base_year="2027",
        estimates=(), source_ref="https://example.com/research"),)


def test_signed_residual_preserved_but_comparison_loss_cannot_exceed_principal():
    frozen = valuation()
    market = compare_generic_to_market(frozen, MarketObservation(50, "2026-09-13", "source"), currency="KRW")
    street = compare_generic_to_street(frozen, reports())
    for envelope in (market.envelope, street.envelope):
        down = envelope.get("Down")
        assert down.intrinsic_value_per_share == Decimal("-20")
        assert down.comparison_equity_value_per_share == 0
        assert down.limited_liability_floor_applied
        assert down.gap_per_share == -50
        assert down.gap_pct_of_reference == -1
        assert envelope.get("Base").gap_pct_of_reference == -1
        assert not envelope.get("Base").limited_liability_floor_applied
        assert envelope.get("Bull").gap_pct_of_reference == 1
    assert frozen.scenarios[0].value_per_share == -20
    assert frozen.valuation_hash == "frozen-signed-valuation"


@pytest.mark.parametrize("expected", ["-10", "0", "30"])
def test_mixed_sign_expected_comparison_does_not_floor_after_averaging(expected):
    frozen = valuation(expected=expected)
    bundle = compare_generic_to_street(frozen, reports())
    assert bundle.envelope.expected_gap is None
    assert "scenario" in bundle.envelope.expected_gap_withheld_reason
    assert bundle.expected_gap_analysis is None
    assert frozen.expected_value_per_share == Decimal(expected)


def test_zero_equity_scenario_and_zero_expected_are_valid_comparisons():
    bundle = compare_generic_to_street(valuation(("0", "0", "0"), expected="0"), reports())
    assert bundle.envelope.expected_gap.gap_pct_of_reference == -1
    assert bundle.expected_gap_analysis.headline_gap_pct_of_street == -1
    assert bundle.envelope.expected_gap_withheld_reason is None
    assert analyze_street_gap(0, reports()).headline_gap_pct_of_street == -1


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_intrinsic_remains_invalid(bad):
    with pytest.raises(ValueError, match="finite"):
        compare_generic_to_street(valuation((bad, "0", "100")), reports())


@pytest.mark.parametrize("price", [0, -1, float("inf"), float("nan")])
def test_nonpositive_or_nonfinite_market_reference_remains_invalid(price):
    with pytest.raises(ValueError, match="positive"):
        compare_generic_to_market(valuation(), MarketObservation(price, "2026-09-13", "source"), currency="KRW")
