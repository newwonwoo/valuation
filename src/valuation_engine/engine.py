from __future__ import annotations

from .models import Scenario, ScenarioValue, ValuationResult


def value_scenario(s: Scenario, shares: int) -> ScenarioValue:
    if shares <= 0:
        raise ValueError("shares must be positive")
    if not 0 <= s.probability <= 1:
        raise ValueError("scenario probability must be between 0 and 1")
    if not 0 <= s.poly_utilization <= 1 or not 0 <= s.wafer_utilization <= 1:
        raise ValueError("utilization must be between 0 and 1")
    if not 0 < s.wafer_economic_share <= 1:
        raise ValueError("wafer economic share must be in (0, 1]")

    poly_ebitda_per_kg = s.poly_asp_usd_per_kg - s.poly_cash_cost_usd_per_kg - s.poly_other_cost_usd_per_kg
    poly_ebitda_trn = s.poly_capacity_kmt * 1_000_000 * s.poly_utilization * poly_ebitda_per_kg * s.fx_krw_per_usd / 1_000_000_000_000
    wafer_ebitda_trn = s.wafer_capacity_gw * 1_000_000_000 * s.wafer_utilization * s.wafer_ebitda_usd_per_w * s.fx_krw_per_usd * s.wafer_economic_share / 1_000_000_000_000
    terminal_ev_trn = poly_ebitda_trn * s.poly_multiple + wafer_ebitda_trn * s.wafer_multiple
    terminal_core_equity_trn = terminal_ev_trn - s.net_debt_trn_krw
    pv_core_equity_trn = terminal_core_equity_trn / ((1 + s.discount_rate) ** s.terminal_years)
    total_equity_trn = pv_core_equity_trn + s.other_business_pv_trn_krw
    fair_value_per_share = total_equity_trn * 1_000_000_000_000 / shares

    return ScenarioValue(s.name, s.probability, poly_ebitda_trn, wafer_ebitda_trn, terminal_ev_trn, terminal_core_equity_trn, pv_core_equity_trn, total_equity_trn, fair_value_per_share)


def run_valuation(scenarios: list[Scenario], shares: int, *, market_price: float | None = None) -> ValuationResult:
    probability_sum = sum(s.probability for s in scenarios)
    if abs(probability_sum - 1.0) > 1e-9:
        raise ValueError(f"scenario probabilities must sum to 1.0, got {probability_sum}")

    values = [value_scenario(s, shares) for s in scenarios]
    expected_equity = sum(v.probability * v.total_equity_trn for v in values)
    expected_per_share = sum(v.probability * v.fair_value_per_share for v in values)
    market_gap = None if market_price is None else market_price / expected_per_share - 1
    return ValuationResult(values, expected_equity, expected_per_share, market_price, market_gap, {"probability_sum": probability_sum, "market_price_used_in_valuation": False})
