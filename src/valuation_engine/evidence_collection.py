from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Protocol

from .ledger import EvidenceLedger
from .records import EvidenceRecord, EvidenceSourceLayer


def _checked_at_temporal(value: str) -> date | datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("evidence batch checked_at is required")
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            "evidence batch checked_at must be an ISO date/timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            "evidence batch checked_at timestamp must be timezone-aware"
        )
    return parsed


def _observed_temporal(value: str) -> date | datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            "evidence observed_date must be an ISO date/timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            "evidence observed_date timestamp must be timezone-aware"
        )
    return parsed


def _observed_after_checkpoint(
    observed: date | datetime,
    checked: date | datetime,
) -> bool:
    if isinstance(observed, datetime) and isinstance(checked, datetime):
        return observed.astimezone(timezone.utc) > checked.astimezone(timezone.utc)
    observed_date = observed.date() if isinstance(observed, datetime) else observed
    checked_date = checked.date() if isinstance(checked, datetime) else checked
    return observed_date > checked_date


@dataclass(frozen=True)
class EvidenceCollectionRequest:
    target_id: str
    required_metrics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("evidence collection request requires target_id")
        if len(self.required_metrics) != len(set(self.required_metrics)):
            raise ValueError("required_metrics must be unique")


@dataclass(frozen=True)
class EvidenceCollectionBatch:
    source_id: str
    checked_at: str
    records: tuple[EvidenceRecord, ...]
    source_fingerprint: str
    document_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if (
            not self.source_id
            or not self.checked_at
            or not self.source_fingerprint
        ):
            raise ValueError(
                "evidence batch requires source_id, checked_at and source_fingerprint"
            )
        checked_temporal = _checked_at_temporal(self.checked_at)
        ids = tuple(item.id for item in self.records)
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"duplicate evidence IDs inside source batch {self.source_id}"
            )
        for item in self.records:
            if item.target == "":
                raise ValueError("evidence target cannot be blank")
            observed_temporal = _observed_temporal(item.observed_date)
            if _observed_after_checkpoint(observed_temporal, checked_temporal):
                raise ValueError(
                    f"evidence {item.id} observed after source batch checked_at"
                )
            if item.source_layer not in {
                EvidenceSourceLayer.REALIZED_OR_FILING,
                EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
                EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
                EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
                EvidenceSourceLayer.ANALYST_UNDERWRITING,
            }:
                raise ValueError(
                    f"source batch {self.source_id} contains non-primary "
                    f"intrinsic layer: {item.source_layer.value}"
                )


class EvidenceCollector(Protocol):
    def __call__(
        self,
        request: EvidenceCollectionRequest,
    ) -> EvidenceCollectionBatch: ...


@dataclass(frozen=True)
class PrimaryEvidenceCollectionResult:
    ledger: EvidenceLedger
    batches: tuple[EvidenceCollectionBatch, ...]
    required_metrics: tuple[str, ...]
    covered_metrics: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    source_snapshot_hash: str

    @property
    def coverage_complete(self) -> bool:
        return not self.missing_metrics


def _batch_snapshot_row(batch: EvidenceCollectionBatch) -> dict[str, object]:
    return {
        "source_id": batch.source_id,
        "checked_at": batch.checked_at,
        "source_fingerprint": batch.source_fingerprint,
        "document_ids": sorted(batch.document_ids),
        "evidence_ids": sorted(item.id for item in batch.records),
    }


def collect_primary_evidence(
    *,
    target_id: str,
    required_metrics: tuple[str, ...],
    collectors: tuple[EvidenceCollector, ...],
) -> PrimaryEvidenceCollectionResult:
    request = EvidenceCollectionRequest(target_id, required_metrics)
    if not collectors:
        raise ValueError("at least one primary evidence collector is required")

    ledger = EvidenceLedger()
    batches: list[EvidenceCollectionBatch] = []
    for collector in collectors:
        batch = collector(request)
        batch.validate()
        for record in batch.records:
            if record.target != target_id:
                raise ValueError(
                    f"evidence target mismatch for {record.id}: expected "
                    f"{target_id}, got {record.target}"
                )
            ledger.append(record)
        batches.append(batch)

    active_metrics = {item.metric for item in ledger.active()}
    covered = tuple(
        metric for metric in required_metrics if metric in active_metrics
    )
    missing = tuple(
        metric for metric in required_metrics if metric not in active_metrics
    )
    batch_rows = [_batch_snapshot_row(batch) for batch in batches]
    batch_rows.sort(
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    snapshot_payload = {
        "target_id": target_id,
        # Multiple collector implementations may legitimately partition metrics from
        # one primary source. Preserve every batch instead of collapsing by source_id.
        "batches": batch_rows,
        "evidence": sorted(
            ledger.to_list(),
            key=lambda item: str(item.get("id", "")),
        ),
    }
    snapshot_hash = sha256(
        json.dumps(
            snapshot_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PrimaryEvidenceCollectionResult(
        ledger=ledger,
        batches=tuple(batches),
        required_metrics=required_metrics,
        covered_metrics=covered,
        missing_metrics=missing,
        source_snapshot_hash=snapshot_hash,
    )


def static_evidence_collector(
    *,
    source_id: str,
    checked_at: str,
    records: tuple[EvidenceRecord, ...],
    source_fingerprint: str,
    document_ids: tuple[str, ...] = (),
) -> EvidenceCollector:
    """Fixture/manual adapter that obeys the live Collector contract."""

    def collect(_: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        return EvidenceCollectionBatch(
            source_id=source_id,
            checked_at=checked_at,
            records=records,
            source_fingerprint=source_fingerprint,
            document_ids=document_ids,
        )

    return collect
