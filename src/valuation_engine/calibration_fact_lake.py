from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json

from .records import EvidenceRecord, EvidenceSourceLayer


class CalibrationFactError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedCalibrationFact:
    fact_id: str
    target_id: str
    metric: str
    value: Decimal
    unit: str
    effective_date: date
    published_at: datetime
    first_seen_at: datetime
    evidence_id: str
    source_ref: str
    revision_of: str | None = None

    def validate(self) -> None:
        if not all(
            (
                self.fact_id,
                self.target_id,
                self.metric,
                self.unit,
                self.evidence_id,
                self.source_ref,
            )
        ):
            raise CalibrationFactError("normalized calibration fact identity is incomplete")
        if not self.value.is_finite():
            raise CalibrationFactError("normalized calibration fact value must be finite")
        for value, label in (
            (self.published_at, "published_at"),
            (self.first_seen_at, "first_seen_at"),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise CalibrationFactError(f"{label} must be timezone-aware")
        if self.first_seen_at < self.published_at:
            raise CalibrationFactError("fact first_seen_at cannot precede publication")
        if self.effective_date > self.published_at.date():
            raise CalibrationFactError(
                "realized calibration fact effective date cannot follow publication"
            )

    @property
    def content_hash(self) -> str:
        payload = {
            "fact_id": self.fact_id,
            "target_id": self.target_id,
            "metric": self.metric,
            "value": str(self.value),
            "unit": self.unit,
            "effective_date": self.effective_date.isoformat(),
            "published_at": self.published_at.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "evidence_id": self.evidence_id,
            "source_ref": self.source_ref,
            "revision_of": self.revision_of,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def calibration_fact_from_evidence(
    evidence: EvidenceRecord,
    *,
    fact_id: str,
    published_at: datetime,
    first_seen_at: datetime,
    revision_of: str | None = None,
) -> NormalizedCalibrationFact:
    if evidence.source_layer is not EvidenceSourceLayer.REALIZED_OR_FILING:
        raise CalibrationFactError(
            "calibration fact lake accepts realized/filing evidence only"
        )
    try:
        value = Decimal(str(evidence.value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationFactError("calibration fact evidence value must be numeric") from exc
    try:
        effective_date = date.fromisoformat(evidence.effective_date[:10])
    except ValueError as exc:
        raise CalibrationFactError("calibration fact effective date must be ISO date") from exc
    fact = NormalizedCalibrationFact(
        fact_id=fact_id,
        target_id=evidence.target,
        metric=evidence.metric,
        value=value,
        unit=evidence.unit,
        effective_date=effective_date,
        published_at=published_at,
        first_seen_at=first_seen_at,
        evidence_id=evidence.id,
        source_ref=evidence.source_ref,
        revision_of=revision_of,
    )
    fact.validate()
    return fact


class CalibrationFactLake:
    """Append-only normalized fact store with revision and knowledge-time replay."""

    def __init__(self) -> None:
        self._facts: dict[str, NormalizedCalibrationFact] = {}

    @property
    def facts(self) -> tuple[NormalizedCalibrationFact, ...]:
        return tuple(self._facts.values())

    def append(self, fact: NormalizedCalibrationFact) -> None:
        fact.validate()
        if fact.fact_id in self._facts:
            raise CalibrationFactError(f"duplicate calibration fact_id: {fact.fact_id}")
        if fact.revision_of is not None:
            prior = self._facts.get(fact.revision_of)
            if prior is None:
                raise CalibrationFactError("calibration fact revision references unknown fact")
            if (
                prior.target_id != fact.target_id
                or prior.metric != fact.metric
                or prior.effective_date != fact.effective_date
            ):
                raise CalibrationFactError(
                    "calibration fact revision must preserve target, metric and effective date"
                )
            if fact.first_seen_at <= prior.first_seen_at:
                raise CalibrationFactError(
                    "calibration fact revision must be first seen after prior fact"
                )
            if any(item.revision_of == prior.fact_id for item in self._facts.values()):
                raise CalibrationFactError(
                    "calibration fact may have only one direct revision"
                )
        self._facts[fact.fact_id] = fact

    def visible_facts(self, *, cutoff: datetime) -> tuple[NormalizedCalibrationFact, ...]:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise CalibrationFactError("fact-lake replay cutoff must be timezone-aware")
        visible = tuple(
            item
            for item in self._facts.values()
            if item.published_at <= cutoff and item.first_seen_at <= cutoff
        )
        superseded = {
            item.revision_of for item in visible if item.revision_of is not None
        }
        return tuple(
            sorted(
                (item for item in visible if item.fact_id not in superseded),
                key=lambda value: (
                    value.target_id,
                    value.metric,
                    value.effective_date,
                    value.first_seen_at,
                    value.fact_id,
                ),
            )
        )

    def exact_fact(
        self,
        *,
        target_id: str,
        metric: str,
        effective_date: date,
        cutoff: datetime,
    ) -> NormalizedCalibrationFact:
        matches = tuple(
            item
            for item in self.visible_facts(cutoff=cutoff)
            if item.target_id == target_id
            and item.metric == metric
            and item.effective_date == effective_date
        )
        if len(matches) != 1:
            raise CalibrationFactError(
                f"expected exactly one visible fact for {target_id}/{metric}/"
                f"{effective_date.isoformat()}, got {len(matches)}"
            )
        return matches[0]

    def snapshot_hash(self, *, cutoff: datetime) -> str:
        payload = [
            {
                "fact_id": item.fact_id,
                "content_hash": item.content_hash,
            }
            for item in self.visible_facts(cutoff=cutoff)
        ]
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class MetricChangeOperator(str, Enum):
    RELATIVE_CHANGE_AT_MOST = "relative_change_at_most"
    RELATIVE_CHANGE_AT_LEAST = "relative_change_at_least"
    DELTA_AT_MOST = "delta_at_most"
    DELTA_AT_LEAST = "delta_at_least"
    LEVEL_AT_MOST = "level_at_most"
    LEVEL_AT_LEAST = "level_at_least"


@dataclass(frozen=True)
class MetricChangeEventRule:
    event_class: str
    horizon: str
    metric: str
    operator: MetricChangeOperator
    threshold: Decimal
    derivation_version: str

    def validate(self) -> None:
        if not all(
            (
                self.event_class,
                self.horizon,
                self.metric,
                self.derivation_version,
            )
        ):
            raise CalibrationFactError("metric-change event rule is incomplete")
        if not self.threshold.is_finite():
            raise CalibrationFactError("metric-change threshold must be finite")


@dataclass(frozen=True)
class DerivedCalibrationOutcome:
    event_key: str
    company_id: str
    event_class: str
    horizon: str
    occurred: bool
    observed_at: datetime
    evidence_ids: tuple[str, ...]
    derivation_version: str
    measured_value: Decimal

    def validate(self) -> None:
        if not all(
            (
                self.event_key,
                self.company_id,
                self.event_class,
                self.horizon,
                self.evidence_ids,
                self.derivation_version,
            )
        ):
            raise CalibrationFactError("derived calibration outcome is incomplete")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise CalibrationFactError("derived outcome observed_at must be timezone-aware")
        if not self.measured_value.is_finite():
            raise CalibrationFactError("derived outcome measured value must be finite")


def derive_metric_change_outcome(
    lake: CalibrationFactLake,
    *,
    event_key: str,
    target_id: str,
    origin_effective_date: date,
    outcome_effective_date: date,
    cutoff: datetime,
    rule: MetricChangeEventRule,
) -> DerivedCalibrationOutcome:
    rule.validate()
    if outcome_effective_date <= origin_effective_date:
        raise CalibrationFactError("outcome effective date must follow origin")
    origin = lake.exact_fact(
        target_id=target_id,
        metric=rule.metric,
        effective_date=origin_effective_date,
        cutoff=cutoff,
    )
    outcome = lake.exact_fact(
        target_id=target_id,
        metric=rule.metric,
        effective_date=outcome_effective_date,
        cutoff=cutoff,
    )
    if origin.unit != outcome.unit:
        raise CalibrationFactError("metric-change facts must use the same canonical unit")

    operator = rule.operator
    if operator in {
        MetricChangeOperator.RELATIVE_CHANGE_AT_MOST,
        MetricChangeOperator.RELATIVE_CHANGE_AT_LEAST,
    }:
        if origin.value == 0:
            raise CalibrationFactError("relative metric change is undefined from zero")
        measured = (outcome.value - origin.value) / abs(origin.value)
    elif operator in {
        MetricChangeOperator.DELTA_AT_MOST,
        MetricChangeOperator.DELTA_AT_LEAST,
    }:
        measured = outcome.value - origin.value
    else:
        measured = outcome.value

    if operator in {
        MetricChangeOperator.RELATIVE_CHANGE_AT_MOST,
        MetricChangeOperator.DELTA_AT_MOST,
        MetricChangeOperator.LEVEL_AT_MOST,
    }:
        occurred = measured <= rule.threshold
    else:
        occurred = measured >= rule.threshold

    derived = DerivedCalibrationOutcome(
        event_key=event_key,
        company_id=target_id,
        event_class=rule.event_class,
        horizon=rule.horizon,
        occurred=occurred,
        observed_at=max(origin.first_seen_at, outcome.first_seen_at),
        evidence_ids=(origin.evidence_id, outcome.evidence_id),
        derivation_version=rule.derivation_version,
        measured_value=measured,
    )
    derived.validate()
    return derived
