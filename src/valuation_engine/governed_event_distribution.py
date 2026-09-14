"""Governed event-prior composition for newly combined leveraged companies.

This is not a substitute for a calibrated operating posterior.  It is the
company-level bridge used when predecessor operating paths are separately
auditable but the current legal perimeter is too new to have its own long
history.  Branch probabilities are explicit analyst priors, never inferred by
assigning observations to the nearest scenario anchor.

The structural-equity calculation prices a dated residual claim on business
assets.  It is a diagnostic unless the strike, asset value, asset volatility,
and maturity structure pass the explicit primary-use qualification gate.  It
therefore cannot be used as a convenient replacement for either
``max(point DCF, 0)`` or a pathwise financing waterfall.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from math import erf, exp, log, sqrt


ZERO = Decimal("0")
ONE = Decimal("1")


class GovernedDistributionError(ValueError):
    """Raised when an event-prior distribution is not decision-auditable."""


class EventProbabilityBasis(str, Enum):
    """Evidence status of branch weights used for distributional claims."""

    CALIBRATED_EVENT_PROBABILITY = "CALIBRATED_EVENT_PROBABILITY"
    GOVERNED_EVENT_PRIOR = "GOVERNED_EVENT_PRIOR"


class StructuralClaimBasis(str, Enum):
    """Time basis of the amount used as the structural-option strike."""

    PROMISED_AT_HORIZON = "PROMISED_AT_HORIZON"
    CURRENT_CARRYING_AMOUNT = "CURRENT_CARRYING_AMOUNT"


class StructuralAssetBasis(str, Enum):
    """Evidence basis for the current business-asset value."""

    MARKET_CALIBRATED = "MARKET_CALIBRATED"
    DCF_DERIVED = "DCF_DERIVED"


class StructuralVolatilityBasis(str, Enum):
    """Evidence basis for asset volatility."""

    CALIBRATED_ASSET_RETURNS = "CALIBRATED_ASSET_RETURNS"
    SCENARIO_ENVELOPE_PROXY = "SCENARIO_ENVELOPE_PROXY"


class StructuralMaturityBasis(str, Enum):
    """How the issuer's liability schedule is represented."""

    SINGLE_MATURITY = "SINGLE_MATURITY"
    VALIDATED_EQUIVALENT_MATURITY = "VALIDATED_EQUIVALENT_MATURITY"
    MULTI_MATURITY_AGGREGATE_PROXY = "MULTI_MATURITY_AGGREGATE_PROXY"


class StructuralModelRole(str, Enum):
    """Maximum decision role authorized for the structural-model output."""

    PRIMARY_VALUE = "PRIMARY_VALUE"
    DIAGNOSTIC_CROSS_CHECK_ONLY = "DIAGNOSTIC_CROSS_CHECK_ONLY"


@dataclass(frozen=True)
class StructuralModelQualification:
    """Separate a structural-credit diagnostic from a primary valuation.

    The Merton strike is a promised payment at the model horizon, not a
    current balance-sheet carrying amount.  Primary use also requires asset
    value and asset volatility calibrated jointly to market observations and
    a liability maturity representation that is genuinely single-date or
    independently validated.  DCF-derived assets, scenario-width volatility,
    and a multi-maturity aggregate can be shown only as diagnostics.
    """

    claim_basis: StructuralClaimBasis
    asset_basis: StructuralAssetBasis
    volatility_basis: StructuralVolatilityBasis
    maturity_basis: StructuralMaturityBasis
    evidence_path_ids: tuple[str, ...]
    permitted_role: StructuralModelRole = StructuralModelRole.PRIMARY_VALUE

    def validate(self) -> None:
        if not self.evidence_path_ids:
            raise GovernedDistributionError(
                "structural-model qualification requires evidence paths"
            )

    @property
    def primary_valuation_authorized(self) -> bool:
        self.validate()
        return (
            self.permitted_role is StructuralModelRole.PRIMARY_VALUE
            and self.claim_basis is StructuralClaimBasis.PROMISED_AT_HORIZON
            and self.asset_basis is StructuralAssetBasis.MARKET_CALIBRATED
            and self.volatility_basis
            is StructuralVolatilityBasis.CALIBRATED_ASSET_RETURNS
            and self.maturity_basis
            in {
                StructuralMaturityBasis.SINGLE_MATURITY,
                StructuralMaturityBasis.VALIDATED_EQUIVALENT_MATURITY,
            }
        )

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.permitted_role is not StructuralModelRole.PRIMARY_VALUE:
            reasons.append("STRUCTURAL_MODEL_ROLE_IS_DIAGNOSTIC_ONLY")
        if self.claim_basis is not StructuralClaimBasis.PROMISED_AT_HORIZON:
            reasons.append("CLAIM_AMOUNT_IS_NOT_PROMISED_AT_HORIZON")
        if self.asset_basis is not StructuralAssetBasis.MARKET_CALIBRATED:
            reasons.append("ASSET_VALUE_IS_NOT_MARKET_CALIBRATED")
        if (
            self.volatility_basis
            is not StructuralVolatilityBasis.CALIBRATED_ASSET_RETURNS
        ):
            reasons.append("ASSET_VOLATILITY_IS_NOT_RETURN_CALIBRATED")
        if self.maturity_basis not in {
            StructuralMaturityBasis.SINGLE_MATURITY,
            StructuralMaturityBasis.VALIDATED_EQUIVALENT_MATURITY,
        }:
            reasons.append("MULTI_MATURITY_CLAIMS_ARE_AGGREGATED")
        return tuple(reasons)


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
    qualification: StructuralModelQualification
    probability_basis: str = EventProbabilityBasis.GOVERNED_EVENT_PRIOR.value
    is_central: bool = False

    def validate(self) -> None:
        if not self.branch_id or not self.evidence_path_ids:
            raise GovernedDistributionError("branch identity and evidence paths are required")
        if self.probability_basis not in {
            item.value for item in EventProbabilityBasis
        }:
            raise GovernedDistributionError(
                "event branches must disclose a recognized probability basis"
            )
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
        self.qualification.validate()
        if (
            self.qualification.claim_basis
            is not StructuralClaimBasis.PROMISED_AT_HORIZON
        ):
            raise GovernedDistributionError(
                "structural strike must be the promised claim amount at the horizon"
            )


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
    """Black-Scholes-Merton residual value using a promised horizon claim.

    This pure calculation may be used for a diagnostic branch.  Primary
    distribution authorization is enforced by
    :func:`compose_governed_equity_distribution`.
    """

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
    qualifications = tuple(branch.qualification for branch in branches)
    if len(set(qualifications)) != 1:
        raise GovernedDistributionError(
            "all structural branches must share one qualification basis"
        )
    qualification = qualifications[0]
    if not qualification.primary_valuation_authorized:
        raise GovernedDistributionError(
            "Merton structural value is diagnostic-only for these inputs: "
            + ",".join(qualification.blocking_reasons)
        )
    probability_bases = {branch.probability_basis for branch in branches}
    if probability_bases != {
        EventProbabilityBasis.CALIBRATED_EVENT_PROBABILITY.value
    }:
        raise GovernedDistributionError(
            "a single governed event prior cannot authorize a probability-weighted "
            "target or quantile success entry; use a source-bound ambiguity set"
        )
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
        "structural_model_qualification": {
            "claim_basis": qualification.claim_basis.value,
            "asset_basis": qualification.asset_basis.value,
            "volatility_basis": qualification.volatility_basis.value,
            "maturity_basis": qualification.maturity_basis.value,
            "evidence_path_ids": qualification.evidence_path_ids,
            "permitted_role": qualification.permitted_role.value,
            "primary_valuation_authorized": True,
        },
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
                "probability_basis": item.probability_basis,
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
        authorization_status=(
            "CALIBRATED_EVENT_PROBABILITY_WITH_QUALIFIED_STRUCTURAL_VALUE"
        ),
        distribution_hash=distribution_hash,
    )
