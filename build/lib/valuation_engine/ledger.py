from __future__ import annotations

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class EvidenceLedgerSnapshot:
    content_hash: str
    mutation_version: int
    records: tuple[EvidenceRecord, ...]

    def __post_init__(self) -> None:
        if not self.content_hash:
            raise ValueError("ledger snapshot requires content_hash")
        if self.mutation_version < 0:
            raise ValueError("ledger snapshot mutation_version cannot be negative")

    def is_current(self, ledger: "EvidenceLedger") -> bool:
        return (
            ledger.mutation_version == self.mutation_version
            and ledger.records() == self.records
        )


class EvidenceLedger:
    """Append-only evidence collection with explicit supersession."""

    def __init__(self, records: Iterable[EvidenceRecord] = ()) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._mutation_version = 0
        self._runtime_snapshot: EvidenceLedgerSnapshot | None = None
        self._runtime_readonly_depth = 0
        for record in records:
            self.append(record)

    @property
    def mutation_version(self) -> int:
        return self._mutation_version

    @property
    def runtime_snapshot(self) -> EvidenceLedgerSnapshot | None:
        return self._runtime_snapshot

    @property
    def runtime_readonly(self) -> bool:
        return self._runtime_readonly_depth > 0

    def _enter_runtime_readonly(self) -> None:
        self._runtime_readonly_depth += 1

    def _exit_runtime_readonly(self) -> None:
        if self._runtime_readonly_depth <= 0:
            raise RuntimeError("EvidenceLedger runtime read-only guard is unbalanced")
        self._runtime_readonly_depth -= 1

    def append(self, record: EvidenceRecord) -> None:
        if self.runtime_readonly:
            raise RuntimeError(
                "EvidenceLedger is read-only during downstream stage execution"
            )
        if record.id in self._records:
            raise ValueError(f"duplicate evidence id: {record.id}")
        if record.supersedes_id:
            prior = self._records.get(record.supersedes_id)
            if prior is None:
                raise ValueError(f"unknown superseded evidence: {record.supersedes_id}")
            if (prior.target, prior.metric, prior.segment) != (record.target, record.metric, record.segment):
                raise ValueError("superseding evidence must retain target, metric and segment")
        self._records[record.id] = record
        self._mutation_version += 1

    def get(self, evidence_id: str) -> EvidenceRecord:
        try:
            return self._records[evidence_id]
        except KeyError as exc:
            raise ValueError(f"unknown evidence id: {evidence_id}") from exc

    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: item.id))

    def active(self) -> tuple[EvidenceRecord, ...]:
        superseded = {r.supersedes_id for r in self._records.values() if r.supersedes_id}
        return tuple(
            r for r in self._records.values()
            if r.id not in superseded and r.status is EvidenceStatus.ACTIVE
        )

    def snapshot(self, *, content_hash: str) -> EvidenceLedgerSnapshot:
        snapshot = EvidenceLedgerSnapshot(
            content_hash=content_hash,
            mutation_version=self._mutation_version,
            records=self.records(),
        )
        self._runtime_snapshot = snapshot
        return snapshot

    def to_list(self) -> list[dict]:
        return [_enum_values(asdict(r)) for r in self.records()]


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
