from dataclasses import fields
from decimal import Decimal

from valuation_engine.continuous_financial_path_probability import (
    ContinuousDriverDependence,
    ContinuousDriverPosterior,
    ScenarioFinancialPath,
    simulate_continuous_financial_paths,
)


def _driver(driver_id: str, mean: str, scale: str = "0.08") -> ContinuousDriverPosterior:
    return ContinuousDriverPosterior(
        driver_id=driver_id,
        mean_path=(Decimal(mean), Decimal(mean), Decimal(mean)),
        scale_path=(Decimal(scale), Decimal(scale), Decimal(scale)),
        mean_uncertainty_path=(Decimal("0.02"), Decimal("0.02"), Decimal("0.02")),
        source_hash=f"SRC-{driver_id}",
    )


def _scenario(scenario_id: str, revenue: str, margin: str, capex: str) -> ScenarioFinancialPath:
    return ScenarioFinancialPath(
        scenario_id=scenario_id,
        driver_paths=(
            ("revenue_growth", (Decimal(revenue),) * 3),
            ("operating_margin", (Decimal(margin),) * 3),
            ("capex_intensity", (Decimal(capex),) * 3),
        ),
        driver_weights=(
            ("revenue_growth", Decimal("1.0")),
            ("operating_margin", Decimal("1.2")),
            ("capex_intensity", Decimal("0.7")),
        ),
    )


def _dependence() -> ContinuousDriverDependence:
    return ContinuousDriverDependence(
        version="continuous-financial-rho-v1",
        driver_ids=("revenue_growth", "operating_margin", "capex_intensity"),
        correlation_matrix=(
            (Decimal("1"), Decimal("0.35"), Decimal("0.15")),
            (Decimal("0.35"), Decimal("1"), Decimal("0.10")),
            (Decimal("0.15"), Decimal("0.10"), Decimal("1")),
        ),
        student_t_df=6,
    )


def test_continuous_paths_map_every_draw_without_boolean_and_rules():
    result = simulate_continuous_financial_paths(
        drivers=(
            _driver("revenue_growth", "0.20"),
            _driver("operating_margin", "0.35"),
            _driver("capex_intensity", "0.28"),
        ),
        scenarios=(
            _scenario("Down", "-0.10", "0.10", "0.18"),
            _scenario("Core", "0.10", "0.25", "0.22"),
            _scenario("Bull", "0.25", "0.38", "0.30"),
        ),
        dependence=_dependence(),
        outer_draws=40,
        inner_draws=80,
        seed=23,
    )
    probs = {item.scenario_id: item.probability for item in result.estimates}
    assert abs(sum(probs.values()) - Decimal("1")) < Decimal("1e-12")
    assert probs["Bull"] > probs["Down"]
    assert all(item.lower_probability <= item.probability <= item.upper_probability for item in result.estimates)


def test_high_capex_does_not_mechanically_kill_bull_when_growth_path_is_bull_like():
    result = simulate_continuous_financial_paths(
        drivers=(
            _driver("revenue_growth", "0.27", "0.04"),
            _driver("operating_margin", "0.40", "0.04"),
            _driver("capex_intensity", "0.32", "0.03"),
        ),
        scenarios=(
            _scenario("Down", "-0.10", "0.10", "0.15"),
            _scenario("Core", "0.10", "0.25", "0.22"),
            _scenario("Bull", "0.28", "0.40", "0.32"),
        ),
        dependence=_dependence(),
        outer_draws=30,
        inner_draws=60,
        seed=29,
    )
    probs = {item.scenario_id: item.probability for item in result.estimates}
    assert probs["Bull"] > Decimal("0.50")


def test_probability_contract_contains_no_market_or_valuation_fields():
    names = {
        item.name.lower()
        for cls in (ContinuousDriverPosterior, ContinuousDriverDependence, ScenarioFinancialPath)
        for item in fields(cls)
    }
    forbidden = ("price", "market", "target", "intrinsic", "expected_value", "upside", "entry", "return")
    assert not any(any(token in name for token in forbidden) for name in names)


def test_scenario_assignment_uses_economic_paths_not_scenario_intrinsic_values():
    result_a = simulate_continuous_financial_paths(
        drivers=(
            _driver("revenue_growth", "0.12"),
            _driver("operating_margin", "0.27"),
            _driver("capex_intensity", "0.23"),
        ),
        scenarios=(
            _scenario("Down", "-0.10", "0.10", "0.15"),
            _scenario("Core", "0.10", "0.25", "0.22"),
            _scenario("Bull", "0.28", "0.40", "0.32"),
        ),
        dependence=_dependence(),
        outer_draws=25,
        inner_draws=50,
        seed=31,
    )
    result_b = simulate_continuous_financial_paths(
        drivers=(
            _driver("revenue_growth", "0.12"),
            _driver("operating_margin", "0.27"),
            _driver("capex_intensity", "0.23"),
        ),
        scenarios=(
            _scenario("Down", "-0.10", "0.10", "0.15"),
            _scenario("Core", "0.10", "0.25", "0.22"),
            _scenario("Bull", "0.28", "0.40", "0.32"),
        ),
        dependence=_dependence(),
        outer_draws=25,
        inner_draws=50,
        seed=31,
    )
    assert result_a.estimates == result_b.estimates
    assert result_a.simulation_hash == result_b.simulation_hash
