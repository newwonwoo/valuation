from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping


class ClaimValuationTreatment(str, Enum):
    VALUED = "VALUED"
    REFERENCE_ONLY = "REFERENCE_ONLY"


@dataclass(frozen=True)
class ClaimValuationImpact:
    claim_id: str
    treatment: ClaimValuationTreatment
    evidence_ids: tuple[str, ...]
    bridge_ids: tuple[str, ...]
    assumption_keys: tuple[tuple[str, str], ...]
    prior_intrinsic_value_per_share: Decimal | None
    revised_intrinsic_value_per_share: Decimal | None
    rationale: str

    @property
    def value_delta_per_share(self) -> Decimal | None:
        if (
            self.prior_intrinsic_value_per_share is None
            or self.revised_intrinsic_value_per_share is None
        ):
            return None
        return (
            self.revised_intrinsic_value_per_share
            - self.prior_intrinsic_value_per_share
        )


@dataclass(frozen=True)
class ClaimValuationSyncAudit:
    impacts: tuple[ClaimValuationImpact, ...]
    headline_claim_ids: tuple[str, ...]

    def impact(self, claim_id: str) -> ClaimValuationImpact:
        try:
            return next(item for item in self.impacts if item.claim_id == claim_id)
        except StopIteration as exc:
            raise KeyError(claim_id) from exc


def audit_claim_to_value_sync(
    *,
    impacts: Iterable[ClaimValuationImpact],
    headline_claim_ids: Iterable[str],
    active_evidence_ids: Iterable[str],
    active_bridge_ids: Iterable[str],
    bridge_evidence_map: Mapping[str, Iterable[str]],
    compiled_assumption_keys: Iterable[tuple[str, str]],
) -> ClaimValuationSyncAudit:
    """Fail closed when a valuation-changing headline is not model-bound."""

    impact_rows = tuple(impacts)
    headline_ids = tuple(headline_claim_ids)
    evidence_ids = frozenset(active_evidence_ids)
    bridge_ids = frozenset(active_bridge_ids)
    assumption_keys = frozenset(compiled_assumption_keys)
    impact_ids = tuple(item.claim_id for item in impact_rows)

    if not impact_rows or not headline_ids:
        raise ValueError("claim-to-value sync requires impacts and headline claims")
    if len(impact_ids) != len(set(impact_ids)):
        raise ValueError("claim-to-value sync requires unique claim IDs")

    impact_map = {item.claim_id: item for item in impact_rows}
    for claim_id in headline_ids:
        impact = impact_map.get(claim_id)
        if impact is None:
            raise ValueError(f"headline claim lacks valuation impact: {claim_id}")
        if impact.treatment is not ClaimValuationTreatment.VALUED:
            raise ValueError(
                f"REFERENCE_ONLY claim cannot lead the report: {claim_id}"
            )

    for impact in impact_rows:
        if not impact.claim_id or not impact.rationale.strip():
            raise ValueError("claim-to-value impact requires ID and rationale")
        if impact.treatment is ClaimValuationTreatment.REFERENCE_ONLY:
            continue
        if not impact.evidence_ids or not impact.bridge_ids or not impact.assumption_keys:
            raise ValueError(
                f"VALUED claim lacks Evidence/Bridge/Assumption mapping: "
                f"{impact.claim_id}"
            )
        missing_evidence = set(impact.evidence_ids) - evidence_ids
        missing_bridges = set(impact.bridge_ids) - bridge_ids
        missing_assumptions = set(impact.assumption_keys) - assumption_keys
        if missing_evidence or missing_bridges or missing_assumptions:
            raise ValueError(
                f"VALUED claim mapping is not active: {impact.claim_id}; "
                f"evidence={sorted(missing_evidence)}, "
                f"bridges={sorted(missing_bridges)}, "
                f"assumptions={sorted(missing_assumptions)}"
            )
        mapped_evidence = {
            evidence_id
            for bridge_id in impact.bridge_ids
            for evidence_id in bridge_evidence_map.get(bridge_id, ())
        }
        unmapped_evidence = set(impact.evidence_ids) - mapped_evidence
        if unmapped_evidence:
            raise ValueError(
                f"VALUED claim Evidence is not bound to its Bridge: "
                f"{impact.claim_id}; evidence={sorted(unmapped_evidence)}"
            )
        if impact.value_delta_per_share is None or impact.value_delta_per_share == 0:
            raise ValueError(
                f"VALUED claim left intrinsic value unchanged: {impact.claim_id}"
            )

    return ClaimValuationSyncAudit(impact_rows, headline_ids)
