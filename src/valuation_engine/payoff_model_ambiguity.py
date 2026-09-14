"""Dated shareholder payoffs under probability and model ambiguity.

An uncalibrated event prior does not support a frequency claim.  It can still
support a decision if each event has an independently valued, dated
shareholder payoff.  This module evaluates the Cartesian product of:

* source-bound probability-vector vertices; and
* complete payoff-model cases (for example, financing-schedule completions).

Because expected present value is linear in probability, the extrema over the
convex hull of the declared probability vectors occur at these vertices.  The
robust entry ceiling is the lowest complete-model expected present value.  It
is not the payoff attached to the smallest probability, and it is never a
claim about the frequency with which an investment return will succeed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .probability_ambiguity import (
    ProbabilityAmbiguityError,
    ProbabilityVector,
    validate_probability_ambiguity_set,
)
from .distributional_apv import PathAPVResult
from .distribution_route_policy import (
    DistributionIntegrationRoute,
    DistributionRouteAuthorization,
    DistributionRouteRequest,
    DistributionRouteStatus,
    authorize_distribution_route,
)


ZERO = Decimal("0")
ONE = Decimal("1")


class PayoffModelAmbiguityError(ValueError):
    """Raised when a payoff-model ambiguity set is incomplete or malformed."""


class PayoffModelAmbiguityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    WITHHELD = "WITHHELD"


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise PayoffModelAmbiguityError(f"{label} must be finite")
    return value


def _return_rate(value: Decimal, label: str) -> Decimal:
    _decimal(value, label)
    if value <= Decimal("-1"):
        raise PayoffModelAmbiguityError(f"{label} must exceed -100%")
    return value


@dataclass(frozen=True)
class RobustEntryPolicy:
    policy_version: str
    horizon_years: int
    required_annual_return: Decimal
    sensitivity_returns: tuple[Decimal, ...]

    def validate(self) -> None:
        if not self.policy_version or self.horizon_years <= 0:
            raise PayoffModelAmbiguityError(
                "robust entry policy requires identity and a positive horizon"
            )
        _return_rate(self.required_annual_return, "required annual return")
        if not self.sensitivity_returns or len(self.sensitivity_returns) != len(
            set(self.sensitivity_returns)
        ):
            raise PayoffModelAmbiguityError(
                "robust entry policy requires distinct sensitivity returns"
            )
        for rate in self.sensitivity_returns:
            _return_rate(rate, "sensitivity return")


@dataclass(frozen=True)
class DatedShareholderCashFlow:
    period: int
    amount_per_share: Decimal

    def validate(self, horizon_years: int) -> None:
        if not 1 <= self.period <= horizon_years:
            raise PayoffModelAmbiguityError(
                "shareholder cash-flow period lies outside the entry horizon"
            )
        if _decimal(self.amount_per_share, "shareholder cash flow") < ZERO:
            raise PayoffModelAmbiguityError(
                "future shareholder cash flow cannot be negative"
            )


@dataclass(frozen=True)
class DatedShareholderPayoffPath:
    branch_id: str
    cash_flows: tuple[DatedShareholderCashFlow, ...]
    payoff_calculation_hash: str

    def validate(self, horizon_years: int) -> None:
        if not self.branch_id or not self.payoff_calculation_hash:
            raise PayoffModelAmbiguityError(
                "dated shareholder payoff requires identity and calculation hash"
            )
        if not self.cash_flows:
            raise PayoffModelAmbiguityError(
                "dated shareholder payoff requires at least one dated cash flow"
            )
        periods = tuple(item.period for item in self.cash_flows)
        if len(periods) != len(set(periods)):
            raise PayoffModelAmbiguityError(
                "dated shareholder payoff repeats a cash-flow period"
            )
        for cash_flow in self.cash_flows:
            cash_flow.validate(horizon_years)

    @property
    def undiscounted_payoff(self) -> Decimal:
        return sum((item.amount_per_share for item in self.cash_flows), ZERO)

    def present_value(self, annual_return: Decimal) -> Decimal:
        _return_rate(annual_return, "annual return")
        return sum(
            (
                item.amount_per_share / ((ONE + annual_return) ** item.period)
                for item in self.cash_flows
            ),
            ZERO,
        )


@dataclass(frozen=True)
class PayoffModelCase:
    """One internally consistent completion of all payoff-model assumptions."""

    model_case_id: str
    payoffs: tuple[DatedShareholderPayoffPath, ...]
    evidence_path_ids: tuple[str, ...]
    model_calculation_hash: str

    def validate(self, branch_ids: tuple[str, ...], horizon_years: int) -> None:
        if (
            not self.model_case_id
            or not self.evidence_path_ids
            or not self.model_calculation_hash
        ):
            raise PayoffModelAmbiguityError(
                "payoff model case requires identity, evidence and calculation hash"
            )
        if len(self.evidence_path_ids) != len(set(self.evidence_path_ids)):
            raise PayoffModelAmbiguityError(
                "payoff model case repeats an evidence path"
            )
        ids = tuple(item.branch_id for item in self.payoffs)
        if len(ids) != len(set(ids)):
            raise PayoffModelAmbiguityError(
                "payoff model case repeats a branch ID"
            )
        if set(ids) != set(branch_ids):
            raise PayoffModelAmbiguityError(
                "every payoff model case must cover the exact branch set"
            )
        for payoff in self.payoffs:
            payoff.validate(horizon_years)
        if self.model_calculation_hash != _model_calculation_hash(
            model_case_id=self.model_case_id,
            payoffs=self.payoffs,
            evidence_path_ids=self.evidence_path_ids,
        ):
            raise PayoffModelAmbiguityError(
                "payoff model case calculation hash does not replay"
            )

    def payoff_map(self) -> dict[str, DatedShareholderPayoffPath]:
        return {item.branch_id: item for item in self.payoffs}


@dataclass(frozen=True)
class PriorModelExpectedPayoff:
    probability_vector_id: str
    payoff_model_case_id: str
    expected_undiscounted_payoff: Decimal
    expected_present_value: Decimal


@dataclass(frozen=True)
class RobustEntrySensitivity:
    required_annual_return: Decimal
    robust_entry_price: Decimal
    binding_probability_vector_id: str
    binding_payoff_model_case_id: str


@dataclass(frozen=True)
class RobustPayoffAmbiguityResult:
    status: PayoffModelAmbiguityStatus
    minimum_expected_present_value: Decimal
    maximum_expected_present_value: Decimal
    robust_entry_price: Decimal | None
    binding_probability_vector_id: str
    binding_payoff_model_case_id: str
    maximum_probability_vector_id: str
    maximum_payoff_model_case_id: str
    combination_results: tuple[PriorModelExpectedPayoff, ...]
    sensitivities: tuple[RobustEntrySensitivity, ...]
    probability_success_claim_authorized: bool
    point_target_authorized: bool
    probability_ambiguity_set_hash: str
    payoff_model_set_hash: str
    source_payoff_hash: str
    calculation_hash: str
    authorization_receipt: str | None
    withheld_reason: str | None


@dataclass(frozen=True)
class PayoffAmbiguityAuditFinding:
    check: str
    passed: bool
    blocking: bool
    detail: str


@dataclass(frozen=True)
class PayoffAmbiguityAuditResult:
    passed: bool
    findings: tuple[PayoffAmbiguityAuditFinding, ...]
    audit_hash: str


def create_payoff_model_case(
    *,
    model_case_id: str,
    payoffs: tuple[DatedShareholderPayoffPath, ...],
    evidence_path_ids: tuple[str, ...],
) -> PayoffModelCase:
    """Seal one complete payoff model so later edits fail hash replay."""

    return PayoffModelCase(
        model_case_id=model_case_id,
        payoffs=payoffs,
        evidence_path_ids=evidence_path_ids,
        model_calculation_hash=_model_calculation_hash(
            model_case_id=model_case_id,
            payoffs=payoffs,
            evidence_path_ids=evidence_path_ids,
        ),
    )


def create_payoff_model_case_from_apv_results(
    *,
    model_case_id: str,
    branch_results: tuple[tuple[str, PathAPVResult], ...],
    evidence_path_ids: tuple[str, ...],
) -> PayoffModelCase:
    """Seal one complete model case directly from evaluated APV paths."""

    if not branch_results:
        raise PayoffModelAmbiguityError("APV payoff model case cannot be empty")
    branch_ids = tuple(branch_id for branch_id, _ in branch_results)
    if len(branch_ids) != len(set(branch_ids)):
        raise PayoffModelAmbiguityError("APV payoff model case repeats a branch")
    return create_payoff_model_case(
        model_case_id=model_case_id,
        payoffs=tuple(
            dated_payoff_from_apv_result(branch_id=branch_id, result=result)
            for branch_id, result in branch_results
        ),
        evidence_path_ids=evidence_path_ids,
    )


def dated_payoff_from_apv_result(
    *,
    branch_id: str,
    result: PathAPVResult,
) -> DatedShareholderPayoffPath:
    """Convert an evaluated APV path into dated per-share legal payoffs.

    A distressed path pays its recovery in the actual distress period.  A
    surviving path pays terminal equity at the declared horizon.  Periodic
    distributions retain their own dates; nothing is collapsed into an
    undated cumulative-dividend field.
    """

    if not branch_id or not result.path_calculation_hash:
        raise PayoffModelAmbiguityError(
            "APV payoff conversion requires branch identity and path receipt"
        )
    if result.realized_periods <= 0 or result.initial_shares <= ZERO:
        raise PayoffModelAmbiguityError("APV payoff timing or shares are invalid")
    amounts = {
        period: amount / result.initial_shares
        for period, amount in enumerate(
            result.distributions_to_old_holders[: result.realized_periods], start=1
        )
    }
    final_payoff = (
        result.distress_old_shareholder_recovery
        if result.distressed
        else result.terminal_old_equity_payoff
    )
    if final_payoff is None or final_payoff < ZERO:
        raise PayoffModelAmbiguityError(
            "APV path lacks a legal non-negative final shareholder payoff"
        )
    amounts[result.realized_periods] = (
        amounts.get(result.realized_periods, ZERO)
        + final_payoff / result.initial_shares
    )
    payoff = DatedShareholderPayoffPath(
        branch_id=branch_id,
        cash_flows=tuple(
            DatedShareholderCashFlow(period, amount)
            for period, amount in sorted(amounts.items())
        ),
        payoff_calculation_hash=result.path_calculation_hash,
    )
    payoff.validate(len(result.distributions_to_old_holders))
    if payoff.present_value(result.equity_required_return) != result.value_per_initial_share:
        raise PayoffModelAmbiguityError(
            "dated shareholder payoff does not reconcile to the APV path value"
        )
    return payoff


def calculate_robust_payoff_ambiguity_entry(
    *,
    payoff_model_cases: tuple[PayoffModelCase, ...],
    probability_vectors: tuple[ProbabilityVector, ...],
    policy: RobustEntryPolicy,
    future_payoffs_authorized: bool,
    source_payoff_hash: str,
) -> RobustPayoffAmbiguityResult:
    """Return an expected-value interval and conservative entry ceiling.

    Each model case must be coherent across all mutually exclusive economic
    branches.  The calculation never assembles a synthetic worst case by
    selecting a different financing assumption in every branch.  Instead it
    evaluates every probability-vector/model-case pair, with each shareholder
    cash flow discounted from its actual declared period.
    """

    policy.validate()
    if not payoff_model_cases or not source_payoff_hash:
        raise PayoffModelAmbiguityError(
            "payoff-model ambiguity requires model cases and a source payoff hash"
        )
    case_ids = tuple(item.model_case_id for item in payoff_model_cases)
    if len(case_ids) != len(set(case_ids)):
        raise PayoffModelAmbiguityError(
            "payoff-model ambiguity contains duplicate model-case IDs"
        )
    first_ids = tuple(item.branch_id for item in payoff_model_cases[0].payoffs)
    if not first_ids:
        raise PayoffModelAmbiguityError("payoff model cases cannot be empty")
    if len(first_ids) != len(set(first_ids)):
        raise PayoffModelAmbiguityError(
            "payoff model case repeats a branch ID"
        )
    branch_ids = tuple(sorted(first_ids))
    for model_case in payoff_model_cases:
        model_case.validate(branch_ids, policy.horizon_years)

    canonical_cases = tuple(
        sorted(payoff_model_cases, key=lambda item: item.model_case_id)
    )
    payoff_vectors = {
        tuple(
            (
                payoff.branch_id,
                tuple(
                    (item.period, str(item.amount_per_share))
                    for item in sorted(payoff.cash_flows, key=lambda item: item.period)
                ),
            )
            for payoff in sorted(model_case.payoffs, key=lambda item: item.branch_id)
        )
        for model_case in canonical_cases
    }
    if len(canonical_cases) > 1 and len(payoff_vectors) != len(canonical_cases):
        raise PayoffModelAmbiguityError(
            "payoff model cases must contain distinct payoff assessments"
        )

    try:
        canonical_priors, prior_hash = validate_probability_ambiguity_set(
            probability_vectors=probability_vectors,
            outcome_ids=branch_ids,
        )
    except ProbabilityAmbiguityError as exc:
        raise PayoffModelAmbiguityError(str(exc)) from exc

    payoff_model_set_hash = _payoff_model_set_hash(canonical_cases)
    combinations = _combination_results(
        probability_vectors=canonical_priors,
        payoff_model_cases=canonical_cases,
        annual_return=policy.required_annual_return,
    )
    minimum = _minimum(combinations)
    maximum = _maximum(combinations)
    sensitivities = tuple(
        _sensitivity(
            probability_vectors=canonical_priors,
            payoff_model_cases=canonical_cases,
            annual_return=rate,
        )
        for rate in policy.sensitivity_returns
    )
    reason: str | None = None
    if not future_payoffs_authorized:
        reason = "FUTURE_PAYOFF_MODELS_NOT_AUTHORIZED"
    elif minimum.expected_present_value <= ZERO:
        reason = "NON_POSITIVE_ROBUST_EXPECTED_PRESENT_VALUE"

    calculation_hash = _calculation_hash(
        combinations=combinations,
        policy=policy,
        prior_hash=prior_hash,
        payoff_model_set_hash=payoff_model_set_hash,
        source_payoff_hash=source_payoff_hash,
        future_payoffs_authorized=future_payoffs_authorized,
        reason=reason,
    )
    receipt = (
        sha256(
            ("future-shareholder-payoff-authorization/v1:" + calculation_hash).encode(
                "utf-8"
            )
        ).hexdigest()
        if reason is None
        else None
    )
    return RobustPayoffAmbiguityResult(
        status=(
            PayoffModelAmbiguityStatus.AVAILABLE
            if reason is None
            else PayoffModelAmbiguityStatus.WITHHELD
        ),
        minimum_expected_present_value=minimum.expected_present_value,
        maximum_expected_present_value=maximum.expected_present_value,
        robust_entry_price=(
            minimum.expected_present_value if reason is None else None
        ),
        binding_probability_vector_id=minimum.probability_vector_id,
        binding_payoff_model_case_id=minimum.payoff_model_case_id,
        maximum_probability_vector_id=maximum.probability_vector_id,
        maximum_payoff_model_case_id=maximum.payoff_model_case_id,
        combination_results=combinations,
        sensitivities=sensitivities if reason is None else (),
        probability_success_claim_authorized=False,
        point_target_authorized=False,
        probability_ambiguity_set_hash=prior_hash,
        payoff_model_set_hash=payoff_model_set_hash,
        source_payoff_hash=source_payoff_hash,
        calculation_hash=calculation_hash,
        authorization_receipt=receipt,
        withheld_reason=reason,
    )


def audit_robust_payoff_ambiguity(
    *,
    result: RobustPayoffAmbiguityResult,
    payoff_model_cases: tuple[PayoffModelCase, ...],
    probability_vectors: tuple[ProbabilityVector, ...],
    policy: RobustEntryPolicy,
    future_payoffs_authorized: bool,
    source_payoff_hash: str,
    route_request: DistributionRouteRequest,
    route_authorization: DistributionRouteAuthorization,
) -> PayoffAmbiguityAuditResult:
    """Replay both the arithmetic and the route authorization before freeze."""

    replay = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=payoff_model_cases,
        probability_vectors=probability_vectors,
        policy=policy,
        future_payoffs_authorized=future_payoffs_authorized,
        source_payoff_hash=source_payoff_hash,
    )
    route_replay = authorize_distribution_route(route_request)
    checks = (
        PayoffAmbiguityAuditFinding(
            "payoff_ambiguity_calculation_replay",
            replay == result,
            True,
            "two-layer dated payoff calculation replays exactly",
        ),
        PayoffAmbiguityAuditFinding(
            "payoff_ambiguity_route_replay",
            route_replay == route_authorization,
            True,
            "distribution route authorization replays exactly",
        ),
        PayoffAmbiguityAuditFinding(
            "payoff_ambiguity_route_scope",
            route_authorization.route
            is DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE
            and route_authorization.status is DistributionRouteStatus.AUTHORIZED
            and route_authorization.expected_value_interval_authorized
            and route_authorization.entry_price_authorized,
            True,
            "prior-ambiguity route authorizes an interval and robust entry",
        ),
        PayoffAmbiguityAuditFinding(
            "payoff_receipt_binding",
            bool(result.authorization_receipt)
            and route_request.future_payoff_authorization_receipt
            == result.authorization_receipt
            and route_request.payoff_horizon_years == policy.horizon_years
            and route_request.payoff_model_case_count == len(payoff_model_cases)
            and route_request.ambiguity_vector_count == len(probability_vectors)
            and route_request.future_shareholder_payoffs_authorized,
            True,
            "route request is bound to the dated payoff receipt, horizon and model count",
        ),
        PayoffAmbiguityAuditFinding(
            "uncalibrated_claim_limits",
            not result.point_target_authorized
            and not result.probability_success_claim_authorized
            and not route_authorization.point_target_authorized
            and not route_authorization.success_probability_claim_authorized,
            True,
            "uncalibrated ambiguity cannot authorize a point target or success probability",
        ),
    )
    passed = all(not item.blocking or item.passed for item in checks)
    payload = {
        "contract": "two_layer_dated_payoff_ambiguity_audit/v1",
        "calculation_hash": result.calculation_hash,
        "authorization_hash": route_authorization.authorization_hash,
        "findings": [
            (item.check, item.passed, item.blocking, item.detail) for item in checks
        ],
    }
    audit_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PayoffAmbiguityAuditResult(
        passed=passed,
        findings=checks,
        audit_hash=audit_hash,
    )


def _combination_results(
    *,
    probability_vectors: tuple[ProbabilityVector, ...],
    payoff_model_cases: tuple[PayoffModelCase, ...],
    annual_return: Decimal,
) -> tuple[PriorModelExpectedPayoff, ...]:
    results: list[PriorModelExpectedPayoff] = []
    for prior in probability_vectors:
        for model_case in payoff_model_cases:
            payoffs = model_case.payoff_map()
            results.append(
                PriorModelExpectedPayoff(
                    probability_vector_id=prior.vector_id,
                    payoff_model_case_id=model_case.model_case_id,
                    expected_undiscounted_payoff=sum(
                        (
                            payoffs[branch_id].undiscounted_payoff * weight
                            for branch_id, weight in prior.weights
                        ),
                        ZERO,
                    ),
                    expected_present_value=sum(
                        (
                            payoffs[branch_id].present_value(annual_return) * weight
                            for branch_id, weight in prior.weights
                        ),
                        ZERO,
                    ),
                )
            )
    return tuple(results)


def _minimum(
    combinations: tuple[PriorModelExpectedPayoff, ...],
) -> PriorModelExpectedPayoff:
    return min(
        combinations,
        key=lambda item: (
            item.expected_present_value,
            item.probability_vector_id,
            item.payoff_model_case_id,
        ),
    )


def _maximum(
    combinations: tuple[PriorModelExpectedPayoff, ...],
) -> PriorModelExpectedPayoff:
    return max(
        combinations,
        key=lambda item: (
            item.expected_present_value,
            item.probability_vector_id,
            item.payoff_model_case_id,
        ),
    )


def _sensitivity(
    *,
    probability_vectors: tuple[ProbabilityVector, ...],
    payoff_model_cases: tuple[PayoffModelCase, ...],
    annual_return: Decimal,
) -> RobustEntrySensitivity:
    binding = _minimum(
        _combination_results(
            probability_vectors=probability_vectors,
            payoff_model_cases=payoff_model_cases,
            annual_return=annual_return,
        )
    )
    return RobustEntrySensitivity(
        required_annual_return=annual_return,
        robust_entry_price=binding.expected_present_value,
        binding_probability_vector_id=binding.probability_vector_id,
        binding_payoff_model_case_id=binding.payoff_model_case_id,
    )


def _payoff_model_set_hash(cases: tuple[PayoffModelCase, ...]) -> str:
    payload = [
        {
            "model_case_id": model_case.model_case_id,
            "payoffs": [
                {
                    "branch_id": payoff.branch_id,
                    "cash_flows": [
                        (item.period, str(item.amount_per_share))
                        for item in sorted(
                            payoff.cash_flows, key=lambda item: item.period
                        )
                    ],
                    "payoff_calculation_hash": payoff.payoff_calculation_hash,
                }
                for payoff in sorted(
                    model_case.payoffs, key=lambda item: item.branch_id
                )
            ],
            "evidence_path_ids": sorted(model_case.evidence_path_ids),
            "model_calculation_hash": model_case.model_calculation_hash,
        }
        for model_case in cases
    ]
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _model_calculation_hash(
    *,
    model_case_id: str,
    payoffs: tuple[DatedShareholderPayoffPath, ...],
    evidence_path_ids: tuple[str, ...],
) -> str:
    payload = {
        "contract": "complete_dated_payoff_model_case/v1",
        "model_case_id": model_case_id,
        "payoffs": [
            {
                "branch_id": payoff.branch_id,
                "cash_flows": [
                    (item.period, str(item.amount_per_share))
                    for item in sorted(payoff.cash_flows, key=lambda item: item.period)
                ],
                "payoff_calculation_hash": payoff.payoff_calculation_hash,
            }
            for payoff in sorted(payoffs, key=lambda item: item.branch_id)
        ],
        "evidence_path_ids": sorted(evidence_path_ids),
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _calculation_hash(
    *,
    combinations: tuple[PriorModelExpectedPayoff, ...],
    policy: RobustEntryPolicy,
    prior_hash: str,
    payoff_model_set_hash: str,
    source_payoff_hash: str,
    future_payoffs_authorized: bool,
    reason: str | None,
) -> str:
    payload = {
        "contract": "two_layer_dated_payoff_ambiguity_entry/v1",
        "combinations": [
            (
                item.probability_vector_id,
                item.payoff_model_case_id,
                str(item.expected_undiscounted_payoff),
                str(item.expected_present_value),
            )
            for item in combinations
        ],
        "policy": {
            "version": policy.policy_version,
            "horizon": policy.horizon_years,
            "required_return": str(policy.required_annual_return),
            "sensitivity_returns": [
                str(value) for value in policy.sensitivity_returns
            ],
        },
        "probability_ambiguity_set_hash": prior_hash,
        "payoff_model_set_hash": payoff_model_set_hash,
        "source_payoff_hash": source_payoff_hash,
        "future_payoffs_authorized": future_payoffs_authorized,
        "reason": reason,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DatedShareholderCashFlow",
    "DatedShareholderPayoffPath",
    "PayoffModelAmbiguityError",
    "PayoffModelAmbiguityStatus",
    "PayoffAmbiguityAuditFinding",
    "PayoffAmbiguityAuditResult",
    "PayoffModelCase",
    "PriorModelExpectedPayoff",
    "RobustEntryPolicy",
    "RobustEntrySensitivity",
    "RobustPayoffAmbiguityResult",
    "calculate_robust_payoff_ambiguity_entry",
    "audit_robust_payoff_ambiguity",
    "create_payoff_model_case",
    "create_payoff_model_case_from_apv_results",
    "dated_payoff_from_apv_result",
]
