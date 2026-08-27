from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.dcf_evaluators import (
    ExplicitFCFFDCFEvaluator,
    LiveDCFRegistration,
)
from valuation_engine.scenario_binding import BoundScenario


def assumption(key: str, value: str, *, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Core",
        measure=Measure(Decimal(value), "KRW_billion", "2026-08-26"),
        bridge_id=f"B:{key}",
        evidence_ids=(f"E:{key}",),
        hypothesis_id=f"H:{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH:{key}",
    )


def ratio(key: str, value: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Core",
        measure=Measure(Decimal(value), "ratio", "2026-08-26"),
        bridge_id=f"B:{key}",
        evidence_ids=(f"E:{key}",),
        hypothesis_id=f"H:{key}",
        economic_path_id=f"path:{key}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH:{key}",
    )


def base_scenario(*, include_uhv: bool) -> BoundScenario:
    rows = [
        assumption("fcff_year_1", "100", path="base:y1"),
        assumption("fcff_year_2", "110", path="base:y2"),
        assumption("fcff_year_3", "120", path="base:y3"),
        assumption("legacy_capex", "20", path="legacy:capex"),
        ratio("terminal_growth", "0.02"),
        ratio("terminal_roic", "0.12"),
    ]
    if include_uhv:
        rows.extend(
            (
                assumption("uhv_fcff_year_1", "0", path="uhv:y1"),
                assumption("uhv_fcff_year_2", "5", path="uhv:ramp"),
                assumption("uhv_fcff_year_3", "15", path="uhv:capacity"),
                assumption("uhv_property_capex", "30", path="uhv:capex"),
            )
        )
    return BoundScenario("Core", tuple(rows))


def evaluator(*, incremental: bool) -> ExplicitFCFFDCFEvaluator:
    return ExplicitFCFFDCFEvaluator(
        archetype="capacity_manufacturing",
        method="driver_dcf",
        version="multi-cohort-v1",
        forecast_years=3,
        discount_rate=Decimal("0.10"),
        discount_rate_path_id="wacc:HASH",
        expansion_capex_key="legacy_capex",
        expansion_capex_year=1,
        additive_fcff_prefixes=(("uhv_",) if incremental else ()),
        additional_expansion_capex=(
            (("uhv_property_capex", 2),) if incremental else ()
        ),
    )


def test_incremental_capacity_cohort_and_its_capex_are_consumed_together():
    base = evaluator(incremental=False).evaluate(
        base_scenario(include_uhv=False),
        segment_id="core",
    )
    incremental = evaluator(incremental=True).evaluate(
        base_scenario(include_uhv=True),
        segment_id="core",
    )

    expected_increment = (
        Decimal("0") / Decimal("1.10")
        + (Decimal("5") - Decimal("30")) / Decimal("1.10") ** 2
        + Decimal("15") / Decimal("1.10") ** 3
        + (Decimal("15") * Decimal("1.02") / Decimal("0.08"))
        / Decimal("1.10") ** 3
    )
    assert abs((incremental.value.amount - base.value.amount) - expected_increment) < Decimal(
        "1e-20"
    )
    assert "uhv:ramp" in incremental.economic_path_ids
    assert "uhv:capacity" in incremental.economic_path_ids
    assert "uhv:capex" in incremental.economic_path_ids


def test_registration_rejects_duplicate_or_out_of_range_capacity_capex():
    with pytest.raises(ValueError, match="keys must be unique"):
        LiveDCFRegistration(
            "capacity_manufacturing",
            "driver_dcf",
            "1",
            3,
            expansion_capex_key="capex",
            expansion_capex_year=1,
            additional_expansion_capex=(("capex", 2),),
        ).validate()

    with pytest.raises(ValueError, match="inside the explicit forecast"):
        LiveDCFRegistration(
            "capacity_manufacturing",
            "driver_dcf",
            "1",
            3,
            additional_expansion_capex=(("new_capex", 4),),
        ).validate()


def test_registration_rejects_duplicate_additive_fcff_prefixes():
    with pytest.raises(ValueError, match="prefixes must be unique"):
        LiveDCFRegistration(
            "capacity_manufacturing",
            "driver_dcf",
            "1",
            3,
            additive_fcff_prefixes=("uhv_", "uhv_"),
        ).validate()
