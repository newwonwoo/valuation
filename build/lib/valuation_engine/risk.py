from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from statistics import fmean, variance


class BetaLevelName(str, Enum):
    L1_BROAD_SECTOR = "L1_BROAD_SECTOR"
    L2_INDUSTRY = "L2_INDUSTRY"
    L3_RISK_DRIVER_SUBINDUSTRY = "L3_RISK_DRIVER_SUBINDUSTRY"
    L4_ECONOMIC_TWINS = "L4_ECONOMIC_TWINS"


BETA_LEVEL_ORDER = (
    BetaLevelName.L1_BROAD_SECTOR,
    BetaLevelName.L2_INDUSTRY,
    BetaLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
    BetaLevelName.L4_ECONOMIC_TWINS,
)


def blume_adjust_beta(raw_beta: float, *, weight_on_raw: float = 2.0 / 3.0, long_run_mean: float = 1.0) -> float:
    if not all(isfinite(v) for v in (raw_beta, weight_on_raw, long_run_mean)):
        raise ValueError("Blume inputs must be finite")
    if not 0 <= weight_on_raw <= 1:
        raise ValueError("weight_on_raw must be in [0, 1]")
    return weight_on_raw * raw_beta + (1.0 - weight_on_raw) * long_run_mean


def vasicek_adjust_beta(
    raw_beta: float,
    *,
    raw_variance: float,
    prior_mean: float,
    prior_variance: float,
) -> float:
    if not all(isfinite(v) for v in (raw_beta, raw_variance, prior_mean, prior_variance)):
        raise ValueError("Vasicek inputs must be finite")
    if raw_variance <= 0 or prior_variance <= 0:
        raise ValueError("Vasicek variances must be positive")
    posterior_variance = 1.0 / (1.0 / raw_variance + 1.0 / prior_variance)
    return posterior_variance * (raw_beta / raw_variance + prior_mean / prior_variance)


@dataclass(frozen=True)
class PeerBetaInput:
    peer_id: str
    levered_beta: float
    debt: float
    equity: float
    tax_rate: float
    beta_standard_error: float | None = None
    estimation_method: str = "regression_or_adjusted"

    def __post_init__(self) -> None:
        if not self.peer_id:
            raise ValueError("peer_id is required")
        if not isfinite(self.levered_beta) or self.levered_beta <= 0:
            raise ValueError("levered_beta must be finite and positive")
        if not isfinite(self.debt) or self.debt < 0:
            raise ValueError("debt must be finite and non-negative")
        if not isfinite(self.equity) or self.equity <= 0:
            raise ValueError("equity must be finite and positive")
        if not isfinite(self.tax_rate) or not 0 <= self.tax_rate < 1:
            raise ValueError("tax_rate must be in [0, 1)")
        if self.beta_standard_error is not None and (
            not isfinite(self.beta_standard_error) or self.beta_standard_error <= 0
        ):
            raise ValueError("beta_standard_error must be finite and positive when supplied")
        if not self.estimation_method:
            raise ValueError("estimation_method is required")

    @property
    def leverage_factor(self) -> float:
        return 1.0 + (1.0 - self.tax_rate) * self.debt / self.equity

    @property
    def asset_beta(self) -> float:
        return self.levered_beta / self.leverage_factor

    @property
    def asset_beta_standard_error(self) -> float | None:
        if self.beta_standard_error is None:
            return None
        return self.beta_standard_error / self.leverage_factor


@dataclass(frozen=True)
class BetaLevel:
    level: BetaLevelName
    peers: tuple[PeerBetaInput, ...]

    def __post_init__(self) -> None:
        if not self.peers:
            raise ValueError(f"{self.level.value} requires at least one peer")
        peer_ids = [peer.peer_id for peer in self.peers]
        if len(peer_ids) != len(set(peer_ids)):
            raise ValueError(f"duplicate peer_id inside {self.level.value}")


@dataclass(frozen=True)
class BetaUpdate:
    level: BetaLevelName
    sample_size: int
    group_mean_asset_beta: float
    group_dispersion_variance: float
    measurement_variance: float
    likelihood_variance: float
    prior_mean: float
    prior_variance: float
    posterior_mean: float
    posterior_variance: float


@dataclass(frozen=True)
class HierarchicalBetaEstimate:
    asset_beta: float
    posterior_variance: float
    updates: tuple[BetaUpdate, ...]


def unlever_beta(
    levered_beta: float,
    *,
    debt: float,
    equity: float,
    tax_rate: float,
) -> float:
    _validate_capital_structure(levered_beta, debt, equity, tax_rate)
    return levered_beta / (1.0 + (1.0 - tax_rate) * debt / equity)


def relever_beta(
    asset_beta: float,
    *,
    debt: float,
    equity: float,
    tax_rate: float,
) -> float:
    _validate_capital_structure(asset_beta, debt, equity, tax_rate)
    return asset_beta * (1.0 + (1.0 - tax_rate) * debt / equity)


def hierarchical_partial_pool(
    levels: tuple[BetaLevel, ...],
    *,
    variance_floor: float = 1e-4,
    singleton_uncertainty_multiplier: float = 4.0,
) -> HierarchicalBetaEstimate:
    """Sequential Normal-Normal partial pooling for L1→L4 asset beta."""
    if tuple(level.level for level in levels) != BETA_LEVEL_ORDER:
        raise ValueError("beta hierarchy must be exactly L1→L2→L3→L4")
    if variance_floor <= 0 or not isfinite(variance_floor):
        raise ValueError("variance_floor must be finite and positive")
    if singleton_uncertainty_multiplier <= 1:
        raise ValueError("singleton_uncertainty_multiplier must exceed 1")

    l1 = levels[0]
    l1_values = tuple(peer.asset_beta for peer in l1.peers)
    prior_mean = fmean(l1_values)
    prior_variance = max(_sample_variance(l1_values, variance_floor), variance_floor)
    l1_measurement = _measurement_variance(l1.peers)
    updates: list[BetaUpdate] = [
        BetaUpdate(
            level=l1.level,
            sample_size=len(l1_values),
            group_mean_asset_beta=prior_mean,
            group_dispersion_variance=prior_variance,
            measurement_variance=l1_measurement,
            likelihood_variance=prior_variance + l1_measurement,
            prior_mean=prior_mean,
            prior_variance=prior_variance,
            posterior_mean=prior_mean,
            posterior_variance=prior_variance,
        )
    ]

    for level in levels[1:]:
        values = tuple(peer.asset_beta for peer in level.peers)
        group_mean = fmean(values)
        dispersion = _sample_variance(values, prior_variance)
        measurement = _measurement_variance(level.peers)
        if len(values) == 1:
            likelihood_variance = max(
                prior_variance * singleton_uncertainty_multiplier + measurement,
                variance_floor,
            )
        else:
            likelihood_variance = max(dispersion / len(values) + measurement / len(values), variance_floor)

        posterior_variance = 1.0 / (1.0 / prior_variance + 1.0 / likelihood_variance)
        posterior_mean = posterior_variance * (
            prior_mean / prior_variance + group_mean / likelihood_variance
        )
        updates.append(
            BetaUpdate(
                level=level.level,
                sample_size=len(values),
                group_mean_asset_beta=group_mean,
                group_dispersion_variance=dispersion,
                measurement_variance=measurement,
                likelihood_variance=likelihood_variance,
                prior_mean=prior_mean,
                prior_variance=prior_variance,
                posterior_mean=posterior_mean,
                posterior_variance=posterior_variance,
            )
        )
        prior_mean = posterior_mean
        prior_variance = posterior_variance

    return HierarchicalBetaEstimate(prior_mean, prior_variance, tuple(updates))


def _measurement_variance(peers: tuple[PeerBetaInput, ...]) -> float:
    variances = tuple(
        peer.asset_beta_standard_error**2
        for peer in peers
        if peer.asset_beta_standard_error is not None
    )
    return fmean(variances) if variances else 0.0


def _sample_variance(values: tuple[float, ...], fallback: float) -> float:
    if len(values) < 2:
        return fallback
    value = variance(values)
    if not isfinite(value) or value <= 0:
        return fallback
    return value


def _validate_capital_structure(
    beta: float,
    debt: float,
    equity: float,
    tax_rate: float,
) -> None:
    if not isfinite(beta) or beta <= 0:
        raise ValueError("beta must be finite and positive")
    if not isfinite(debt) or debt < 0:
        raise ValueError("debt must be finite and non-negative")
    if not isfinite(equity) or equity <= 0:
        raise ValueError("equity must be finite and positive")
    if not isfinite(tax_rate) or not 0 <= tax_rate < 1:
        raise ValueError("tax_rate must be in [0, 1)")
