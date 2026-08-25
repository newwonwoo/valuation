from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
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
_ALLOWED_EVIDENCE_ROLES = {"realized", "company_plan", "policy"}
_FORBIDDEN_INTRINSIC_METRIC_TOKENS = (
    "current_market_price",
    "market_price",
    "target_price",
    "consensus_target",
    "consensus_eps",
    "target_market_cap",
    "target_multiple",
    "street_reference",
    "street_consensus",
)


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

    @property
    def default_evidence_role(self) -> str:
        return {
            PrimarySourceKind.REGULATORY_FILING: "realized",
            PrimarySourceKind.COMPANY_IR: "company_plan",
            PrimarySourceKind.PRIMARY_REGULATOR: "policy",
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
        if any(token in lowered for token in _FORBIDDEN_INTRINSIC_METRIC_TOKENS):
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
    evidence_role: str = ""
    source_ref: str = ""

    def validate(self, *, document: AuthorizedPrimaryDocument) -> None:
        if not all((self.metric, self.segment, self.unit, self.effective_date, self.locator)):
            raise ValueError("primary metric observation is incomplete")
        _reject_forbidden_metric(self.metric)
        _canonical_scalar(self.value)
        role = _resolve_evidence_role(document, self)
        if self.source_ref and self.source_ref != document.source_ref:
            raise ValueError("primary metric observation source_ref must match authorized document")
        effective = date.fromisoformat(self.effective_date[:10])
        published = _parse_aware(document.published_at, "published_at").date()
        if (
            document.kind is PrimarySourceKind.REGULATORY_FILING
            and role == "realized"
            and effective > published
        ):
            raise ValueError(
                "realized regulatory filing metric cannot have an effective date after publication"
            )


@dataclass(frozen=True)
class PrimaryEvidenceRecord(EvidenceRecord):
    published_at: str = ""
    first_seen_at: str = ""
    source_revision: str = ""
    evidence_role: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not all((self.published_at, self.first_seen_at, self.source_revision, self.evidence_role)):
            raise ValueError(
                "primary Evidence requires publication, first-seen, revision identity and evidence_role"
            )
        if self.evidence_role not in _ALLOWED_EVIDENCE_ROLES:
            raise ValueError(f"unsupported primary Evidence role: {self.evidence_role}")
        published = _parse_aware(self.published_at, "Evidence published_at")
        first_seen = _parse_aware(self.first_seen_at, "Evidence first_seen_at")
        if published > first_seen:
            raise ValueError("Evidence publication cannot follow first-seen time")
        if self.observed_date[:10] != first_seen.date().isoformat():
            raise ValueError("primary Evidence observed_date must equal first-seen date")
        if not _HASH64.fullmatch(self.source_revision):
            raise ValueError("primary Evidence source_revision must be exact document SHA-256")


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
    for metric in allowed_metrics:
        _reject_forbidden_metric(metric)

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
) -> PrimaryEvidenceRecord:
    value = _canonical_scalar(observation.value)
    evidence_role = _resolve_evidence_role(document, observation)
    payload = {
        "document_hash": document.document_hash.lower(),
        "target": document.target_id,
        "metric": observation.metric,
        "segment": observation.segment,
        "value": value,
        "unit": observation.unit,
        "effective_date": observation.effective_date[:10],
        "locator": observation.locator,
        "published_at": document.published_at,
        "first_seen_at": document.checked_at,
        "evidence_role": evidence_role,
    }
    evidence_id = "E:PRIMARY:" + sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    return PrimaryEvidenceRecord(
        id=evidence_id,
        target=document.target_id,
        metric=observation.metric,
        value=value,
        unit=observation.unit,
        source_layer=_evidence_layer_for_role(evidence_role),
        effective_date=observation.effective_date[:10],
        observed_date=_parse_aware(document.checked_at, "checked_at").date().isoformat(),
        source_name=document.source_id,
        source_ref=f"{document.source_ref}#{observation.locator}",
        source_grade="A_PRIMARY",
        confidence=1.0,
        segment=observation.segment,
        notes=observation.notes,
        critical=observation.critical,
        published_at=document.published_at,
        first_seen_at=document.checked_at,
        source_revision=document.document_hash.lower(),
        evidence_role=evidence_role,
    )


def _source_fingerprint(
    document: AuthorizedPrimaryDocument,
    observations: tuple[PrimaryMetricObservation, ...],
) -> str:
    payload = {
        "contract": "authorized_primary_source/v3",
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
                "value": _canonical_scalar(item.value),
                "unit": item.unit,
                "effective_date": item.effective_date,
                "locator": item.locator,
                "critical": item.critical,
                "notes": item.notes,
                "evidence_role": _resolve_evidence_role(document, item),
                "source_ref": item.source_ref,
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
        ).encode("utf-8")
    ).hexdigest()


def _resolve_evidence_role(
    document: AuthorizedPrimaryDocument,
    observation: PrimaryMetricObservation,
) -> str:
    role = observation.evidence_role or document.kind.default_evidence_role
    if role not in _ALLOWED_EVIDENCE_ROLES:
        raise ValueError(f"unsupported primary metric evidence_role: {role}")
    return role


def _evidence_layer_for_role(role: str) -> EvidenceSourceLayer:
    return {
        "realized": EvidenceSourceLayer.REALIZED_OR_FILING,
        "company_plan": EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
        "policy": EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
    }[role]


def _reject_forbidden_metric(metric: str) -> None:
    normalized = metric.strip().casefold().replace("-", "_").replace(" ", "_")
    if any(token in normalized for token in _FORBIDDEN_INTRINSIC_METRIC_TOKENS):
        raise ValueError(
            f"target-market/Street metric cannot enter primary intrinsic Evidence: {metric}"
        )


def _canonical_scalar(value: Any) -> str | int | float:
    if isinstance(value, bool) or value is None:
        raise ValueError("primary metric value must be a scalar string or finite number")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("primary metric Decimal value must be finite")
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("primary metric float value must be finite")
        return value
    if isinstance(value, str):
        return value
    raise TypeError(
        "primary metric value must be JSON-stable scalar; normalize custom values before collection"
    )


def _parse_aware(value: str, label: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware ISO datetime")
    return parsed
