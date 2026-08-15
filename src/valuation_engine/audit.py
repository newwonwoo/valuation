from __future__ import annotations

from dataclasses import replace
from math import isclose

from .engine import run_valuation
from .models import Scenario


def audit_model(scenarios: list[Scenario], shares: int, market_price: float | None = None) -> dict:
    base = run_valuation(scenarios, shares, market_price=market_price)
    stress_price = 1.0 if market_price is None else max(1.0, market_price * 0.2)
    anchor_stress = run_valuation(scenarios, shares, market_price=stress_price)
    anchor_pass = isclose(base.expected_value_per_share, anchor_stress.expected_value_per_share, rel_tol=0, abs_tol=1e-9)

    asp_up = [replace(s, poly_asp_usd_per_kg=s.poly_asp_usd_per_kg + 1.0) for s in scenarios]
    asp_stress = run_valuation(asp_up, shares, market_price=market_price)
    asp_pass = asp_stress.expected_value_per_share > base.expected_value_per_share

    return {
        "probabilities_sum_to_one": abs(sum(s.probability for s in scenarios) - 1) < 1e-9,
        "current_price_anchor_zero": anchor_pass,
        "asp_sensitivity_positive": asp_pass,
        "asp_plus_1_expected_value_change": asp_stress.expected_value_per_share - base.expected_value_per_share,
        "pass": anchor_pass and asp_pass,
    }
