from decimal import Decimal

from valuation_engine.continuous_predictive_weight import (
    ContinuousWeightPolicy,
    PredictiveEvidenceProfile,
    continuous_predictive_weight,
)


def test_negative_oos_skill_reduces_weight_but_never_forces_zero():
    result = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=40,
            company_count=8,
            quarter_count=5,
            brier_skill_windows=(Decimal("-0.10"), Decimal("-0.05")),
            brier_skill_interval=(Decimal("-0.25"), Decimal("0.10")),
            ece=Decimal("0.12"),
            regime_similarity=Decimal("0.40"),
        )
    )
    assert Decimal("0") < result.likelihood_weight < Decimal("1")
    assert result.uncertainty_inflation > Decimal("1")


def test_positive_predictive_skill_and_maturity_increase_weight():
    weak = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=20,
            company_count=4,
            quarter_count=3,
            brier_skill_windows=(Decimal("-0.02"),),
            ece=Decimal("0.15"),
            regime_similarity=Decimal("0.35"),
        )
    )
    strong = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=250,
            company_count=35,
            quarter_count=12,
            brier_skill_windows=(Decimal("0.12"), Decimal("0.15"), Decimal("0.18"), Decimal("0.14")),
            brier_skill_interval=(Decimal("0.08"), Decimal("0.20")),
            ece=Decimal("0.03"),
            regime_similarity=Decimal("0.90"),
        )
    )
    assert strong.likelihood_weight > weak.likelihood_weight
    assert strong.uncertainty_inflation < weak.uncertainty_inflation


def test_brier_interval_crossing_zero_is_soft_penalty_not_rejection():
    crossing = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=100,
            company_count=20,
            quarter_count=8,
            brier_skill_windows=(Decimal("0.02"), Decimal("0.01")),
            brier_skill_interval=(Decimal("-0.10"), Decimal("0.12")),
            ece=Decimal("0.05"),
            regime_similarity=Decimal("0.70"),
        )
    )
    narrow = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=100,
            company_count=20,
            quarter_count=8,
            brier_skill_windows=(Decimal("0.02"), Decimal("0.01")),
            brier_skill_interval=(Decimal("0.005"), Decimal("0.025")),
            ece=Decimal("0.05"),
            regime_similarity=Decimal("0.70"),
        )
    )
    assert crossing.likelihood_weight > Decimal("0")
    assert narrow.likelihood_weight > crossing.likelihood_weight


def test_no_oos_history_still_returns_small_positive_weight():
    result = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=0,
            company_count=0,
            quarter_count=0,
            brier_skill_windows=(),
            ece=None,
            regime_similarity=Decimal("0.50"),
        ),
        ContinuousWeightPolicy(minimum_weight=Decimal("0.05")),
    )
    assert result.likelihood_weight >= Decimal("0.05")
    assert result.mean_brier_skill is None


def test_zero_brier_skill_is_neutral_not_a_gate_boundary():
    result = continuous_predictive_weight(
        PredictiveEvidenceProfile(
            resolved_events=100,
            company_count=20,
            quarter_count=8,
            brier_skill_windows=(Decimal("0"), Decimal("0")),
            ece=Decimal("0.08"),
            regime_similarity=Decimal("0.50"),
        )
    )
    assert Decimal("0.45") <= result.skill_component <= Decimal("0.55")
    assert result.likelihood_weight > Decimal("0")
