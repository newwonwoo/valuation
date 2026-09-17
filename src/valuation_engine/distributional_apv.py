"""Pathwise APV and old-shareholder value distributions.

Business value, usable tax shields and explicit financing costs remain separate.
Limited liability is applied only to an explicit horizon payoff or a documented
distress waterfall; aggregation never floors a signed valuation after the fact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .levered_financing_paths import FinancingActionType, FinancingPathResult


ZERO = Decimal("0")
ONE = Decimal("1")


class DistributionalAPVError(ValueError):
    """Raised when a path cannot be valued without violating APV contracts."""


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise DistributionalAPVError(f"{label} must be finite")
    return value


def _rate(value: Decimal, label: str, *, allow_negative: bool = False) -> Decimal:
    _decimal(value, label)
    lower = Decimal("-0.99") if allow_negative else ZERO
    if not lower <= value < ONE:
        raise DistributionalAPVError(f"{label} lies outside its valid range")
    return value


@dataclass(frozen=True)
class SegmentCashFlowPath:
    segment_id: str
    economic_path_id: str
    unlevered_fcff: tuple[Decimal, ...]
    asset_required_return: Decimal
    terminal_growth: Decimal

    def validate(self, horizon: int) -> None:
        if not self.segment_id or not self.economic_path_id or len(self.unlevered_fcff) != horizon:
            raise DistributionalAPVError("segment cash-flow identity or horizon is invalid")
        if not self.unlevered_fcff:
            raise DistributionalAPVError("segment cash-flow path cannot be empty")
        for value in self.unlevered_fcff:
            _decimal(value, "segment FCFF")
        _rate(self.asset_required_return, "asset required return")
        _rate(self.terminal_growth, "terminal growth", allow_negative=True)
        if self.asset_required_return <= self.terminal_growth:
            raise DistributionalAPVError("asset required return must exceed terminal growth")

    @property
    def terminal_value_at_horizon(self) -> Decimal:
        return self.unlevered_fcff[-1] * (ONE + self.terminal_growth) / (
            self.asset_required_return - self.terminal_growth
        )


@dataclass(frozen=True)
class TaxShieldSchedule:
    taxable_income_before_interest: tuple[Decimal, ...]
    deductible_interest: tuple[Decimal, ...]
    tax_rate: Decimal
    discount_rate: Decimal

    def validate(self, horizon: int) -> None:
        if (
            len(self.taxable_income_before_interest) != horizon
            or len(self.deductible_interest) != horizon
        ):
            raise DistributionalAPVError("tax-shield schedule horizon mismatch")
        for value in self.taxable_income_before_interest:
            _decimal(value, "taxable income")
        for value in self.deductible_interest:
            if _decimal(value, "deductible interest") < ZERO:
                raise DistributionalAPVError("deductible interest cannot be negative")
        _rate(self.tax_rate, "tax rate")
        _rate(self.discount_rate, "tax-shield discount rate")


@dataclass(frozen=True)
class NonOperatingAssetDisposal:
    """Disposed values on the same basis as the APV's pre-sale asset balances.

    Sale price is not a proxy for retained asset value. Operating-asset sales
    require rebuilt operating paths and are not supported by this bridge.
    """

    period: int
    gross_proceeds: Decimal
    present_asset_value: Decimal
    horizon_asset_value: Decimal


@dataclass(frozen=True)
class APVPathInput:
    path_id: str
    segments: tuple[SegmentCashFlowPath, ...]
    tax_shield_schedule: TaxShieldSchedule
    financing_result: FinancingPathResult
    non_operating_assets_present: Decimal
    non_operating_assets_at_horizon: Decimal
    distributions_to_old_holders: tuple[Decimal, ...]
    equity_required_return: Decimal
    initial_shares: Decimal
    asset_disposals: tuple[NonOperatingAssetDisposal, ...] = ()

    def validate(self) -> None:
        if not self.path_id or not self.segments:
            raise DistributionalAPVError("APV path identity is incomplete")
        horizon = len(self.tax_shield_schedule.taxable_income_before_interest)
        if horizon <= 0:
            raise DistributionalAPVError("APV horizon must be positive")
        if len(self.distributions_to_old_holders) != horizon:
            raise DistributionalAPVError("old-holder distribution horizon mismatch")
        if len(self.financing_result.periods) > horizon:
            raise DistributionalAPVError("financing result exceeds the APV horizon")
        if not self.financing_result.distressed and len(self.financing_result.periods) != horizon:
            raise DistributionalAPVError("surviving financing path must reach the horizon")
        segment_ids = tuple(item.segment_id for item in self.segments)
        economic_paths = tuple(item.economic_path_id for item in self.segments)
        if len(segment_ids) != len(set(segment_ids)):
            raise DistributionalAPVError("APV path repeats a segment")
        if len(economic_paths) != len(set(economic_paths)):
            raise DistributionalAPVError("APV path repeats an economic path")
        for segment in self.segments:
            segment.validate(horizon)
        self.tax_shield_schedule.validate(horizon)
        if _decimal(self.non_operating_assets_present, "present non-operating assets") < ZERO:
            raise DistributionalAPVError("present non-operating assets cannot be negative")
        if _decimal(self.non_operating_assets_at_horizon, "horizon non-operating assets") < ZERO:
            raise DistributionalAPVError("horizon non-operating assets cannot be negative")
        for value in self.distributions_to_old_holders:
            if _decimal(value, "old-holder distribution") < ZERO:
                raise DistributionalAPVError("old-holder distributions cannot be negative")
        _rate(self.equity_required_return, "equity required return")
        if _decimal(self.initial_shares, "initial shares") <= ZERO:
            raise DistributionalAPVError("initial shares must be positive")
        sales = tuple(
            (period.period, action.gross_amount)
            for period in self.financing_result.periods
            for action in period.actions
            if action.action_type is FinancingActionType.ASSET_SALE
        )
        for disposal in self.asset_disposals:
            if not 1 <= disposal.period <= horizon:
                raise DistributionalAPVError("asset disposal period is outside the horizon")
            for field in ("gross_proceeds", "present_asset_value", "horizon_asset_value"):
                if _decimal(getattr(disposal, field), field) <= ZERO:
                    raise DistributionalAPVError("disposed asset values must be positive")
        if sales != tuple((item.period, item.gross_proceeds) for item in self.asset_disposals):
            raise DistributionalAPVError("asset sales require matching disposed-asset values")
        if sales and self.financing_result.distressed:
            raise DistributionalAPVError("asset sales in distress require a reconciled recovery asset bridge")
        if any(
            action.source_id != "NON_CORE_ASSET_SALE"
            for period in self.financing_result.periods
            for action in period.actions
            if action.action_type is FinancingActionType.ASSET_SALE
        ):
            raise DistributionalAPVError("operating asset sales require rebuilt operating paths")
        if (sum((item.present_asset_value for item in self.asset_disposals), ZERO)
                > self.non_operating_assets_present
            or sum((item.horizon_asset_value for item in self.asset_disposals), ZERO)
                > self.non_operating_assets_at_horizon):
            raise DistributionalAPVError("disposed assets exceed the pre-sale asset balance")
        action_costs = tuple(
            action.transaction_cost
            for period in self.financing_result.periods
            for action in period.actions
            if action.action_type is not FinancingActionType.DISTRESS
            and action.transaction_cost != ZERO
        )
        if action_costs != self.financing_result.explicit_financing_costs:
            raise DistributionalAPVError(
                "financing action costs do not reconcile to the financing result"
            )


@dataclass(frozen=True)
class SegmentAPVResult:
    segment_id: str
    economic_path_id: str
    forecast_fcff_present_value: Decimal
    terminal_value_present_value: Decimal
    unlevered_value: Decimal
    terminal_value_at_horizon: Decimal


@dataclass(frozen=True)
class PathAPVResult:
    path_id: str
    segments: tuple[SegmentAPVResult, ...]
    usable_tax_shields: tuple[Decimal, ...]
    tax_shield_present_value: Decimal
    explicit_financing_cost_present_value: Decimal
    operating_apv: Decimal
    distressed: bool
    dilution_occurred: bool
    old_shareholder_retention: Decimal
    terminal_old_equity_payoff: Decimal | None
    distress_old_shareholder_recovery: Decimal | None
    old_shareholder_present_value: Decimal
    value_per_initial_share: Decimal
    initial_shares: Decimal
    distributions_to_old_holders: tuple[Decimal, ...]
    realized_periods: int
    equity_required_return: Decimal
    path_calculation_hash: str


@dataclass(frozen=True)
class EquityValueDistribution:
    quantiles: tuple[tuple[Decimal, Decimal], ...]
    mean: Decimal
    distress_probability: Decimal
    dilution_probability: Decimal
    expected_old_share_retention_in_distress: Decimal
    draw_count: int
    seed_set: tuple[int, ...]
    input_hash: str
    distribution_hash: str
    valuation_distribution_authorized: bool
    path_values_per_share: tuple[Decimal, ...]

    def quantile(self, probability: Decimal) -> Decimal:
        for key, value in self.quantiles:
            if key == probability:
                return value
        raise KeyError(f"quantile {probability} is not available")

    @property
    def headline_value(self) -> Decimal:
        return self.quantile(Decimal("0.50"))


def present_value(cash_flows: tuple[Decimal, ...], discount_rate: Decimal) -> Decimal:
    _rate(discount_rate, "discount rate")
    total = ZERO
    for period, cash_flow in enumerate(cash_flows, start=1):
        _decimal(cash_flow, "cash flow")
        total += cash_flow / ((ONE + discount_rate) ** period)
    return total


def calculate_usable_tax_shields(schedule: TaxShieldSchedule) -> tuple[Decimal, ...]:
    horizon = len(schedule.taxable_income_before_interest)
    schedule.validate(horizon)
    return tuple(
        min(max(taxable_income, ZERO), interest) * schedule.tax_rate
        for taxable_income, interest in zip(
            schedule.taxable_income_before_interest, schedule.deductible_interest
        )
    )


def evaluate_apv_path(path: APVPathInput) -> PathAPVResult:
    path.validate()
    horizon = len(path.tax_shield_schedule.taxable_income_before_interest)
    segment_results: list[SegmentAPVResult] = []
    for segment in path.segments:
        forecast_pv = present_value(segment.unlevered_fcff, segment.asset_required_return)
        terminal_h = segment.terminal_value_at_horizon
        terminal_pv = terminal_h / ((ONE + segment.asset_required_return) ** horizon)
        segment_results.append(
            SegmentAPVResult(
                segment_id=segment.segment_id,
                economic_path_id=segment.economic_path_id,
                forecast_fcff_present_value=forecast_pv,
                terminal_value_present_value=terminal_pv,
                unlevered_value=forecast_pv + terminal_pv,
                terminal_value_at_horizon=terminal_h,
            )
        )
    all_shields = calculate_usable_tax_shields(path.tax_shield_schedule)
    realized_horizon = len(path.financing_result.periods)
    shields = all_shields[:realized_horizon]
    financing_shields = tuple(
        period.cash_tax_shield for period in path.financing_result.periods
    )
    if shields != financing_shields:
        raise DistributionalAPVError(
            "APV tax shields and financing-path cash tax shields do not reconcile"
        )
    shield_pv = present_value(shields, path.tax_shield_schedule.discount_rate)
    financing_costs = tuple(
        sum(
            (
                action.transaction_cost
                for action in period.actions
                if action.action_type is not FinancingActionType.DISTRESS
            ),
            ZERO,
        )
        for period in path.financing_result.periods
    )
    financing_cost_pv = present_value(financing_costs, path.equity_required_return)
    operating_apv = (
        sum((item.unlevered_value for item in segment_results), ZERO)
        + shield_pv
        - financing_cost_pv
        + path.non_operating_assets_present
        - sum((item.present_asset_value for item in path.asset_disposals), ZERO)
    )

    realized_periods = (
        path.financing_result.distress_period
        if path.financing_result.distressed
        else horizon
    )
    assert realized_periods is not None
    distribution_pv = present_value(
        path.distributions_to_old_holders[:realized_periods], path.equity_required_return
    )
    terminal_payoff: Decimal | None = None
    distress_recovery: Decimal | None = None
    if path.financing_result.distressed:
        if path.financing_result.recovery is None:
            raise DistributionalAPVError("distressed financing path requires a recovery waterfall")
        distress_recovery = path.financing_result.recovery.old_shareholder_recovery
        payoff_pv = distress_recovery / (
            (ONE + path.equity_required_return) ** realized_periods
        )
        retention = path.financing_result.recovery.old_shareholder_retention
    else:
        terminal_enterprise_value = sum(
            (item.terminal_value_at_horizon for item in segment_results), ZERO
        )
        # The only going-concern floor is the explicit future horizon payoff.
        terminal_payoff = max(
            terminal_enterprise_value
            + path.non_operating_assets_at_horizon
            - sum((item.horizon_asset_value for item in path.asset_disposals), ZERO)
            + path.financing_result.ending_cash
            - path.financing_result.horizon_senior_claims,
            ZERO,
        ) * path.financing_result.old_shareholder_ownership
        payoff_pv = terminal_payoff / ((ONE + path.equity_required_return) ** horizon)
        retention = path.financing_result.old_shareholder_ownership
    old_shareholder_pv = distribution_pv + payoff_pv
    calculated = {
        "segments": tuple(segment_results),
        "usable_tax_shields": shields,
        "tax_shield_present_value": shield_pv,
        "explicit_financing_cost_present_value": financing_cost_pv,
        "operating_apv": operating_apv,
        "terminal_old_equity_payoff": terminal_payoff,
        "distress_old_shareholder_recovery": distress_recovery,
        "old_shareholder_present_value": old_shareholder_pv,
        "value_per_initial_share": old_shareholder_pv / path.initial_shares,
        "realized_periods": realized_periods,
    }
    path_calculation_hash = sha256(
        json.dumps(
            {
                "contract": "distributional_apv_path/v1",
                "input": _jsonable(asdict(path)),
                "calculated": _jsonable(calculated),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PathAPVResult(
        path_id=path.path_id,
        segments=tuple(segment_results),
        usable_tax_shields=shields,
        tax_shield_present_value=shield_pv,
        explicit_financing_cost_present_value=financing_cost_pv,
        operating_apv=operating_apv,
        distressed=path.financing_result.distressed,
        dilution_occurred=path.financing_result.dilution_occurred,
        old_shareholder_retention=retention,
        terminal_old_equity_payoff=terminal_payoff,
        distress_old_shareholder_recovery=distress_recovery,
        old_shareholder_present_value=old_shareholder_pv,
        value_per_initial_share=old_shareholder_pv / path.initial_shares,
        initial_shares=path.initial_shares,
        distributions_to_old_holders=path.distributions_to_old_holders,
        realized_periods=realized_periods,
        equity_required_return=path.equity_required_return,
        path_calculation_hash=path_calculation_hash,
    )


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def aggregate_equity_distribution(
    *,
    path_results: tuple[PathAPVResult, ...],
    seed_set: tuple[int, ...],
    input_hash: str,
    valuation_distribution_authorized: bool,
) -> EquityValueDistribution:
    """Summarize signed path outputs without report-time flooring."""

    if not valuation_distribution_authorized:
        raise DistributionalAPVError("cannot aggregate an unauthorized valuation distribution")
    if not path_results or not input_hash or not seed_set:
        raise DistributionalAPVError("distribution identity and paths are required")
    if len(seed_set) != len(set(seed_set)):
        raise DistributionalAPVError("distribution seed set contains duplicates")
    path_ids = tuple(item.path_id for item in path_results)
    if len(path_ids) != len(set(path_ids)):
        raise DistributionalAPVError("distribution repeats a path ID")
    values = tuple(item.value_per_initial_share for item in path_results)
    for value in values:
        _decimal(value, "path value per share")
    probabilities = tuple(
        Decimal(value) for value in ("0.10", "0.20", "0.25", "0.50", "0.80", "0.90")
    )
    quantiles = tuple((probability, decimal_quantile(values, probability)) for probability in probabilities)
    distress = tuple(item for item in path_results if item.distressed)
    mean = sum(values, ZERO) / Decimal(len(values))
    distress_probability = Decimal(len(distress)) / Decimal(len(path_results))
    dilution_probability = Decimal(
        sum(1 for item in path_results if item.dilution_occurred)
    ) / Decimal(len(path_results))
    expected_retention = (
        sum((item.old_shareholder_retention for item in distress), ZERO)
        / Decimal(len(distress))
        if distress
        else ZERO
    )
    payload = {
        "contract": "capacity_yield_levered/driver_distributional_apv/v1",
        "input_hash": input_hash,
        "seeds": seed_set,
        "paths": [
            (
                item.path_id,
                str(item.value_per_initial_share),
                item.distressed,
                item.dilution_occurred,
                str(item.old_shareholder_retention),
            )
            for item in path_results
        ],
        "quantiles": [(str(key), str(value)) for key, value in quantiles],
        "mean": str(mean),
    }
    distribution_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EquityValueDistribution(
        quantiles=quantiles,
        mean=mean,
        distress_probability=distress_probability,
        dilution_probability=dilution_probability,
        expected_old_share_retention_in_distress=expected_retention,
        draw_count=len(path_results),
        seed_set=seed_set,
        input_hash=input_hash,
        distribution_hash=distribution_hash,
        valuation_distribution_authorized=True,
        path_values_per_share=values,
    )


def decimal_quantile(values: tuple[Decimal, ...], probability: Decimal) -> Decimal:
    """Type-7 linear quantile using Decimal arithmetic throughout."""

    if not values:
        raise DistributionalAPVError("quantile requires at least one value")
    _decimal(probability, "quantile probability")
    if not ZERO <= probability <= ONE:
        raise DistributionalAPVError("quantile probability must lie within [0,1]")
    ordered = tuple(sorted(values))
    for value in ordered:
        _decimal(value, "quantile value")
    if len(ordered) == 1:
        return ordered[0]
    location = probability * Decimal(len(ordered) - 1)
    lower_index = int(location)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = location - Decimal(lower_index)
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


__all__ = [
    "APVPathInput",
    "NonOperatingAssetDisposal",
    "DistributionalAPVError",
    "EquityValueDistribution",
    "PathAPVResult",
    "SegmentAPVResult",
    "SegmentCashFlowPath",
    "TaxShieldSchedule",
    "aggregate_equity_distribution",
    "calculate_usable_tax_shields",
    "decimal_quantile",
    "evaluate_apv_path",
    "present_value",
]
