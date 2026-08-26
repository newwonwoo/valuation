from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class CostOfEquityInputs:
    risk_free_rate: float
    beta: float
    equity_risk_premium: float
    cash_flow_currency: str
    risk_free_currency: str
    country_risk_premium: float = 0.0
    country_risk_lambda: float = 0.0
    additional_risk_premium: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (
            ("risk_free_rate", self.risk_free_rate),
            ("beta", self.beta),
            ("equity_risk_premium", self.equity_risk_premium),
            ("country_risk_premium", self.country_risk_premium),
            ("country_risk_lambda", self.country_risk_lambda),
            ("additional_risk_premium", self.additional_risk_premium),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.beta <= 0:
            raise ValueError("beta must be positive")
        if self.equity_risk_premium < 0 or self.country_risk_premium < 0:
            raise ValueError("risk premia must be non-negative")
        if not 0 <= self.country_risk_lambda <= 2:
            raise ValueError("country_risk_lambda must be in [0, 2]")
        if not self.cash_flow_currency or not self.risk_free_currency:
            raise ValueError("cash-flow and risk-free currencies are required")
        if self.cash_flow_currency != self.risk_free_currency:
            raise ValueError("risk-free currency must match cash-flow currency")

    @property
    def cost_of_equity(self) -> float:
        return (
            self.risk_free_rate
            + self.beta * self.equity_risk_premium
            + self.country_risk_lambda * self.country_risk_premium
            + self.additional_risk_premium
        )


@dataclass(frozen=True)
class CostOfDebtInputs:
    marginal_pre_tax_cost: float
    tax_rate: float

    def __post_init__(self) -> None:
        if not isfinite(self.marginal_pre_tax_cost) or self.marginal_pre_tax_cost < 0:
            raise ValueError("marginal_pre_tax_cost must be finite and non-negative")
        if not isfinite(self.tax_rate) or not 0 <= self.tax_rate < 1:
            raise ValueError("tax_rate must be in [0, 1)")

    @property
    def after_tax_cost(self) -> float:
        return self.marginal_pre_tax_cost * (1.0 - self.tax_rate)


@dataclass(frozen=True)
class TargetCapitalStructure:
    equity_weight: float
    debt_weight: float

    def __post_init__(self) -> None:
        if not all(isfinite(v) for v in (self.equity_weight, self.debt_weight)):
            raise ValueError("capital-structure weights must be finite")
        if self.equity_weight < 0 or self.debt_weight < 0:
            raise ValueError("capital-structure weights must be non-negative")
        if abs(self.equity_weight + self.debt_weight - 1.0) > 1e-9:
            raise ValueError("market-value capital-structure weights must sum to one")


@dataclass(frozen=True)
class WACCResult:
    cost_of_equity: float
    after_tax_cost_of_debt: float
    equity_weight: float
    debt_weight: float
    wacc: float


def compute_wacc(
    equity: CostOfEquityInputs,
    debt: CostOfDebtInputs,
    capital_structure: TargetCapitalStructure,
) -> WACCResult:
    ke = equity.cost_of_equity
    kd = debt.after_tax_cost
    value = capital_structure.equity_weight * ke + capital_structure.debt_weight * kd
    return WACCResult(ke, kd, capital_structure.equity_weight, capital_structure.debt_weight, value)


@dataclass(frozen=True)
class CustomerAdvanceCreditEvidence:
    repeated_and_structural: bool
    net_debt_to_ebitda_improved: bool
    interest_coverage_improved: bool
    external_borrowing_growth_slowed: bool
    borrowing_rate_or_credit_spread_declined: bool
    liquidity_risk_reduced: bool

    @property
    def supports_wacc_reduction(self) -> bool:
        return all(
            (
                self.repeated_and_structural,
                self.net_debt_to_ebitda_improved,
                self.interest_coverage_improved,
                self.external_borrowing_growth_slowed,
                self.borrowing_rate_or_credit_spread_declined,
                self.liquidity_risk_reduced,
            )
        )


def customer_funded_growth_ratio(
    growth_related_customer_advances: float,
    growth_capex: float,
    incremental_nwc_need: float,
) -> float:
    values = (growth_related_customer_advances, growth_capex, incremental_nwc_need)
    if not all(isfinite(v) and v >= 0 for v in values):
        raise ValueError("customer-funded-growth inputs must be finite and non-negative")
    denominator = growth_capex + incremental_nwc_need
    if denominator <= 0:
        raise ValueError("growth CAPEX + incremental NWC need must be positive")
    return growth_related_customer_advances / denominator


@dataclass(frozen=True)
class TerminalConsistency:
    wacc: float
    terminal_growth: float
    terminal_roic: float
    reinvestment_rate: float


def validate_terminal_consistency(
    *,
    wacc: float,
    terminal_growth: float,
    terminal_roic: float,
) -> TerminalConsistency:
    for name, value in (
        ("wacc", wacc),
        ("terminal_growth", terminal_growth),
        ("terminal_roic", terminal_roic),
    ):
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")
    if wacc <= terminal_growth:
        raise ValueError("WACC must exceed terminal growth")
    if terminal_roic <= 0:
        raise ValueError("terminal ROIC must be positive")
    reinvestment = terminal_growth / terminal_roic
    if reinvestment < 0 or reinvestment > 1:
        raise ValueError("terminal reinvestment rate g/ROIC must be in [0, 1]")
    return TerminalConsistency(wacc, terminal_growth, terminal_roic, reinvestment)
