"""Return- and quantile-based entry policy for a frozen payoff distribution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .distributional_apv import decimal_quantile
from .probability_ambiguity import (
    ProbabilityAmbiguityError,
    ProbabilityVector,
    validate_probability_ambiguity_set,
)


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
    """Legacy horizon payoff used only for calibrated terminal distributions.

    ``cumulative_dividends`` is retained for artifact compatibility but new
    entry calculations reject any non-zero value because it has no payment
    date.  Use ``payoff_model_ambiguity.DatedShareholderPayoffPath`` whenever
    intermediate distributions or early recovery are possible.
    """

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


@dataclass(frozen=True)
class ProbabilityVectorEntry:
    vector_id: str
    expected_terminal_payoff: Decimal
    diagnostic_quantile_payoff: Decimal
    diagnostic_quantile_branch_id: str


@dataclass(frozen=True)
class AmbiguityRobustEntryResult:
    """Entry ceiling robust to a declared set of probability assessments.

    The entry price is the lowest expected payoff across the supplied
    probability set, discounted at the required return.  It is an expected
    return criterion, not a claim that the return succeeds with a calibrated
    frequency.  The ordinary quantile result remains diagnostic and is exposed
    only when every probability vector selects the same supporting branch.
    """

    status: EntryPriceStatus
    entry_price: Decimal | None
    worst_case_expected_payoff: Decimal | None
    best_case_expected_payoff: Decimal | None
    binding_probability_vector_id: str | None
    vector_results: tuple[ProbabilityVectorEntry, ...]
    diagnostic_quantile_stable: bool
    diagnostic_quantile_entry_price: Decimal | None
    probability_success_claim_authorized: bool
    sensitivities: tuple[EntryPriceSensitivity, ...]
    policy_version: str
    distribution_hash: str
    ambiguity_set_hash: str
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
    if any(payoff.cumulative_dividends != ZERO for payoff in payoffs):
        raise EntryPriceError(
            "undated cumulative dividends are forbidden; use dated shareholder cash flows"
        )

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


def calculate_ambiguity_robust_entry_price(
    *,
    payoffs: tuple[ExitPayoffPath, ...],
    probability_vectors: tuple[ProbabilityVector, ...],
    policy: EntryPricePolicy,
    valuation_values_authorized: bool,
    distribution_hash: str,
) -> AmbiguityRobustEntryResult:
    """Retained diagnostic for the former undated single-model contract.

    It still replays expected terminal payoffs and the unstable quantile
    diagnostic for old artifacts, but it cannot authorize a new entry price.
    New work must use ``payoff_model_ambiguity`` so cash flows carry dates and
    financing uncertainty is represented by complete payoff-model cases.
    """

    policy.validate()
    if not payoffs or not distribution_hash:
        raise EntryPriceError(
            "ambiguity-robust entry requires payoffs and a distribution hash"
        )
    payoff_ids = tuple(item.path_id for item in payoffs)
    if len(payoff_ids) != len(set(payoff_ids)):
        raise EntryPriceError("entry payoff branches contain duplicate IDs")
    for payoff in payoffs:
        payoff.validate()
    try:
        probability_vectors, ambiguity_set_hash = (
            validate_probability_ambiguity_set(
                probability_vectors=probability_vectors,
                outcome_ids=payoff_ids,
            )
        )
    except ProbabilityAmbiguityError as exc:
        raise EntryPriceError(str(exc)) from exc

    total_payoffs = {
        item.path_id: item.exit_share_value + item.cumulative_dividends
        for item in payoffs
    }
    vector_results = tuple(
        _probability_vector_entry(
            vector=vector,
            total_payoffs=total_payoffs,
            quantile=policy.success_quantile,
        )
        for vector in probability_vectors
    )
    quantile_branches = {
        item.diagnostic_quantile_branch_id for item in vector_results
    }
    quantile_stable = len(quantile_branches) == 1
    divisor = (ONE + policy.required_annual_return) ** policy.horizon_years
    quantile_entry = (
        vector_results[0].diagnostic_quantile_payoff / divisor
        if quantile_stable
        else None
    )

    if not valuation_values_authorized:
        return _ambiguity_withheld_result(
            payoffs=payoffs,
            vector_results=vector_results,
            policy=policy,
            distribution_hash=distribution_hash,
            ambiguity_set_hash=ambiguity_set_hash,
            quantile_stable=quantile_stable,
            quantile_entry=quantile_entry,
            reason="VALUATION_VALUES_NOT_AUTHORIZED",
        )

    binding = min(
        vector_results,
        key=lambda item: (item.expected_terminal_payoff, item.vector_id),
    )
    best = max(
        vector_results,
        key=lambda item: (item.expected_terminal_payoff, item.vector_id),
    )
    entry = binding.expected_terminal_payoff / divisor
    sensitivities = tuple(
        EntryPriceSensitivity(
            required_annual_return=rate,
            entry_price=binding.expected_terminal_payoff
            / ((ONE + rate) ** policy.horizon_years),
        )
        for rate in policy.sensitivity_returns
    )
    if entry <= ZERO:
        return _ambiguity_withheld_result(
            payoffs=payoffs,
            vector_results=vector_results,
            policy=policy,
            distribution_hash=distribution_hash,
            ambiguity_set_hash=ambiguity_set_hash,
            quantile_stable=quantile_stable,
            quantile_entry=quantile_entry,
            reason="NON_POSITIVE_WORST_CASE_EXPECTED_PAYOFF",
            worst=binding.expected_terminal_payoff,
            best=best.expected_terminal_payoff,
            binding_vector_id=binding.vector_id,
            sensitivities=sensitivities,
        )
    return _ambiguity_withheld_result(
        payoffs=payoffs,
        vector_results=vector_results,
        policy=policy,
        distribution_hash=distribution_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        quantile_stable=quantile_stable,
        quantile_entry=quantile_entry,
        reason="UNDATED_SINGLE_MODEL_ENTRY_RETIRED_USE_DATED_PAYOFF_AMBIGUITY",
        worst=binding.expected_terminal_payoff,
        best=best.expected_terminal_payoff,
        binding_vector_id=binding.vector_id,
        sensitivities=(),
    )


def _probability_vector_entry(
    *,
    vector: ProbabilityVector,
    total_payoffs: dict[str, Decimal],
    quantile: Decimal,
) -> ProbabilityVectorEntry:
    weights = vector.as_map()
    expected = sum(
        (total_payoffs[branch_id] * weights[branch_id] for branch_id in total_payoffs),
        ZERO,
    )
    ordered = sorted(total_payoffs.items(), key=lambda item: (item[1], item[0]))
    cumulative = ZERO
    quantile_branch_id = ordered[-1][0]
    quantile_payoff = ordered[-1][1]
    for branch_id, payoff in ordered:
        cumulative += weights[branch_id]
        if cumulative >= quantile:
            quantile_branch_id = branch_id
            quantile_payoff = payoff
            break
    return ProbabilityVectorEntry(
        vector_id=vector.vector_id,
        expected_terminal_payoff=expected,
        diagnostic_quantile_payoff=quantile_payoff,
        diagnostic_quantile_branch_id=quantile_branch_id,
    )


def _ambiguity_withheld_result(
    *,
    payoffs: tuple[ExitPayoffPath, ...],
    vector_results: tuple[ProbabilityVectorEntry, ...],
    policy: EntryPricePolicy,
    distribution_hash: str,
    ambiguity_set_hash: str,
    quantile_stable: bool,
    quantile_entry: Decimal | None,
    reason: str,
    worst: Decimal | None = None,
    best: Decimal | None = None,
    binding_vector_id: str | None = None,
    sensitivities: tuple[EntryPriceSensitivity, ...] = (),
) -> AmbiguityRobustEntryResult:
    calculation_hash = _ambiguity_calculation_hash(
        payoffs=payoffs,
        vector_results=vector_results,
        policy=policy,
        distribution_hash=distribution_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        entry=None,
        reason=reason,
    )
    return AmbiguityRobustEntryResult(
        status=EntryPriceStatus.WITHHELD,
        entry_price=None,
        worst_case_expected_payoff=worst,
        best_case_expected_payoff=best,
        binding_probability_vector_id=binding_vector_id,
        vector_results=vector_results,
        diagnostic_quantile_stable=quantile_stable,
        diagnostic_quantile_entry_price=quantile_entry,
        probability_success_claim_authorized=False,
        sensitivities=sensitivities,
        policy_version=policy.policy_version,
        distribution_hash=distribution_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        calculation_hash=calculation_hash,
        withheld_reason=reason,
    )


def _ambiguity_calculation_hash(
    *,
    payoffs: tuple[ExitPayoffPath, ...],
    vector_results: tuple[ProbabilityVectorEntry, ...],
    policy: EntryPricePolicy,
    distribution_hash: str,
    ambiguity_set_hash: str,
    entry: Decimal | None,
    reason: str | None,
) -> str:
    payload = {
        "contract": "ambiguity_robust_expected_return_entry/v1",
        "payoffs": [
            (item.path_id, str(item.exit_share_value), str(item.cumulative_dividends))
            for item in payoffs
        ],
        "vectors": [
            (
                item.vector_id,
                str(item.expected_terminal_payoff),
                str(item.diagnostic_quantile_payoff),
                item.diagnostic_quantile_branch_id,
            )
            for item in vector_results
        ],
        "policy": {
            "version": policy.policy_version,
            "horizon": policy.horizon_years,
            "return": str(policy.required_annual_return),
            "quantile": str(policy.success_quantile),
            "sensitivities": [str(value) for value in policy.sensitivity_returns],
        },
        "distribution_hash": distribution_hash,
        "ambiguity_set_hash": ambiguity_set_hash,
        "entry": str(entry) if entry is not None else None,
        "reason": reason,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
    "AmbiguityRobustEntryResult",
    "EntryPriceError",
    "EntryPricePolicy",
    "EntryPriceResult",
    "EntryPriceSensitivity",
    "EntryPriceStatus",
    "ExitPayoffPath",
    "ProbabilityVector",
    "ProbabilityVectorEntry",
    "calculate_ambiguity_robust_entry_price",
    "calculate_entry_price",
]
