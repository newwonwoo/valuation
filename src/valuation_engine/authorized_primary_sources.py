from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any

from .evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
    EvidenceCollector,
)
from .records import EvidenceRecord, EvidenceSourceLayer


_HASH64 = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_ACCESS = {"public", "licensed", "explicit_permission"}


class PrimarySourceKind(str, Enum):
    REGULATORY_FILING = "regulatory_filing"
    COMPANY_IR = "company_ir"
    PRIMARY_REGULATOR = "primary_regulator"

    @property
    def evidence_layer(self) -> EvidenceSourceLayer:
        return {
            PrimarySourceKind.REGULATORY_FILING: EvidenceSourceLayer.REALIZED_OR_FILING,
            PrimarySourceKind.COMPANY_IR: EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
            PrimarySourceKind.PRIMARY_REGULATOR: EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
        }[self]


@dataclass(frozen=True)
class AuthorizedPrimaryDocument:
    source_id: str
    target_id: str
    kind: PrimarySourceKind
    document_id: str
    document_hash: str
    source_ref: str
    published_at: str
    checked_at: str
    access_basis: str

    def validate(self) -> None:
        if not all(
            (
                self.source_id,
                self.target_id,
                self.document_id,
                self.source_ref,
                self.published_at,
                self.checked_at,
                self.access_basis,
            )
        ):
            raise ValueError("authorized primary document is incomplete")
        if self.access_basis not in _ALLOWED_ACCESS:
            raise ValueError(
                "primary source access_basis must be public, licensed or explicit_permission"
            )
        if not _HASH64.fullmatch(self.document_hash):
            raise ValueError("primary source document_hash must be an exact SHA-256 hex digest")
        published = _parse_aware(self.published_at, "published_at")
        checked = _parse_aware(self.checked_at, "checked_at")
        if published > checked:
            raise ValueError("primary source cannot be checked before publication")
        lowered = self.source_ref.casefold()
        if any(token in lowered for token in ("target_price", "consensus_target", "market_price")):
            raise ValueError("target-market references cannot enter a primary intrinsic source pack")


@dataclass(frozen=True)
class PrimaryMetricObservation:
    metric: str
    segment: str
    value: Any
    unit: str
    effective_date: str
    locator: str
    critical: bool = False
    notes: str = ""

    def validate(self, *, document: AuthorizedPrimaryDocument) -> None:
        if not all((self.metric, self.segment, self.unit, self.effective_date, self.locator)):
            raise ValueError("primary metric observation is incomplete")
        effective = date.fromisoformat(self.effective_date[:10])
        published = _parse_aware(document.published_at, "published_at").date()
        if (
            document.kind is PrimarySourceKind.REGULATORY_FILING
            and effective > published
        ):
            raise ValueError(
                "realized regulatory filing metric cannot have an effective date after publication"
            )
        json.dumps(self.value, ensure_ascii=False, sort_keys=True, default=str)


def authorized_primary_source_collector(
    *,
    document: AuthorizedPrimaryDocument,
    observations: tuple[PrimaryMetricObservation, ...],
    allowed_metrics: tuple[str, ...],
    allowed_segments: tuple[str, ...],
) -> EvidenceCollector:
    document.validate()
    if not observations:
        raise ValueError("authorized primary source requires observations")
    if not allowed_metrics or len(allowed_metrics) != len(set(allowed_metrics)):
        raise ValueError("allowed_metrics must be non-empty and unique")
    if not allowed_segments or len(allowed_segments) != len(set(allowed_segments)):
        raise ValueError("allowed_segments must be non-empty and unique")

    metric_set = set(allowed_metrics)
    segment_set = set(allowed_segments)
    records: list[EvidenceRecord] = []
    for observation in observations:
        observation.validate(document=document)
        if observation.metric not in metric_set:
            raise ValueError(
                f"observation metric {observation.metric} is outside the declared collector capability"
            )
        if observation.segment not in segment_set:
            raise ValueError(
                f"observation segment {observation.segment} is outside the declared collector scope"
            )
        records.append(_evidence_record(document, observation))
    if len({item.id for item in records}) != len(records):
        raise ValueError("primary source observations produce duplicate deterministic Evidence IDs")

    fingerprint = _source_fingerprint(document, observations)

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        if request.target_id != document.target_id:
            raise ValueError(
                f"primary source target mismatch: expected {document.target_id}, got {request.target_id}"
            )
        selected = tuple(
            item for item in records if item.metric in set(request.required_metrics)
        )
        return EvidenceCollectionBatch(
            source_id=document.source_id,
            checked_at=document.checked_at,
            records=selected,
            source_fingerprint=fingerprint,
            document_ids=(document.document_id,),
        )

    return collect


def _evidence_record(
    document: AuthorizedPrimaryDocument,
    observation: PrimaryMetricObservation,
) -> EvidenceRecord:
    payload = {
        "document_hash": document.document_hash.lower(),
        "target": document.target_id,
        "metric": observation.metric,
        "segment": observation.segment,
        "value": observation.value,
        "unit": observation.unit,
        "effective_date": observation.effective_date[:10],
        "locator": observation.locator,
    }
    evidence_id = "E:PRIMARY:" + sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return EvidenceRecord(
        id=evidence_id,
        target=document.target_id,
        metric=observation.metric,
        value=observation.value,
        unit=observation.unit,
        source_layer=document.kind.evidence_layer,
        effective_date=observation.effective_date[:10],
        observed_date=_parse_aware(document.published_at, "published_at").date().isoformat(),
        source_name=document.source_id,
        source_ref=f"{document.source_ref}#{observation.locator}",
        source_grade="A_PRIMARY",
        confidence=1.0,
        segment=observation.segment,
        notes=observation.notes,
        critical=observation.critical,
    )


def _source_fingerprint(
    document: AuthorizedPrimaryDocument,
    observations: tuple[PrimaryMetricObservation, ...],
) -> str:
    payload = {
        "contract": "authorized_primary_source/v1",
        "source_id": document.source_id,
        "target_id": document.target_id,
        "kind": document.kind.value,
        "document_id": document.document_id,
        "document_hash": document.document_hash.lower(),
        "source_ref": document.source_ref,
        "published_at": document.published_at,
        "checked_at": document.checked_at,
        "access_basis": document.access_basis,
        "observations": [
            {
                "metric": item.metric,
                "segment": item.segment,
                "value": item.value,
                "unit": item.unit,
                "effective_date": item.effective_date,
                "locator": item.locator,
                "critical": item.critical,
                "notes": item.notes,
            }
            for item in observations
        ],
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _parse_aware(value: str, label: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware ISO datetime")
    return parsed
