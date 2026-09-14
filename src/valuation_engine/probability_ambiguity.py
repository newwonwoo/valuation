"""Source-bound probability ambiguity sets and signed expected-value ranges."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json


ZERO = Decimal("0")
ONE = Decimal("1")


class ProbabilityAmbiguityError(ValueError):
    """Raised when an ambiguity set or its outcome values are malformed."""


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise ProbabilityAmbiguityError(f"{label} must be finite")
    return value


@dataclass(frozen=True)
class ProbabilityVector:
    """One source-bound probability assessment inside an ambiguity set."""

    vector_id: str
    weights: tuple[tuple[str, Decimal], ...]
    evidence_path_ids: tuple[str, ...]

    def validate(self, outcome_ids: tuple[str, ...]) -> None:
        if not self.vector_id or not self.evidence_path_ids:
            raise ProbabilityAmbiguityError(
                "probability vector requires identity and evidence paths"
            )
        ids = tuple(outcome_id for outcome_id, _ in self.weights)
        if len(ids) != len(set(ids)):
            raise ProbabilityAmbiguityError(
                "probability vector repeats an outcome ID"
            )
        if set(ids) != set(outcome_ids):
            raise ProbabilityAmbiguityError(
                "probability vector must cover the exact outcome set"
            )
        total = ZERO
        for _, weight in self.weights:
            _decimal(weight, "probability weight")
            if not ZERO <= weight <= ONE:
                raise ProbabilityAmbiguityError(
                    "probability weight must lie within [0,1]"
                )
            total += weight
        if total != ONE:
            raise ProbabilityAmbiguityError(
                "probability vector weights must sum to one"
            )

    def as_map(self) -> dict[str, Decimal]:
        return dict(self.weights)


def validate_probability_ambiguity_set(
    *,
    probability_vectors: tuple[ProbabilityVector, ...],
    outcome_ids: tuple[str, ...],
) -> tuple[tuple[ProbabilityVector, ...], str]:
    """Validate, canonicalize and hash a non-degenerate ambiguity set."""

    if len(probability_vectors) < 2:
        raise ProbabilityAmbiguityError(
            "ambiguity set requires at least two probability vectors"
        )
    vector_ids = tuple(item.vector_id for item in probability_vectors)
    if len(vector_ids) != len(set(vector_ids)):
        raise ProbabilityAmbiguityError(
            "ambiguity set contains duplicate probability-vector IDs"
        )
    for vector in probability_vectors:
        vector.validate(outcome_ids)
    canonical_weights = {
        tuple(sorted(vector.weights, key=lambda item: item[0]))
        for vector in probability_vectors
    }
    if len(canonical_weights) != len(probability_vectors):
        raise ProbabilityAmbiguityError(
            "ambiguity set must contain distinct probability assessments"
        )
    canonical = tuple(sorted(probability_vectors, key=lambda item: item.vector_id))
    payload = [
        {
            "vector_id": vector.vector_id,
            "weights": [
                (key, str(value))
                for key, value in sorted(vector.weights, key=lambda item: item[0])
            ],
            "evidence_path_ids": sorted(vector.evidence_path_ids),
        }
        for vector in canonical
    ]
    ambiguity_set_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return canonical, ambiguity_set_hash


class AmbiguityValueStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    WITHHELD = "WITHHELD"


@dataclass(frozen=True)
class SignedOutcomeValue:
    """Audited present value for one mutually exclusive outcome.

    Values deliberately remain signed.  A present-value scenario is not a
    dated shareholder payoff and may not receive a report-time zero floor.
    """

    outcome_id: str
    present_value: Decimal
    evidence_path_ids: tuple[str, ...]

    def validate(self) -> None:
        if not self.outcome_id or not self.evidence_path_ids:
            raise ProbabilityAmbiguityError(
                "outcome value requires identity and evidence paths"
            )
        _decimal(self.present_value, "outcome present value")


@dataclass(frozen=True)
class ProbabilityExpectedValue:
    vector_id: str
    expected_value: Decimal


@dataclass(frozen=True)
class AmbiguityExpectedValueResult:
    status: AmbiguityValueStatus
    minimum_expected_value: Decimal | None
    maximum_expected_value: Decimal | None
    binding_minimum_vector_id: str | None
    binding_maximum_vector_id: str | None
    vector_results: tuple[ProbabilityExpectedValue, ...]
    value_set_hash: str
    ambiguity_set_hash: str
    calculation_hash: str
    calibrated_probability_claim_authorized: bool
    withheld_reason: str | None


def calculate_ambiguity_expected_value_range(
    *,
    outcomes: tuple[SignedOutcomeValue, ...],
    probability_vectors: tuple[ProbabilityVector, ...],
    values_authorized: bool,
    value_set_hash: str,
) -> AmbiguityExpectedValueResult:
    """Calculate the expected-value interval across all declared priors."""

    if not outcomes or not value_set_hash:
        raise ProbabilityAmbiguityError(
            "ambiguity value requires outcomes and a value-set hash"
        )
    outcome_ids = tuple(item.outcome_id for item in outcomes)
    if len(outcome_ids) != len(set(outcome_ids)):
        raise ProbabilityAmbiguityError("outcome values contain duplicate IDs")
    for outcome in outcomes:
        outcome.validate()
    canonical_vectors, ambiguity_set_hash = validate_probability_ambiguity_set(
        probability_vectors=probability_vectors,
        outcome_ids=outcome_ids,
    )
    canonical_outcomes = tuple(sorted(outcomes, key=lambda item: item.outcome_id))
    calculation_hash = _expected_value_hash(
        outcomes=canonical_outcomes,
        probability_vectors=canonical_vectors,
        value_set_hash=value_set_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        values_authorized=values_authorized,
    )
    if not values_authorized:
        return AmbiguityExpectedValueResult(
            status=AmbiguityValueStatus.WITHHELD,
            minimum_expected_value=None,
            maximum_expected_value=None,
            binding_minimum_vector_id=None,
            binding_maximum_vector_id=None,
            vector_results=(),
            value_set_hash=value_set_hash,
            ambiguity_set_hash=ambiguity_set_hash,
            calculation_hash=calculation_hash,
            calibrated_probability_claim_authorized=False,
            withheld_reason="OUTCOME_VALUES_NOT_AUTHORIZED",
        )
    value_map = {item.outcome_id: item.present_value for item in canonical_outcomes}
    vector_results = tuple(
        ProbabilityExpectedValue(
            vector_id=vector.vector_id,
            expected_value=sum(
                (
                    value_map[outcome_id] * weight
                    for outcome_id, weight in vector.weights
                ),
                ZERO,
            ),
        )
        for vector in canonical_vectors
    )
    minimum = min(
        vector_results, key=lambda item: (item.expected_value, item.vector_id)
    )
    maximum = max(
        vector_results, key=lambda item: (item.expected_value, item.vector_id)
    )
    return AmbiguityExpectedValueResult(
        status=AmbiguityValueStatus.AVAILABLE,
        minimum_expected_value=minimum.expected_value,
        maximum_expected_value=maximum.expected_value,
        binding_minimum_vector_id=minimum.vector_id,
        binding_maximum_vector_id=maximum.vector_id,
        vector_results=vector_results,
        value_set_hash=value_set_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        calculation_hash=calculation_hash,
        calibrated_probability_claim_authorized=False,
        withheld_reason=None,
    )


def _expected_value_hash(
    *,
    outcomes: tuple[SignedOutcomeValue, ...],
    probability_vectors: tuple[ProbabilityVector, ...],
    value_set_hash: str,
    ambiguity_set_hash: str,
    values_authorized: bool,
) -> str:
    payload = {
        "contract": "governed_prior_ambiguity_expected_value/v1",
        "outcomes": [
            {
                "outcome_id": item.outcome_id,
                "present_value": str(item.present_value),
                "evidence_path_ids": sorted(item.evidence_path_ids),
            }
            for item in outcomes
        ],
        "probability_vectors": [item.vector_id for item in probability_vectors],
        "value_set_hash": value_set_hash,
        "ambiguity_set_hash": ambiguity_set_hash,
        "values_authorized": values_authorized,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "AmbiguityExpectedValueResult",
    "AmbiguityValueStatus",
    "ProbabilityAmbiguityError",
    "ProbabilityExpectedValue",
    "ProbabilityVector",
    "SignedOutcomeValue",
    "calculate_ambiguity_expected_value_range",
    "validate_probability_ambiguity_set",
]
