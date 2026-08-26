from decimal import Decimal

import pytest

from valuation_engine.actual_units import Dimension, Measure, measure_from_raw


def test_money_units_convert_within_same_currency_base():
    value = measure_from_raw("2.5", "KRW_billion", "2026-06-30")
    converted = value.convert_to("KRW")
    assert converted.amount == Decimal("2500000000.0")
    assert converted.dimension is Dimension.MONEY


def test_cross_currency_conversion_requires_explicit_fx_transform():
    with pytest.raises(ValueError, match="explicit FX transform"):
        measure_from_raw("10", "USD", "2026-06-30").convert_to("KRW")


def test_dimension_mismatch_is_blocked():
    with pytest.raises(ValueError, match="dimension mismatch"):
        measure_from_raw("1", "GW", "2026-06-30").convert_to("kg")


def test_area_units_convert_to_canonical_square_metres():
    converted = measure_from_raw("1", "pyeong", "2026-06-30").convert_to("sqm")
    assert converted.amount == Decimal("3.305785123966942148760330579")
    assert converted.dimension is Dimension.AREA


def test_non_finite_measure_is_blocked():
    with pytest.raises(ValueError, match="finite"):
        Measure(Decimal("NaN"), "ratio", "2026-06-30")
