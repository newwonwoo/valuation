from valuation_engine.records import CalibrationStatus


def test_only_calibrated_probability_can_drive_intrinsic_weighting():
    assert not CalibrationStatus.UNCALIBRATED.allows_intrinsic_probability_weight
    assert not CalibrationStatus.CALIBRATING.allows_intrinsic_probability_weight
    assert CalibrationStatus.CALIBRATED.allows_intrinsic_probability_weight
    assert not CalibrationStatus.DEGRADED.allows_intrinsic_probability_weight
