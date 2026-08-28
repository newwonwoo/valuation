from decimal import Decimal

import pytest

from valuation_engine.scenario_posterior_monte_carlo import (
    CorrelationDependence,
    PosteriorEventFactor,
    PosteriorScenarioRule,
    simulate_scenario_posterior,
)


def factors():
    return (
        PosteriorEventFactor("revenue_miss", Decimal("3"), Decimal("7"), "REV"),
        PosteriorEventFactor("margin_compression", Decimal("4"), Decimal("6"), "MARGIN"),
    )


def rules():
    return (
        PosteriorScenarioRule("Bull", forbidden_event_ids=("revenue_miss", "margin_compression")),
        PosteriorScenarioRule("Core", required_event_ids=("revenue_miss",), forbidden_event_ids=("margin_compression",)),
        PosteriorScenarioRule("Down", required_event_ids=("margin_compression",)),
    )


def dependence(rho: str):
    return CorrelationDependence(
        version=f"rho-{rho}",
        event_ids=("revenue_miss", "margin_compression"),
        correlation_matrix=(
            (Decimal("1"), Decimal(rho)),
            (Decimal(rho), Decimal("1")),
        ),
    )


def test_monte_carlo_returns_point_probabilities_and_credible_intervals():
    result = simulate_scenario_posterior(
        factors=factors(),
        rules=rules(),
        dependence=dependence("0.35"),
        outer_draws=60,
        inner_draws=80,
        seed=7,
    )
    assert len(result.estimates) == 3
    total = sum(item.point_probability for item in result.estimates)
    assert abs(total - Decimal("1")) < Decimal("0.02")
    assert all(item.lower_probability <= item.point_probability <= item.upper_probability for item in result.estimates)
    assert result.simulation_hash


def test_wider_event_posteriors_produce_nonzero_scenario_uncertainty():
    result = simulate_scenario_posterior(
        factors=(
            PosteriorEventFactor("revenue_miss", Decimal("1.2"), Decimal("2.8"), "REV"),
            PosteriorEventFactor("margin_compression", Decimal("1.4"), Decimal("2.1"), "MARGIN"),
        ),
        rules=rules(),
        dependence=dependence("0.20"),
        outer_draws=80,
        inner_draws=60,
        seed=11,
    )
    assert any(item.upper_probability - item.lower_probability > Decimal("0.05") for item in result.estimates)


def test_dependence_is_explicit_and_changes_joint_scenario_result():
    positive = simulate_scenario_posterior(
        factors=factors(), rules=rules(), dependence=dependence("0.70"), outer_draws=50, inner_draws=80, seed=5
    )
    negative = simulate_scenario_posterior(
        factors=factors(), rules=rules(), dependence=dependence("-0.50"), outer_draws=50, inner_draws=80, seed=5
    )
    p = {item.scenario_id: item.point_probability for item in positive.estimates}
    n = {item.scenario_id: item.point_probability for item in negative.estimates}
    assert abs(p["Down"] - n["Down"]) > Decimal("0.01") or abs(p["Bull"] - n["Bull"]) > Decimal("0.01")


def test_invalid_correlation_matrix_is_rejected():
    bad = CorrelationDependence(
        version="bad",
        event_ids=("revenue_miss", "margin_compression"),
        correlation_matrix=((Decimal("1"), Decimal("0.4")), (Decimal("0.1"), Decimal("1"))),
    )
    with pytest.raises(ValueError, match="symmetric"):
        simulate_scenario_posterior(
            factors=factors(), rules=rules(), dependence=bad, outer_draws=20, inner_draws=20
        )


def test_every_simulated_state_must_map_to_exactly_one_scenario():
    incomplete = (
        PosteriorScenarioRule("Bull", forbidden_event_ids=("revenue_miss", "margin_compression")),
        PosteriorScenarioRule("Down", required_event_ids=("margin_compression",)),
    )
    with pytest.raises(ValueError, match="exactly one scenario"):
        simulate_scenario_posterior(
            factors=factors(), rules=incomplete, dependence=dependence("0.20"), outer_draws=20, inner_draws=20, seed=3
        )
