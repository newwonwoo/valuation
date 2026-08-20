from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import exp, isfinite, log
from statistics import fmean, variance


class PERLevelName(str, Enum):
    L1_BROAD_SECTOR = "L1_BROAD_SECTOR"
    L2_INDUSTRY = "L2_INDUSTRY"
    L3_RISK_DRIVER_SUBINDUSTRY = "L3_RISK_DRIVER_SUBINDUSTRY"
    L4_ECONOMIC_TWINS = "L4_ECONOMIC_TWINS"


PER_LEVEL_ORDER = (
    PERLevelName.L1_BROAD_SECTOR,
    PERLevelName.L2_INDUSTRY,
    PERLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
    PERLevelName.L4_ECONOMIC_TWINS,
)


@dataclass(frozen=True)
class FundamentalPERAssumptions:
    normalized_forward_eps: float
    explicit_growth_rates: tuple[float, ...]
    fcfe_conversion_rates: tuple[float, ...]
    cost_of_equity: float
    terminal_growth: float
    terminal_roe: float

    def __post_init__(self) -> None:
        if not isfinite(self.normalized_forward_eps) or self.normalized_forward_eps <= 0:
            raise ValueError("PER requires positive normalized forward EPS")
        if not isfinite(self.cost_of_equity) or self.cost_of_equity <= 0:
            raise ValueError("cost_of_equity must be finite and positive")
        if not isfinite(self.terminal_growth) or not isfinite(self.terminal_roe):
            raise ValueError("terminal assumptions must be finite")
        if self.cost_of_equity <= self.terminal_growth:
            raise ValueError("cost_of_equity must exceed terminal growth")
        if self.terminal_roe <= 0:
            raise ValueError("terminal ROE must be positive")
        if len(self.fcfe_conversion_rates) != len(self.explicit_growth_rates) + 1:
            raise ValueError("fcfe_conversion_rates must cover EPS1 plus every explicit growth year")
        for growth in self.explicit_growth_rates:
            if not isfinite(growth) or growth <= -1:
                raise ValueError("explicit growth rates must be finite and greater than -100%")
        for conversion in self.fcfe_conversion_rates:
            if not isfinite(conversion) or not 0 <= conversion <= 1.5:
                raise ValueError("FCFE/EPS conversion must be finite and in [0, 1.5]")
        terminal_payout = 1.0 - self.terminal_growth / self.terminal_roe
        if terminal_payout < 0 or terminal_payout > 1.5:
            raise ValueError("terminal payout implied by g/ROE is not economically valid")


@dataclass(frozen=True)
class FundamentalPERResult:
    forward_per: float
    implied_price: float
    terminal_payout_ratio: float
    explicit_eps_path: tuple[float, ...]


def fundamental_forward_per(inputs: FundamentalPERAssumptions) -> FundamentalPERResult:
    eps_path = [inputs.normalized_forward_eps]
    for growth in inputs.explicit_growth_rates:
        eps_path.append(eps_path[-1] * (1.0 + growth))

    price = 0.0
    for year, (eps, conversion) in enumerate(zip(eps_path, inputs.fcfe_conversion_rates), start=1):
        fcfe = eps * conversion
        price += fcfe / (1.0 + inputs.cost_of_equity) ** year

    terminal_payout = 1.0 - inputs.terminal_growth / inputs.terminal_roe
    terminal_eps_next = eps_path[-1] * (1.0 + inputs.terminal_growth)
    terminal_fcfe_next = terminal_eps_next * terminal_payout
    terminal_value = terminal_fcfe_next / (inputs.cost_of_equity - inputs.terminal_growth)
    price += terminal_value / (1.0 + inputs.cost_of_equity) ** len(eps_path)
    return FundamentalPERResult(
        forward_per=price / inputs.normalized_forward_eps,
        implied_price=price,
        terminal_payout_ratio=terminal_payout,
        explicit_eps_path=tuple(eps_path),
    )


@dataclass(frozen=True)
class EconomicAssumptionFingerprint:
    growth_rates: tuple[float, ...]
    margin_path: tuple[float, ...]
    reinvestment_path: tuple[float, ...]
    growth_duration_years: int


def validate_dcf_per_assumption_consistency(
    dcf: EconomicAssumptionFingerprint,
    per: EconomicAssumptionFingerprint,
    *,
    tolerance: float = 1e-9,
) -> None:
    if dcf.growth_duration_years != per.growth_duration_years:
        raise ValueError("DCF-PER growth duration mismatch")
    for label, left, right in (
        ("growth", dcf.growth_rates, per.growth_rates),
        ("margin", dcf.margin_path, per.margin_path),
        ("reinvestment", dcf.reinvestment_path, per.reinvestment_path),
    ):
        if len(left) != len(right) or any(abs(a - b) > tolerance for a, b in zip(left, right)):
            raise ValueError(f"DCF-PER {label} assumption mismatch")


@dataclass(frozen=True)
class PeerPERInput:
    peer_id: str
    market_forward_per: float
    fundamental_forward_per: float

    def __post_init__(self) -> None:
        if not self.peer_id:
            raise ValueError("peer_id is required")
        if not isfinite(self.market_forward_per) or self.market_forward_per <= 0:
            raise ValueError("market_forward_per must be finite and positive")
        if not isfinite(self.fundamental_forward_per) or self.fundamental_forward_per <= 0:
            raise ValueError("fundamental_forward_per must be finite and positive")

    @property
    def log_residual_premium(self) -> float:
        return log(self.market_forward_per / self.fundamental_forward_per)


@dataclass(frozen=True)
class PERLevel:
    level: PERLevelName
    peers: tuple[PeerPERInput, ...]

    def __post_init__(self) -> None:
        if not self.peers:
            raise ValueError(f"{self.level.value} requires at least one peer")


@dataclass(frozen=True)
class ResidualUpdate:
    level: PERLevelName
    sample_size: int
    group_mean: float
    likelihood_variance: float
    posterior_mean: float
    posterior_variance: float


@dataclass(frozen=True)
class ResidualPremiumEstimate:
    log_residual_premium: float
    premium_multiplier: float
    posterior_variance: float
    updates: tuple[ResidualUpdate, ...]


def hierarchical_residual_pool(
    levels: tuple[PERLevel, ...],
    *,
    variance_floor: float = 1e-4,
    singleton_uncertainty_multiplier: float = 4.0,
) -> ResidualPremiumEstimate:
    if tuple(level.level for level in levels) != PER_LEVEL_ORDER:
        raise ValueError("PER hierarchy must be exactly L1→L2→L3→L4")
    l1_values = tuple(peer.log_residual_premium for peer in levels[0].peers)
    prior_mean = fmean(l1_values)
    prior_variance = max(_sample_variance(l1_values, variance_floor), variance_floor)
    updates = [ResidualUpdate(levels[0].level, len(l1_values), prior_mean, prior_variance, prior_mean, prior_variance)]

    for level in levels[1:]:
        values = tuple(peer.log_residual_premium for peer in level.peers)
        group_mean = fmean(values)
        dispersion = _sample_variance(values, prior_variance)
        if len(values) == 1:
            likelihood_variance = max(prior_variance * singleton_uncertainty_multiplier, variance_floor)
        else:
            likelihood_variance = max(dispersion / len(values), variance_floor)
        posterior_variance = 1.0 / (1.0 / prior_variance + 1.0 / likelihood_variance)
        posterior_mean = posterior_variance * (
            prior_mean / prior_variance + group_mean / likelihood_variance
        )
        updates.append(
            ResidualUpdate(level.level, len(values), group_mean, likelihood_variance, posterior_mean, posterior_variance)
        )
        prior_mean, prior_variance = posterior_mean, posterior_variance

    return ResidualPremiumEstimate(prior_mean, exp(prior_mean), prior_variance, tuple(updates))


@dataclass(frozen=True)
class HierarchicalWarrantedPER:
    core_fundamental_per: float
    expansion_adjusted_fundamental_per: float | None
    market_realization_per: float | None
    residual_premium_multiplier: float | None


def build_hierarchical_warranted_per(
    core: FundamentalPERAssumptions,
    *,
    expansion: FundamentalPERAssumptions | None = None,
    expansion_is_committed_or_preinvested: bool = False,
    residual_levels: tuple[PERLevel, ...] | None = None,
) -> HierarchicalWarrantedPER:
    core_value = fundamental_forward_per(core).forward_per
    expansion_value: float | None = None
    if expansion is not None:
        if not expansion_is_committed_or_preinvested:
            raise ValueError("Expansion-Adjusted PER requires committed/pre-invested capacity evidence")
        expansion_value = fundamental_forward_per(expansion).forward_per

    residual_multiplier: float | None = None
    market_realization: float | None = None
    if residual_levels is not None:
        pooled = hierarchical_residual_pool(residual_levels)
        residual_multiplier = pooled.premium_multiplier
        base = expansion_value if expansion_value is not None else core_value
        market_realization = base * residual_multiplier

    return HierarchicalWarrantedPER(core_value, expansion_value, market_realization, residual_multiplier)


def _sample_variance(values: tuple[float, ...], fallback: float) -> float:
    if len(values) < 2:
        return fallback
    value = variance(values)
    if not isfinite(value) or value <= 0:
        return fallback
    return value
