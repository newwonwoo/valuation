from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import ScenarioBindingSpec, ScenarioBindingStatus, bind_scenarios


def assumption(key: str, scenario: str, value: str, *, calibration=None) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id=scenario,
        measure=Measure(Decimal(value), "ratio", "2026-06-30"),
        bridge_id=f"B:{scenario}:{key}",
        evidence_ids=(f"E:{scenario}:{key}",),
        hypothesis_id=f"H:{scenario}:{key}",
        economic_path_id=f"P:{scenario}:{key}",
        transform_id="identity_observation",
        input_evidence_hash=f"hash:{scenario}:{key}",
        calibration_status=calibration,
    )


def compiled(*items: CompiledAssumption) -> CompiledAssumptionSet:
    return CompiledAssumptionSet("T", tuple(items), "ASSUMPTION_HASH")


def test_descriptive_scenarios_bind_without_numeric_probability():
    result = bind_scenarios(
        compiled(
            assumption("margin", "Bear", "0.1"),
            assumption("margin", "Base", "0.2"),
            assumption("margin", "Bull", "0.3"),
        ),
        ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin",)),
    )
    assert result.status is ScenarioBindingStatus.BOUND
    assert result.scenario_set is not None
    assert not result.scenario_set.numeric_weighting_allowed
    assert result.scenario_set.calibration_status is CalibrationStatus.UNCALIBRATED


def test_calibrated_scenario_probabilities_enable_numeric_weighting():
    items = []
    for scenario, margin, probability in (
        ("Bear", "0.1", "0.2"),
        ("Base", "0.2", "0.5"),
        ("Bull", "0.3", "0.3"),
    ):
        items.extend((
            assumption("margin", scenario, margin),
            assumption("scenario_probability", scenario, probability, calibration=CalibrationStatus.CALIBRATED),
        ))
    result = bind_scenarios(
        compiled(*items),
        ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin", "scenario_probability"), "scenario_probability"),
    )
    assert result.passed
    assert result.scenario_set.numeric_weighting_allowed
    assert result.scenario_set.calibration_status is CalibrationStatus.CALIBRATED
    assert result.scenario_set.get("Base").probability == Decimal("0.5")


def test_uncalibrated_probability_is_withheld_not_fabricated():
    items = []
    for scenario, probability in (("Bear", "0.2"), ("Base", "0.5"), ("Bull", "0.3")):
        items.extend((
            assumption("margin", scenario, "0.2"),
            assumption("scenario_probability", scenario, probability, calibration=CalibrationStatus.UNCALIBRATED),
        ))
    result = bind_scenarios(
        compiled(*items),
        ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin", "scenario_probability"), "scenario_probability"),
    )
    assert result.passed
    assert not result.scenario_set.numeric_weighting_allowed
    assert all(item.probability is None for item in result.scenario_set.scenarios)
    assert any(item.code == "PROBABILITY_WEIGHTING_WITHHELD" for item in result.findings)


def test_missing_required_assumption_blocks_binding():
    result = bind_scenarios(
        compiled(
            assumption("margin", "Bear", "0.1"),
            assumption("margin", "Base", "0.2"),
        ),
        ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin",)),
    )
    assert not result.passed
    assert any(item.code == "MISSING_REQUIRED_ASSUMPTION" for item in result.findings)


def test_calibrated_probability_sum_must_equal_one():
    items = []
    for scenario, probability in (("Bear", "0.2"), ("Base", "0.5"), ("Bull", "0.4")):
        items.extend((
            assumption("margin", scenario, "0.2"),
            assumption("scenario_probability", scenario, probability, calibration=CalibrationStatus.CALIBRATED),
        ))
    result = bind_scenarios(
        compiled(*items),
        ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin", "scenario_probability"), "scenario_probability"),
    )
    assert not result.passed
    assert any(item.code == "CALIBRATED_PROBABILITY_SUM_INVALID" for item in result.findings)
