"""Governed event-prior composition for newly combined leveraged companies.

This is not a substitute for a calibrated operating posterior.  It is the
company-level bridge used when predecessor operating paths are separately
auditable but the current legal perimeter is too new to have its own long
history.  Branch probabilities are explicit analyst priors, never inferred by
assigning observations to the nearest scenario anchor.

The structural-equity calculation prices the dated residual claim on business
assets.  It therefore does not apply ``max(point DCF, 0)`` in a report layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from math import erf, exp, log, sqrt


ZERO = Decimal("0")
ONE = Decimal("1")


class GovernedDistributionError(ValueError):
    """Raised when an event-prior distribution is not decision-auditable."""


def _finite(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise GovernedDistributionError(f"{label} must be finite")
    return value


def _normal_cdf(value: float) -> float:
    return (1.0 + erf(value / sqrt(2.0))) / 2.0


@dataclass(frozen=True)
class StructuralEquityBranch:
    branch_id: str
    probability: Decimal
    business_asset_value: Decimal
    senior_claim_value: Decimal
    annual_asset_volatility: Decimal
    risk_free_rate: Decimal
    claim_horizon_years: Decimal
    evidence_path_ids: tuple[str, ...]
    probability_basis: str = "GOVERNED_EVENT_PRIOR"
    is_central: bool = False

    def validate(self) -> None:
        if not self.branch_id or not self.evidence_path_ids:
            raise GovernedDistributionError("branch identity and evidence paths are required")
        if self.probability_basis != "GOVERNED_EVENT_PRIOR":
            raise GovernedDistributionError("event branches must disclose governed-prior status")
        if not ZERO < _finite(self.probability, "branch probability") < ONE:
            raise GovernedDistributionError("branch probability must lie within (0,1)")
        if _finite(self.business_asset_value, "business asset value") <= ZERO:
            raise GovernedDistributionError("business asset value must be positive")
        if _finite(self.senior_claim_value, "senior claim value") <= ZERO:
            raise GovernedDistributionError("senior claim value must be positive")
        if _finite(self.annual_asset_volatility, "asset volatility") <= ZERO:
            raise GovernedDistributionError("asset volatility must be positive")
        if not ZERO <= _finite(self.risk_free_rate, "risk-free rate") < ONE:
            raise GovernedDistributionError("risk-free rate lies outside [0,1)")
        if _finite(self.claim_horizon_years, "claim horizon") <= ZERO:
            raise GovernedDistributionError("claim horizon must be positive")


@dataclass(frozen=True)
class GovernedEquityDistribution:
    branch_values_per_share: tuple[tuple[str, Decimal, Decimal], ...]
    p10: Decimal
    p20: Decimal
    p50: Decimal
    p80: Decimal
    p90: Decimal
    mean: Decimal
    entry_price: Decimal
    entry_price_sensitivities: tuple[tuple[Decimal, Decimal], ...]
    entry_quantile: Decimal
    target_success_probability: Decimal
    realized_success_probability: Decimal
    authorization_status: str
    distribution_hash: str


def structural_equity_value(branch: StructuralEquityBranch) -> Decimal:
    """Black-Scholes-Merton residual-claim value for one explicit branch."""

    branch.validate()
    assets = float(branch.business_asset_value)
    claims = float(branch.senior_claim_value)
    volatility = float(branch.annual_asset_volatility)
    rate = float(branch.risk_free_rate)
    horizon = float(branch.claim_horizon_years)
    root_horizon = sqrt(horizon)
    d1 = (
        log(assets / claims) + (rate + volatility * volatility / 2.0) * horizon
    ) / (volatility * root_horizon)
    d2 = d1 - volatility * root_horizon
    value = assets * _normal_cdf(d1) - claims * exp(-rate * horizon) * _normal_cdf(d2)
    if value < 0:
        raise GovernedDistributionError("structural equity formula produced a negative value")
    return Decimal(str(value))


def weighted_quantile(
    values: tuple[tuple[Decimal, Decimal], ...], probability: Decimal
) -> Decimal:
    if not values or not ZERO <= probability <= ONE:
        raise GovernedDistributionError("weighted quantile inputs are invalid")
    ordered = sorted(values, key=lambda item: item[0])
    cumulative = ZERO
    for value, weight in ordered:
        _finite(value, "weighted value")
        if weight < ZERO:
            raise GovernedDistributionError("quantile weight cannot be negative")
        cumulative += weight
        if cumulative >= probability:
            return value
    if cumulative != ONE:
        raise GovernedDistributionError("quantile weights must sum to one")
    return ordered[-1][0]


def compose_governed_equity_distribution(
    *,
    branches: tuple[StructuralEquityBranch, ...],
    diluted_shares: Decimal,
    entry_horizon_years: int,
    required_annual_return: Decimal,
    entry_quantile: Decimal,
    sensitivity_returns: tuple[Decimal, ...],
    source_bridge_hash: str,
) -> GovernedEquityDistribution:
    """Compose explicit branch priors into value and return-threshold outputs.

    The central branch must be uniquely most probable.  This prevents a repeat
    of the old nearest-anchor result in which the so-called base case had the
    smallest probability merely because it occupied a bounded Voronoi cell.
    """

    if not branches or not source_bridge_hash:
        raise GovernedDistributionError("branches and source bridge hash are required")
    if _finite(diluted_shares, "diluted shares") <= ZERO:
        raise GovernedDistributionError("diluted shares must be positive")
    if entry_horizon_years <= 0:
        raise GovernedDistributionError("entry horizon must be positive")
    if required_annual_return <= Decimal("-1"):
        raise GovernedDistributionError("required return must exceed -100%")
    if not ZERO < entry_quantile < ONE:
        raise GovernedDistributionError("entry quantile must lie within (0,1)")
    if not sensitivity_returns:
        raise GovernedDistributionError("entry sensitivity returns are required")
    ids = tuple(item.branch_id for item in branches)
    if len(ids) != len(set(ids)):
        raise GovernedDistributionError("branch IDs must be unique")
    for branch in branches:
        branch.validate()
    if sum((item.probability for item in branches), ZERO) != ONE:
        raise GovernedDistributionError("branch probabilities must sum exactly to one")
    central = tuple(item for item in branches if item.is_central)
    largest = max(item.probability for item in branches)
    if (
        len(central) != 1
        or central[0].probability != largest
        or sum(item.probability == largest for item in branches) != 1
    ):
        raise GovernedDistributionError("one central branch must be uniquely most probable")

    per_share = tuple(
        (
            branch.branch_id,
            structural_equity_value(branch) / diluted_shares,
            branch.probability,
        )
        for branch in branches
    )
    weighted = tuple((value, probability) for _, value, probability in per_share)
    mean = sum((value * probability for _, value, probability in per_share), ZERO)
    p10 = weighted_quantile(weighted, Decimal("0.10"))
    p20 = weighted_quantile(weighted, Decimal("0.20"))
    p50 = weighted_quantile(weighted, Decimal("0.50"))
    p80 = weighted_quantile(weighted, Decimal("0.80"))
    p90 = weighted_quantile(weighted, Decimal("0.90"))

    def entry_for(return_rate: Decimal) -> Decimal:
        if return_rate <= Decimal("-1"):
            raise GovernedDistributionError("sensitivity return must exceed -100%")
        discounted = tuple(
            (
                value / ((ONE + return_rate) ** entry_horizon_years),
                probability,
            )
            for _, value, probability in per_share
        )
        return weighted_quantile(discounted, entry_quantile)

    entry = entry_for(required_annual_return)
    discounted_at_policy = tuple(
        (
            value / ((ONE + required_annual_return) ** entry_horizon_years),
            probability,
        )
        for _, value, probability in per_share
    )
    realized_success = sum(
        (probability for value, probability in discounted_at_policy if value >= entry),
        ZERO,
    )
    sensitivities = tuple((rate, entry_for(rate)) for rate in sensitivity_returns)
    payload = {
        "contract": "capacity_yield_levered/governed_event_distribution/v1",
        "source_bridge_hash": source_bridge_hash,
        "branches": [
            {
                "id": item.branch_id,
                "probability": str(item.probability),
                "business_asset_value": str(item.business_asset_value),
                "senior_claim_value": str(item.senior_claim_value),
                "annual_asset_volatility": str(item.annual_asset_volatility),
                "risk_free_rate": str(item.risk_free_rate),
                "claim_horizon_years": str(item.claim_horizon_years),
                "evidence_path_ids": item.evidence_path_ids,
                "is_central": item.is_central,
                "equity_value_per_share": str(value),
            }
            for item, (_, value, _) in zip(branches, per_share)
        ],
        "diluted_shares": str(diluted_shares),
        "p10": str(p10),
        "p20": str(p20),
        "p50": str(p50),
        "p80": str(p80),
        "p90": str(p90),
        "mean": str(mean),
        "entry": str(entry),
        "entry_horizon_years": entry_horizon_years,
        "required_annual_return": str(required_annual_return),
        "entry_quantile": str(entry_quantile),
        "realized_success_probability": str(realized_success),
        "sensitivities": [(str(rate), str(value)) for rate, value in sensitivities],
    }
    distribution_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return GovernedEquityDistribution(
        branch_values_per_share=per_share,
        p10=p10,
        p20=p20,
        p50=p50,
        p80=p80,
        p90=p90,
        mean=mean,
        entry_price=entry,
        entry_price_sensitivities=sensitivities,
        entry_quantile=entry_quantile,
        target_success_probability=ONE - entry_quantile,
        realized_success_probability=realized_success,
        authorization_status="GOVERNED_EVENT_PRIOR_WITH_AUDITED_SCENARIO_VALUE_BRIDGE",
        distribution_hash=distribution_hash,
    )
