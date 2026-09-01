from pathlib import Path

import pytest

from valuation_engine.calibration_cohort_registry import (
    CalibrationCohortRegistryError,
    load_production_calibration_registry,
    resolve_production_calibration_cohort,
    validate_declared_calibration,
)


ROOT = Path(__file__).resolve().parents[1]


def test_committed_registry_resolves_steel_and_reit_production_cohorts():
    registry = load_production_calibration_registry(
        ROOT / "config" / "kr_calibration_cohort_registry.yaml"
    )

    steel = resolve_production_calibration_cohort(
        registry,
        ksic_code="2411",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert steel is not None
    assert steel.cohort_key == "kr.steel.long|5y_path|continuous_v1"

    reit = resolve_production_calibration_cohort(
        registry,
        ksic_code="6811",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert reit is not None
    assert reit.cohort_key == "kr.reit.office|5y_path|continuous_v1"


def test_unregistered_industry_remains_allowed_to_run_uncalibrated():
    registry = load_production_calibration_registry()
    assert (
        resolve_production_calibration_cohort(
            registry,
            ksic_code="2611",
            forecast_years=5,
            scenario_ids=("Down", "Base", "Bull"),
        )
        is None
    )


def test_registered_cohort_requires_calibration_binding_and_names_it():
    registry = load_production_calibration_registry()
    cohort = resolve_production_calibration_cohort(
        registry,
        ksic_code="2411",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert cohort is not None

    with pytest.raises(CalibrationCohortRegistryError) as caught:
        validate_declared_calibration(cohort, None)
    message = str(caught.value)
    assert "CALIBRATION_REQUIRED" in message
    assert "kr-steel-long-continuous-v1" in message
    assert "kr.steel.long|5y_path|continuous_v1" in message


def test_registered_cohort_rejects_wrong_probability_route():
    registry = load_production_calibration_registry()
    cohort = resolve_production_calibration_cohort(
        registry,
        ksic_code="6811",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert cohort is not None

    with pytest.raises(CalibrationCohortRegistryError, match="CALIBRATION_COHORT_MISMATCH"):
        validate_declared_calibration(
            cohort,
            {
                "cohort_key": "wrong",
                "forecast_class": cohort.forecast_class,
                "external_probability_source": cohort.external_probability_source,
            },
        )


def test_registered_cohort_accepts_the_exact_structural_binding():
    registry = load_production_calibration_registry()
    cohort = resolve_production_calibration_cohort(
        registry,
        ksic_code="2411",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert cohort is not None
    validate_declared_calibration(
        cohort,
        {
            "cohort_key": cohort.cohort_key,
            "forecast_class": cohort.forecast_class,
            "external_probability_source": cohort.external_probability_source,
        },
    )

def test_target_binding_rejects_other_issuer_exclusion():
    registry = load_production_calibration_registry()
    cohort = resolve_production_calibration_cohort(
        registry,
        ksic_code="2411",
        forecast_years=5,
        scenario_ids=("Down", "Base", "Bull"),
    )
    assert cohort is not None
    with pytest.raises(CalibrationCohortRegistryError, match="CALIBRATION_TARGET_MISMATCH"):
        validate_declared_calibration(
            cohort,
            {
                "cohort_key": cohort.cohort_key,
                "forecast_class": cohort.forecast_class,
                "external_probability_source": cohort.external_probability_source,
                "constants": {"excluded_ticker": "104700"},
            },
            target_ticker="084010",
            target_corp_code="00113225",
            conditioning_source_ref=(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?"
                "corp_code=00113225&bsns_year=2025"
            ),
        )
