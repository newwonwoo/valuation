import pytest

from valuation_engine.dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from valuation_engine.finite_life_evaluators import FiniteLifeNPVRegistration, live_finite_npv_registry_loader
from valuation_engine.rnpv_evaluator import LiveRNPVRegistration, live_rnpv_registry_loader


def test_cross_method_warranted_per_cannot_register_as_segment_dcf():
    with pytest.raises(ValueError, match="belongs to execution family warranted_per"):
        live_fcff_dcf_registry_loader(
            registrations=(
                LiveDCFRegistration(
                    "capacity_manufacturing",
                    "warranted_per",
                    "invalid-v1",
                    3,
                ),
            )
        )


def test_sotp_aggregator_cannot_register_as_finite_life_segment_npv():
    with pytest.raises(ValueError, match="belongs to execution family sotp"):
        live_finite_npv_registry_loader(
            registrations=(
                FiniteLifeNPVRegistration(
                    "project_finance",
                    "sotp",
                    "invalid-v1",
                    3,
                ),
            )
        )


def test_dcf_method_cannot_register_as_rnpv():
    with pytest.raises(ValueError, match="belongs to execution family explicit_fcff_dcf"):
        live_rnpv_registry_loader(
            registrations=(
                LiveRNPVRegistration(
                    "capacity_manufacturing",
                    "driver_dcf",
                    "invalid-v1",
                    3,
                    "test-cohort",
                ),
            )
        )
