from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json


class IntrinsicEnvelopeError(ValueError):
    """Raised when a primary intrinsic-value envelope is internally inconsistent."""


class PrimaryValuationKind(str, Enum):
    DETERMINISTIC_SCENARIO = "DETERMINISTIC_SCENARIO"
    DISTRIBUTIONAL_APV = "DISTRIBUTIONAL_APV"


@dataclass(frozen=True)
class DistributionLineage:
    """Immutable lineage that makes a distributional result freeze-safe.

    A distribution cannot be treated as the canonical intrinsic result merely
    because a source DCF was audited.  The distribution route, its value
    distribution and the entry policy must all be bound into the same lineage.
    Ambiguity/payoff hashes are optional only for a fully calibrated pathwise
    distribution; when either ambiguity hash is present the paired payoff-model
    hash is required as well.
    """

    distribution_hash: str
    route_authorization_hash: str
    entry_policy_version: str
    ambiguity_set_hash: str = ""
    payoff_model_set_hash: str = ""
    entry_calculation_hash: str = ""

    def validate(self) -> None:
        if not self.distribution_hash:
            raise IntrinsicEnvelopeError("distribution lineage requires distribution_hash")
        if not self.route_authorization_hash:
            raise IntrinsicEnvelopeError(
                "distribution lineage requires route_authorization_hash"
            )
        if not self.entry_policy_version:
            raise IntrinsicEnvelopeError(
                "distribution lineage requires entry_policy_version"
            )
        if bool(self.ambiguity_set_hash) != bool(self.payoff_model_set_hash):
            raise IntrinsicEnvelopeError(
                "ambiguity_set_hash and payoff_model_set_hash must be supplied together"
            )


@dataclass(frozen=True)
class IntrinsicValuationEnvelope:
    """Common primary-value identity for deterministic and distributional routes."""

    kind: PrimaryValuationKind
    reporting_unit: str
    source_value_hash: str
    economic_path_ids: tuple[str, ...]
    envelope_hash: str
    distribution_lineage: DistributionLineage | None = None
    reference_value_hashes: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.reporting_unit or not self.source_value_hash or not self.envelope_hash:
            raise IntrinsicEnvelopeError("intrinsic envelope identity is incomplete")
        if not self.economic_path_ids:
            raise IntrinsicEnvelopeError("intrinsic envelope requires economic paths")
        if len(self.economic_path_ids) != len(set(self.economic_path_ids)):
            raise IntrinsicEnvelopeError("intrinsic envelope repeats an economic path")
        if any(not item for item in self.reference_value_hashes):
            raise IntrinsicEnvelopeError("reference value hashes cannot be blank")
        if len(self.reference_value_hashes) != len(set(self.reference_value_hashes)):
            raise IntrinsicEnvelopeError("reference value hashes must be unique")
        if self.kind is PrimaryValuationKind.DETERMINISTIC_SCENARIO:
            if self.distribution_lineage is not None:
                raise IntrinsicEnvelopeError(
                    "deterministic intrinsic envelope cannot carry distribution lineage"
                )
        elif self.kind is PrimaryValuationKind.DISTRIBUTIONAL_APV:
            if self.distribution_lineage is None:
                raise IntrinsicEnvelopeError(
                    "distributional intrinsic envelope requires distribution lineage"
                )
            self.distribution_lineage.validate()
        else:  # pragma: no cover - Enum construction prevents this in normal use.
            raise IntrinsicEnvelopeError("unsupported primary valuation kind")
        if self.envelope_hash != _envelope_hash(
            kind=self.kind,
            reporting_unit=self.reporting_unit,
            source_value_hash=self.source_value_hash,
            economic_path_ids=self.economic_path_ids,
            distribution_lineage=self.distribution_lineage,
            reference_value_hashes=self.reference_value_hashes,
        ):
            raise IntrinsicEnvelopeError("intrinsic envelope hash does not replay")


def seal_intrinsic_valuation_envelope(
    *,
    kind: PrimaryValuationKind,
    reporting_unit: str,
    source_value_hash: str,
    economic_path_ids: tuple[str, ...],
    distribution_lineage: DistributionLineage | None = None,
    reference_value_hashes: tuple[str, ...] = (),
) -> IntrinsicValuationEnvelope:
    envelope = IntrinsicValuationEnvelope(
        kind=kind,
        reporting_unit=reporting_unit,
        source_value_hash=source_value_hash,
        economic_path_ids=tuple(dict.fromkeys(economic_path_ids)),
        envelope_hash=_envelope_hash(
            kind=kind,
            reporting_unit=reporting_unit,
            source_value_hash=source_value_hash,
            economic_path_ids=tuple(dict.fromkeys(economic_path_ids)),
            distribution_lineage=distribution_lineage,
            reference_value_hashes=reference_value_hashes,
        ),
        distribution_lineage=distribution_lineage,
        reference_value_hashes=reference_value_hashes,
    )
    envelope.validate()
    return envelope


def _envelope_hash(
    *,
    kind: PrimaryValuationKind,
    reporting_unit: str,
    source_value_hash: str,
    economic_path_ids: tuple[str, ...],
    distribution_lineage: DistributionLineage | None,
    reference_value_hashes: tuple[str, ...],
) -> str:
    payload = {
        "contract": "intrinsic_valuation_envelope/v1",
        "kind": kind.value,
        "reporting_unit": reporting_unit,
        "source_value_hash": source_value_hash,
        "economic_path_ids": list(economic_path_ids),
        "reference_value_hashes": list(reference_value_hashes),
        "distribution_lineage": (
            None
            if distribution_lineage is None
            else {
                "distribution_hash": distribution_lineage.distribution_hash,
                "route_authorization_hash": distribution_lineage.route_authorization_hash,
                "entry_policy_version": distribution_lineage.entry_policy_version,
                "ambiguity_set_hash": distribution_lineage.ambiguity_set_hash,
                "payoff_model_set_hash": distribution_lineage.payoff_model_set_hash,
                "entry_calculation_hash": distribution_lineage.entry_calculation_hash,
            }
        ),
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DistributionLineage",
    "IntrinsicEnvelopeError",
    "IntrinsicValuationEnvelope",
    "PrimaryValuationKind",
    "seal_intrinsic_valuation_envelope",
]
