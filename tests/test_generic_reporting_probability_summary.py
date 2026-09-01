from decimal import Decimal

from valuation_engine.generic_reporting import _scenario_probability_summary
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet


def test_calibrated_bound_probabilities_drive_reader_summary():
    scenario_set = BoundScenarioSet(
        target_id="010130",
        scenarios=(
            BoundScenario("Down", (), Decimal("0.0779166666666667")),
            BoundScenario("Base", (), Decimal("0.6856166666666666")),
            BoundScenario("Bull", (), Decimal("0.2364666666666667")),
        ),
        calibration_status=CalibrationStatus.CALIBRATED,
        numeric_weighting_allowed=True,
        scenario_set_hash="hash",
        calibration_snapshot_hash="snapshot",
        calibration_dataset_hash="dataset",
    )

    summary = _scenario_probability_summary(scenario_set, None)

    assert summary == (
        "하방 7.8% · 기준 68.6% · 상방 23.6% "
        "(보정 완료·수치 가중 적용)"
    )
    assert "미산출" not in summary
    assert "미보정" not in summary


def test_no_probability_source_remains_unavailable():
    assert _scenario_probability_summary(None, None) == "미산출"
