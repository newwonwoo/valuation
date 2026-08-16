from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Iterable

from .records import (
    AffectedVariable,
    AssumptionRecord,
    BridgeRecord,
    EvidenceRecord,
    EvidenceSourceLayer,
    EvidenceStatus,
    HypothesisRecord,
)


class EvidenceLedger:
    """Append-only evidence collection with explicit supersession."""

    def __init__(self, records: Iterable[EvidenceRecord] = ()) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        for record in records:
            self.append(record)

    def append(self, record: EvidenceRecord) -> None:
        if record.id in self._records:
            raise ValueError(f"duplicate evidence id: {record.id}")
        if record.supersedes_id:
            prior = self._records.get(record.supersedes_id)
            if prior is None:
                raise ValueError(f"unknown superseded evidence: {record.supersedes_id}")
            if (prior.target, prior.metric, prior.segment) != (record.target, record.metric, record.segment):
                raise ValueError("superseding evidence must retain target, metric and segment")
        self._records[record.id] = record

    def get(self, evidence_id: str) -> EvidenceRecord:
        try:
            return self._records[evidence_id]
        except KeyError as exc:
            raise ValueError(f"unknown evidence id: {evidence_id}") from exc

    def active(self) -> tuple[EvidenceRecord, ...]:
        superseded = {r.supersedes_id for r in self._records.values() if r.supersedes_id}
        return tuple(
            r for r in self._records.values()
            if r.id not in superseded and r.status is EvidenceStatus.ACTIVE
        )

    def to_list(self) -> list[dict]:
        return [_enum_values(asdict(r)) for r in sorted(self._records.values(), key=lambda item: item.id)]


def validate_traceability(
    ledger: EvidenceLedger,
    hypotheses: Iterable[HypothesisRecord],
    bridges: Iterable[BridgeRecord],
    assumptions: Iterable[AssumptionRecord],
) -> None:
    hypothesis_map = {item.id: item for item in hypotheses}
    bridge_map = {item.id: item for item in bridges}
    for hypothesis in hypothesis_map.values():
        for evidence_id in (*hypothesis.supporting_evidence_ids, *hypothesis.contradicting_evidence_ids):
            ledger.get(evidence_id)
    for bridge in bridge_map.values():
        hypothesis = hypothesis_map.get(bridge.hypothesis_id)
        if hypothesis is None:
            raise ValueError(f"bridge {bridge.id} references unknown hypothesis")
        bridge_evidence = []
        for evidence_id in bridge.evidence_ids:
            evidence = ledger.get(evidence_id)
            bridge_evidence.append(evidence)
            if evidence.source_layer is EvidenceSourceLayer.MARKET_COMPARISON:
                raise ValueError("market_comparison evidence cannot enter an intrinsic assumption bridge")
        if bridge.affected_variable is AffectedVariable.PRICE and bridge_evidence and all(
            item.source_layer is EvidenceSourceLayer.POLICY_PRIMARY_SOURCE for item in bridge_evidence
        ):
            raise ValueError("policy price cannot become enterprise price without economic evidence")
    for assumption in assumptions:
        bridge = bridge_map.get(assumption.bridge_id)
        if bridge is None:
            raise ValueError(f"assumption {assumption.key} has no valid bridge")
        if assumption.unit != bridge.unit:
            raise ValueError(f"assumption {assumption.key} unit does not match bridge")
        if assumption.value != bridge.new_value:
            raise ValueError(f"assumption {assumption.key} value does not match bridge")


def stale_evidence_findings(
    ledger: EvidenceLedger,
    as_of: date,
    *,
    critical_max_age_days: int = 180,
    noncritical_max_age_days: int = 365,
) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []
    for evidence in ledger.active():
        age = (as_of - date.fromisoformat(evidence.observed_date[:10])).days
        limit = critical_max_age_days if evidence.critical else noncritical_max_age_days
        if age > limit:
            message = f"{evidence.id} stale by {age - limit} days"
            (blocking if evidence.critical else warnings).append(message)
    return blocking, warnings


def _enum_values(value):
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_enum_values(item) for item in value]
    return value.value if hasattr(value, "value") else value
