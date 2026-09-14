"""Return- and quantile-based entry policy for a frozen payoff distribution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .distributional_apv import decimal_quantile


ZERO = Decimal("0")
ONE = Decimal("1")


class EntryPriceError(ValueError):
    """Raised when an entry policy or payoff set is malformed."""


class EntryPriceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    WITHHELD = "WITHHELD"


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise EntryPriceError(f"{label} must be finite")
    return value


@dataclass(frozen=True)
class EntryPricePolicy:
    policy_version: str
    horizon_years: int
    required_annual_return: Decimal
    success_quantile: Decimal
    sensitivity_returns: tuple[Decimal, ...]

    def validate(self) -> None:
        if not self.policy_version or self.horizon_years <= 0:
            raise EntryPriceError("entry policy identity and horizon are required")
        _decimal(self.required_annual_return, "required annual return")
        if self.required_annual_return <= Decimal("-1"):
            raise EntryPriceError("required annual return must exceed -100%")
        _decimal(self.success_quantile, "success quantile")
        if not ZERO < self.success_quantile < ONE:
            raise EntryPriceError("success quantile must lie within (0,1)")
        if not self.sensitivity_returns or len(set(self.sensitivity_returns)) != len(
            self.sensitivity_returns
        ):
            raise EntryPriceError("entry policy requires distinct sensitivity returns")
        for value in self.sensitivity_returns:
            _decimal(value, "sensitivity return")
            if value <= Decimal("-1"):
                raise EntryPriceError("sensitivity return must exceed -100%")


@dataclass(frozen=True)
class ExitPayoffPath:
    path_id: str
    exit_share_value: Decimal
    cumulative_dividends: Decimal

    def validate(self) -> None:
        if not self.path_id:
            raise EntryPriceError("exit payoff requires a path ID")
        # Exit equity and dividends are legal payoffs and therefore cannot be
        # negative.  Signed present-value diagnostics belong upstream and are
        # never silently floored here.
        if _decimal(self.exit_share_value, "exit share value") < ZERO:
            raise EntryPriceError("exit share value cannot be negative")
        if _decimal(self.cumulative_dividends, "cumulative dividends") < ZERO:
            raise EntryPriceError("cumulative dividends cannot be negative")


@dataclass(frozen=True)
class EntryPriceSensitivity:
    required_annual_return: Decimal
    entry_price: Decimal | None


@dataclass(frozen=True)
class EntryPriceResult:
    status: EntryPriceStatus
    entry_price: Decimal | None
    target_success_probability: Decimal
    realized_success_probability: Decimal | None
    discounted_payoffs: tuple[Decimal, ...]
    sensitivities: tuple[EntryPriceSensitivity, ...]
    policy_version: str
    distribution_hash: str
    calculation_hash: str
    withheld_reason: str | None


def calculate_entry_price(
    *,
    payoffs: tuple[ExitPayoffPath, ...],
    policy: EntryPricePolicy,
    valuation_distribution_authorized: bool,
    distribution_hash: str,
) -> EntryPriceResult:
    """Calculate Q(q) of terminal payoffs discounted at the required return.

    There is intentionally no current-price or Street-price argument.  Those
    comparison objects may only be loaded after this result is frozen.
    """

    policy.validate()
    if not payoffs or not distribution_hash:
        raise EntryPriceError("entry calculation requires payoffs and a distribution hash")
    path_ids = tuple(item.path_id for item in payoffs)
    if len(path_ids) != len(set(path_ids)):
        raise EntryPriceError("entry payoff paths contain duplicate IDs")
    for payoff in payoffs:
        payoff.validate()

    target_success_probability = ONE - policy.success_quantile
    if not valuation_distribution_authorized:
        return _withheld_result(
            payoffs=payoffs,
            policy=policy,
            distribution_hash=distribution_hash,
            target_success_probability=target_success_probability,
            reason="VALUATION_DISTRIBUTION_NOT_AUTHORIZED",
        )

    discounted = _discounted_payoffs(
        payoffs, policy.required_annual_return, policy.horizon_years
    )
    entry = decimal_quantile(discounted, policy.success_quantile)
    sensitivities = tuple(
        EntryPriceSensitivity(
            required_annual_return=rate,
            entry_price=decimal_quantile(
                _discounted_payoffs(payoffs, rate, policy.horizon_years),
                policy.success_quantile,
            ),
        )
        for rate in policy.sensitivity_returns
    )
    if entry <= ZERO:
        return _withheld_result(
            payoffs=payoffs,
            policy=policy,
            distribution_hash=distribution_hash,
            target_success_probability=target_success_probability,
            reason="NON_POSITIVE_ENTRY_QUANTILE",
            discounted_payoffs=discounted,
            sensitivities=sensitivities,
        )
    realized_success = Decimal(sum(value >= entry for value in discounted)) / Decimal(
        len(discounted)
    )
    calculation_hash = _calculation_hash(
        payoffs, policy, distribution_hash, discounted, entry
    )
    return EntryPriceResult(
        status=EntryPriceStatus.AVAILABLE,
        entry_price=entry,
        target_success_probability=target_success_probability,
        realized_success_probability=realized_success,
        discounted_payoffs=discounted,
        sensitivities=sensitivities,
        policy_version=policy.policy_version,
        distribution_hash=distribution_hash,
        calculation_hash=calculation_hash,
        withheld_reason=None,
    )


def _discounted_payoffs(
    payoffs: tuple[ExitPayoffPath, ...], annual_return: Decimal, horizon_years: int
) -> tuple[Decimal, ...]:
    divisor = (ONE + annual_return) ** horizon_years
    return tuple(
        (item.exit_share_value + item.cumulative_dividends) / divisor for item in payoffs
    )


def _withheld_result(
    *,
    payoffs: tuple[ExitPayoffPath, ...],
    policy: EntryPricePolicy,
    distribution_hash: str,
    target_success_probability: Decimal,
    reason: str,
    discounted_payoffs: tuple[Decimal, ...] = (),
    sensitivities: tuple[EntryPriceSensitivity, ...] = (),
) -> EntryPriceResult:
    calculation_hash = _calculation_hash(
        payoffs, policy, distribution_hash, discounted_payoffs, None, reason
    )
    return EntryPriceResult(
        status=EntryPriceStatus.WITHHELD,
        entry_price=None,
        target_success_probability=target_success_probability,
        realized_success_probability=None,
        discounted_payoffs=discounted_payoffs,
        sensitivities=sensitivities,
        policy_version=policy.policy_version,
        distribution_hash=distribution_hash,
        calculation_hash=calculation_hash,
        withheld_reason=reason,
    )


def _calculation_hash(
    payoffs: tuple[ExitPayoffPath, ...],
    policy: EntryPricePolicy,
    distribution_hash: str,
    discounted: tuple[Decimal, ...],
    entry: Decimal | None,
    reason: str | None = None,
) -> str:
    payload = {
        "contract": "return_quantile_entry/v1",
        "payoffs": [
            (item.path_id, str(item.exit_share_value), str(item.cumulative_dividends))
            for item in payoffs
        ],
        "policy": {
            "version": policy.policy_version,
            "horizon": policy.horizon_years,
            "return": str(policy.required_annual_return),
            "quantile": str(policy.success_quantile),
            "sensitivities": [str(value) for value in policy.sensitivity_returns],
        },
        "distribution_hash": distribution_hash,
        "discounted": [str(value) for value in discounted],
        "entry": str(entry) if entry is not None else None,
        "reason": reason,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "EntryPriceError",
    "EntryPricePolicy",
    "EntryPriceResult",
    "EntryPriceSensitivity",
    "EntryPriceStatus",
    "ExitPayoffPath",
    "calculate_entry_price",
]
