from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    SegmentValuation,
    ValueKind,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.sotp import (
    ParentAdjustment,
    SegmentAggregationInput,
    aggregate_scenario_equity_values,
    aggregate_sotp,
)


def assumption(key: str, value: str, unit: str, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Base",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B:{key}",
        evidence_ids=(f"E:{key}",),
        hypothesis_id=f"H:{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"hash:{key}",
    )


def test_exact_registry_has_no_generic_fallback():
    registry = EvaluatorRegistry()
    evaluator = NormalizedMultipleEvaluator("commodity_price_taker")
    registry.register(evaluator)
    assert registry.get(ModelKey("commodity_price_taker", "normalized_multiple", "1")) is evaluator
    with pytest.raises(KeyError, match="no exact evaluator"):
        registry.get(ModelKey("capacity_manufacturing", "normalized_multiple", "1"))
    with pytest.raises(ValueError, match="duplicate evaluator"):
        registry.register(evaluator)


def test_normalized_multiple_evaluator_uses_compiled_assumptions_only():
    scenario = BoundScenario(
        "Base",
        (
            assumption("normalized_ebitda", "100", "KRW_billion", "PATH:EBITDA"),
            assumption("normalized_multiple", "8", "multiple", "PATH:MULTIPLE"),
        ),
    )
    registry = EvaluatorRegistry()
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    value = registry.evaluate(
        ModelKey("commodity_price_taker", "normalized_multiple", "1"),
        scenario,
        segment_id="poly",
    )
    assert value.value_kind is ValueKind.ENTERPRISE_VALUE
    assert value.value.amount == Decimal("800")
    assert value.value.unit == "KRW_billion"
    assert value.economic_path_ids == ("PATH:EBITDA", "PATH:MULTIPLE")


def segment_value(segment: str, scenario: str, amount: str, path: str, *, kind=ValueKind.ENTERPRISE_VALUE):
    return SegmentValuation(
        contribution_id=f"{segment}:{scenario}",
        segment_id=segment,
        scenario_id=scenario,
        value_kind=kind,
        value=Measure(Decimal(amount), "KRW_billion", "2026-06-30"),
        economic_path_ids=(path,),
        evaluator_id="test",
        evaluator_version="1",
    )


def test_sotp_requires_explicit_ev_to_equity_bridge_and_applies_ownership():
    result = aggregate_sotp(
        (
            SegmentAggregationInput(
                "asset:A",
                segment_value("A", "Base", "1000", "PATH:A"),
                Decimal("0.8"),
                Measure(Decimal("-200"), "KRW_billion", "2026-06-30"),
            ),
        ),
        scenario_id="Base",
        reporting_unit="KRW_billion",
        parent_adjustments=(
            ParentAdjustment("parent_cash", Measure(Decimal("100"), "KRW_billion", "2026-06-30")),
        ),
    )
    # (1000 - 200) * 80% + 100 = 740
    assert result.equity_value.amount == Decimal("740.0")
    assert result.aggregation_hash


def test_sotp_blocks_duplicate_economic_paths():
    inputs = (
        SegmentAggregationInput(
            "asset:A", segment_value("A", "Base", "100", "SAME"), Decimal("1"),
            Measure(Decimal("0"), "KRW_billion", "2026-06-30"),
        ),
        SegmentAggregationInput(
            "asset:B", segment_value("B", "Base", "200", "SAME"), Decimal("1"),
            Measure(Decimal("0"), "KRW_billion", "2026-06-30"),
        ),
    )
    with pytest.raises(ValueError, match="duplicate economic value path"):
        aggregate_sotp(inputs, scenario_id="Base", reporting_unit="KRW_billion")


def bound_set(*, calibrated: bool) -> BoundScenarioSet:
    probabilities = (Decimal("0.2"), Decimal("0.5"), Decimal("0.3")) if calibrated else (None, None, None)
    scenarios = tuple(
        BoundScenario(name, (), probability)
        for name, probability in zip(("Bear", "Base", "Bull"), probabilities)
    )
    return BoundScenarioSet(
        target_id="T",
        scenarios=scenarios,
        calibration_status=CalibrationStatus.CALIBRATED if calibrated else CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=calibrated,
        scenario_set_hash="S",
    )


def company_value(scenario: str, amount: str):
    return aggregate_sotp(
        (
            SegmentAggregationInput(
                f"asset:{scenario}",
                segment_value("core", scenario, amount, f"PATH:{scenario}"),
                Decimal("1"),
                Measure(Decimal("0"), "KRW_billion", "2026-06-30"),
            ),
        ),
        scenario_id=scenario,
        reporting_unit="KRW_billion",
    )


def test_expected_value_requires_calibrated_scenario_weights():
    values = (
        company_value("Bear", "100"),
        company_value("Base", "200"),
        company_value("Bull", "400"),
    )
    uncalibrated = aggregate_scenario_equity_values(bound_set(calibrated=False), values)
    assert uncalibrated.expected_equity_value is None

    calibrated = aggregate_scenario_equity_values(bound_set(calibrated=True), values)
    assert calibrated.expected_equity_value is not None
    assert calibrated.expected_equity_value.amount == Decimal("240.0")
