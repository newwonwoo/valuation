"""Authorization boundary for value-distribution integration routes.

The economic model, not a company name or a scenario label, selects one of
three routes:

* a calibrated continuous driver distribution is valued path by path;
* an uncalibrated but source-bound set of event priors produces an expected-
  value interval; or
* the historical nearest-anchor quantizer is replayed for audit only.

This module does not calculate values. It prevents a diagnostic probability
artifact from silently acquiring authority over a target, entry price or
success-probability claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json


class DistributionRouteError(ValueError):
    """Raised when a route request contradicts its evidence basis."""


class DistributionIntegrationRoute(str, Enum):
    PATHWISE_VALUE_DISTRIBUTION = "PATHWISE_VALUE_DISTRIBUTION"
    PRIOR_AMBIGUITY_VALUE_RANGE = "PRIOR_AMBIGUITY_VALUE_RANGE"
    LEGACY_NEAREST_ANCHOR_REPLAY = "LEGACY_NEAREST_ANCHOR_REPLAY"


class DistributionRouteStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    CONDITIONAL = "CONDITIONAL"
    REPLAY_ONLY = "REPLAY_ONLY"
    BLOCKED = "BLOCKED"


NO_SCENARIO_ASSIGNMENT = "CONTINUOUS_PATHS_REMAIN_UNASSIGNED"
LEGACY_NEAREST_ASSIGNMENT = "NEAREST_PREDECLARED_ECONOMIC_SCENARIO_PATH"


@dataclass(frozen=True)
class DistributionRouteRequest:
    route: DistributionIntegrationRoute
    economic_archetypes: tuple[str, ...]
    scenario_assignment_method: str
    evidence_path_ids: tuple[str, ...]
    calibrated_driver_distribution: bool = False
    financing_waterfall_authorized: bool = False
    signed_values_authorized: bool = False
    ambiguity_set_validated: bool = False
    ambiguity_vector_count: int = 0
    future_shareholder_payoffs_authorized: bool = False
    future_payoff_authorization_receipt: str | None = None
    payoff_horizon_years: int | None = None
    payoff_model_case_count: int = 0
    frozen_legacy_replay_receipt: str | None = None

    def validate(self) -> None:
        if not self.economic_archetypes or not all(self.economic_archetypes):
            raise DistributionRouteError("distribution route requires economic archetypes")
        if len(self.economic_archetypes) != len(set(self.economic_archetypes)):
            raise DistributionRouteError("distribution route repeats an economic archetype")
        if not self.scenario_assignment_method or not self.evidence_path_ids:
            raise DistributionRouteError(
                "distribution route requires an assignment declaration and evidence paths"
            )
        if len(self.evidence_path_ids) != len(set(self.evidence_path_ids)):
            raise DistributionRouteError("distribution route repeats an evidence path")
        if self.ambiguity_vector_count < 0:
            raise DistributionRouteError("ambiguity vector count cannot be negative")
        if self.payoff_model_case_count < 0:
            raise DistributionRouteError("payoff model case count cannot be negative")
        if self.payoff_horizon_years is not None and self.payoff_horizon_years <= 0:
            raise DistributionRouteError("payoff horizon must be positive")


@dataclass(frozen=True)
class DistributionRouteAuthorization:
    route: DistributionIntegrationRoute
    status: DistributionRouteStatus
    pathwise_value_distribution_authorized: bool
    expected_value_interval_authorized: bool
    point_target_authorized: bool
    entry_price_authorized: bool
    success_probability_claim_authorized: bool
    diagnostic_only: bool
    blocking_reasons: tuple[str, ...]
    authorization_hash: str


def authorize_distribution_route(
    request: DistributionRouteRequest,
) -> DistributionRouteAuthorization:
    """Authorize only outputs supported by the declared distribution evidence."""

    request.validate()
    reasons: list[str] = []
    pathwise = False
    interval = False
    point_target = False
    entry = False
    success_claim = False
    diagnostic_only = False

    if request.route is DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION:
        if request.scenario_assignment_method != NO_SCENARIO_ASSIGNMENT:
            reasons.append("PATHWISE_ROUTE_FORBIDS_SCENARIO_ANCHOR_ASSIGNMENT")
        if not request.calibrated_driver_distribution:
            reasons.append("DRIVER_DISTRIBUTION_NOT_CALIBRATED")
        if not request.financing_waterfall_authorized:
            reasons.append("FINANCING_WATERFALL_NOT_AUTHORIZED")
        if not reasons:
            pathwise = True
            point_target = True
            payoff_reasons = _future_payoff_authorization_reasons(request)
            entry = not payoff_reasons
            success_claim = not payoff_reasons
            reasons.extend(payoff_reasons)
        status = (
            DistributionRouteStatus.AUTHORIZED
            if pathwise and entry
            else DistributionRouteStatus.CONDITIONAL
            if pathwise
            else DistributionRouteStatus.BLOCKED
        )

    elif request.route is DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE:
        if request.scenario_assignment_method != NO_SCENARIO_ASSIGNMENT:
            reasons.append("AMBIGUITY_ROUTE_FORBIDS_SCENARIO_ANCHOR_ASSIGNMENT")
        if not request.signed_values_authorized:
            reasons.append("SIGNED_OUTCOME_VALUES_NOT_AUTHORIZED")
        if not request.ambiguity_set_validated or request.ambiguity_vector_count < 2:
            reasons.append("NON_DEGENERATE_AMBIGUITY_SET_REQUIRED")
        if not reasons:
            interval = True
            payoff_reasons = _future_payoff_authorization_reasons(request)
            entry = not payoff_reasons
            reasons.extend(payoff_reasons)
        status = (
            DistributionRouteStatus.AUTHORIZED
            if interval and entry
            else DistributionRouteStatus.CONDITIONAL
            if interval
            else DistributionRouteStatus.BLOCKED
        )

    else:
        diagnostic_only = True
        if request.scenario_assignment_method != LEGACY_NEAREST_ASSIGNMENT:
            reasons.append("LEGACY_REPLAY_REQUIRES_DECLARED_NEAREST_ASSIGNMENT")
        if not request.frozen_legacy_replay_receipt:
            reasons.append("FROZEN_LEGACY_REPLAY_RECEIPT_REQUIRED")
        if "capacity_yield_levered" in request.economic_archetypes:
            reasons.append("CAPACITY_YIELD_ROUTE_FORBIDS_NEAREST_ANCHOR_PROBABILITY")
        status = (
            DistributionRouteStatus.REPLAY_ONLY
            if not reasons
            else DistributionRouteStatus.BLOCKED
        )

    authorization_hash = _authorization_hash(
        request=request,
        status=status,
        pathwise=pathwise,
        interval=interval,
        point_target=point_target,
        entry=entry,
        success_claim=success_claim,
        diagnostic_only=diagnostic_only,
        reasons=tuple(reasons),
    )
    return DistributionRouteAuthorization(
        route=request.route,
        status=status,
        pathwise_value_distribution_authorized=pathwise,
        expected_value_interval_authorized=interval,
        point_target_authorized=point_target,
        entry_price_authorized=entry,
        success_probability_claim_authorized=success_claim,
        diagnostic_only=diagnostic_only,
        blocking_reasons=tuple(reasons),
        authorization_hash=authorization_hash,
    )


def _authorization_hash(
    *,
    request: DistributionRouteRequest,
    status: DistributionRouteStatus,
    pathwise: bool,
    interval: bool,
    point_target: bool,
    entry: bool,
    success_claim: bool,
    diagnostic_only: bool,
    reasons: tuple[str, ...],
) -> str:
    payload = {
        "contract": "distribution_route_authorization/v1",
        "request": {
            "route": request.route.value,
            "economic_archetypes": sorted(request.economic_archetypes),
            "scenario_assignment_method": request.scenario_assignment_method,
            "evidence_path_ids": sorted(request.evidence_path_ids),
            "calibrated_driver_distribution": request.calibrated_driver_distribution,
            "financing_waterfall_authorized": request.financing_waterfall_authorized,
            "signed_values_authorized": request.signed_values_authorized,
            "ambiguity_set_validated": request.ambiguity_set_validated,
            "ambiguity_vector_count": request.ambiguity_vector_count,
            "future_shareholder_payoffs_authorized": (
                request.future_shareholder_payoffs_authorized
            ),
            "future_payoff_authorization_receipt": (
                request.future_payoff_authorization_receipt
            ),
            "payoff_horizon_years": request.payoff_horizon_years,
            "payoff_model_case_count": request.payoff_model_case_count,
            "frozen_legacy_replay_receipt": request.frozen_legacy_replay_receipt,
        },
        "authorization": {
            "status": status.value,
            "pathwise_value_distribution_authorized": pathwise,
            "expected_value_interval_authorized": interval,
            "point_target_authorized": point_target,
            "entry_price_authorized": entry,
            "success_probability_claim_authorized": success_claim,
            "diagnostic_only": diagnostic_only,
            "blocking_reasons": reasons,
        },
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _future_payoff_authorization_reasons(
    request: DistributionRouteRequest,
) -> tuple[str, ...]:
    """Require an evaluator receipt, not a free-standing LLM assertion."""

    if not request.future_shareholder_payoffs_authorized:
        return ("FUTURE_SHAREHOLDER_PAYOFFS_NOT_AUTHORIZED",)
    reasons: list[str] = []
    if not request.future_payoff_authorization_receipt:
        reasons.append("FUTURE_PAYOFF_AUTHORIZATION_RECEIPT_REQUIRED")
    if request.payoff_horizon_years is None:
        reasons.append("DATED_FUTURE_PAYOFF_HORIZON_REQUIRED")
    if request.payoff_model_case_count <= 0:
        reasons.append("COMPLETE_PAYOFF_MODEL_CASE_REQUIRED")
    return tuple(reasons)


__all__ = [
    "DistributionIntegrationRoute",
    "DistributionRouteAuthorization",
    "DistributionRouteError",
    "DistributionRouteRequest",
    "DistributionRouteStatus",
    "LEGACY_NEAREST_ASSIGNMENT",
    "NO_SCENARIO_ASSIGNMENT",
    "authorize_distribution_route",
]
