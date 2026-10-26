"""Typed post-freeze broker references, not intrinsic valuation inputs.

Report recommendations, target availability and evidence quality are separate.
An anonymous aggregate is a dated observation, never a fabricated named report.
The caller owns source/artifact verification and the same-run freeze gate; this
module preserves their bindings and performs only compatible-reference math.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .post_freeze import ReferenceGapEnvelope, _envelope
from .runtime_authority import DecisionDomain, forbid_llm_decision
from .valuation_execution import GenericValuationResult, IntrinsicValuationScope


class BrokerReferenceKind(str, Enum):
    NAMED_REPORT = "NAMED_REPORT"
    AGGREGATE_SNAPSHOT = "AGGREGATE_SNAPSHOT"


class BrokerClaimType(str, Enum):
    REALIZED_FACT = "REALIZED_FACT"
    COMPANY_GUIDANCE = "COMPANY_GUIDANCE"
    BROKER_FORECAST = "BROKER_FORECAST"
    METHOD_ASSUMPTION = "METHOD_ASSUMPTION"
    INDUSTRY_MECHANISM = "INDUSTRY_MECHANISM"
    TARGET_PRICE = "TARGET_PRICE"
    RATING = "RATING"
    TARGET_MULTIPLE = "TARGET_MULTIPLE"


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string; use UNKNOWN when unknown")
    return value.strip()


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be a finite number")
    return result


def _timestamp(value: str, label: str, *, timezone_required: bool = False,
               end_of_day: bool = False) -> datetime:
    value = _text(value, label)
    try:
        if len(value) == 10:
            if timezone_required:
                raise ValueError("date-only observation")
            parsed_date = date.fromisoformat(value)
            return datetime.combine(parsed_date, time.max if end_of_day else time.min,
                                    tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be ISO date or timezone-aware timestamp" if not timezone_required
                         else f"{label} must be a timezone-aware ISO timestamp") from exc


def _hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a SHA-256 digest")


def _ids(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"{label} must be a tuple of non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")


def _optional_text(value: Any, label: str) -> None:
    if value is not None:
        _text(value, label)


@dataclass(frozen=True)
class BrokerMetricBasis:
    metric: str
    period_start: str
    period_end: str
    frequency: str
    scope: str
    perimeter: str
    accounting_basis: str
    unit: str
    currency: str

    def __post_init__(self) -> None:
        for field in fields(self):
            _text(getattr(self, field.name), f"metric basis {field.name}")
        for name in ("period_start", "period_end"):
            value = getattr(self, name)
            if value != "UNKNOWN":
                date.fromisoformat(value)
        if self.period_start != "UNKNOWN" and self.period_end != "UNKNOWN":
            if self.period_start > self.period_end:
                raise ValueError("metric period_start is after period_end")


@dataclass(frozen=True)
class BrokerMetric:
    metric_id: str
    basis: BrokerMetricBasis
    value: Decimal | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    source_locator: str = ""

    def __post_init__(self) -> None:
        _text(self.metric_id, "metric_id")
        if not isinstance(self.basis, BrokerMetricBasis):
            raise TypeError("metric basis must be BrokerMetricBasis")
        if self.value is not None:
            if self.low is not None or self.high is not None:
                raise ValueError("metric must carry a point OR a range, not both")
            object.__setattr__(self, "value", _decimal(self.value, "metric value"))
        elif self.low is not None and self.high is not None:
            object.__setattr__(self, "low", _decimal(self.low, "metric low"))
            object.__setattr__(self, "high", _decimal(self.high, "metric high"))
            if self.low > self.high:
                raise ValueError("metric low exceeds high")
        else:
            raise ValueError("metric requires a point or both range bounds")
        if not isinstance(self.source_locator, str):
            raise ValueError("metric source_locator must be a string")

    @property
    def bounds(self) -> tuple[Decimal, Decimal]:
        return (self.value, self.value) if self.value is not None else (self.low, self.high)


@dataclass(frozen=True)
class BrokerClaim:
    claim_id: str
    claim_type: BrokerClaimType
    summary: str
    metric: BrokerMetric | None = None
    source_locator: str = ""
    underlying_source_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.claim_id, "claim_id")
        object.__setattr__(self, "claim_type", BrokerClaimType(self.claim_type))
        _text(self.summary, "claim summary")
        if self.metric is not None and not isinstance(self.metric, BrokerMetric):
            raise TypeError("claim metric must be BrokerMetric")
        if not self.source_locator and (self.metric is None or not self.metric.source_locator):
            raise ValueError("claim requires a source locator")
        _ids(self.underlying_source_families, "claim underlying_source_families")


@dataclass(frozen=True)
class BrokerReference:
    reference_id: str
    reference_kind: BrokerReferenceKind
    provider_name: str
    source_ref: str
    content_sha256: str
    authorization_basis: str
    observed_at: str
    original_published_at: str | None = None
    snapshot_as_of: str | None = None
    last_rating_at: str | None = None
    available_at: str | None = None
    broker: str | None = None
    analyst: str | None = None
    rating: str | None = None
    target_price: Decimal | None = None
    target_currency: str | None = None
    target_horizon: str | None = None
    target_horizon_end: str | None = None
    target_share_basis: str = "UNKNOWN"
    target_comparison_eligible: bool = True
    target_withheld_reason: str = ""
    valuation_method: str | None = None
    valuation_base_year: str | None = None
    access_quality: str = "UNKNOWN"
    claims: tuple[BrokerClaim, ...] = ()
    underlying_source_families: tuple[str, ...] = ()
    aggregate_report_count: int | None = None
    aggregate_median_target_price: Decimal | None = None
    aggregate_min_target_price: Decimal | None = None
    aggregate_max_target_price: Decimal | None = None

    def __post_init__(self) -> None:
        from .source_reporting import canonical_verification_url

        for name in ("reference_id", "provider_name", "target_share_basis", "access_quality"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "reference_kind", BrokerReferenceKind(self.reference_kind))
        _text(self.source_ref, "source_ref")
        if canonical_verification_url(self.source_ref) is None:
            raise ValueError("broker source_ref must be a credential-free HTTP(S) URL")
        _hash(self.content_sha256, "reference content_sha256")
        if self.authorization_basis not in {"explicit_permission", "licensed_export", "public_access"}:
            raise ValueError("broker reference requires explicit rights/authorization basis")
        observed = _timestamp(self.observed_at, "observed_at", timezone_required=True)
        for name in ("original_published_at", "snapshot_as_of", "last_rating_at", "available_at"):
            value = getattr(self, name)
            if value is not None and _timestamp(value, name) > observed:
                raise ValueError(f"{name} cannot be after observed_at")
        if self.available_at is not None and self.original_published_at is not None:
            if _timestamp(self.available_at, "available_at") < _timestamp(self.original_published_at, "original_published_at"):
                raise ValueError("available_at cannot precede original publication")
        for name in ("broker", "analyst", "rating", "target_currency", "target_horizon",
                     "valuation_method", "valuation_base_year"):
            _optional_text(getattr(self, name), name)
        if self.reference_kind is BrokerReferenceKind.NAMED_REPORT and self.broker is None:
            raise ValueError("NAMED_REPORT requires a named broker; use AGGREGATE_SNAPSHOT for unattributed aggregate")
        if self.target_horizon_end is not None:
            date.fromisoformat(self.target_horizon_end)
        if self.target_price is not None:
            object.__setattr__(self, "target_price", _decimal(self.target_price, "target_price"))
            if self.target_price <= 0 or not self.target_currency:
                raise ValueError("numeric target requires positive price and currency")
        elif self.target_currency is not None:
            raise ValueError("target_currency without numeric target is ambiguous")
        if not isinstance(self.target_comparison_eligible, bool):
            raise ValueError("target_comparison_eligible must be boolean")
        if not self.target_comparison_eligible and not self.target_withheld_reason.strip():
            raise ValueError("withheld numeric target requires an explicit reason")
        if not isinstance(self.claims, tuple) or any(not isinstance(c, BrokerClaim) for c in self.claims):
            raise TypeError("reference claims must be a tuple of BrokerClaim")
        if len({c.claim_id for c in self.claims}) != len(self.claims):
            raise ValueError("duplicate claim IDs in reference")
        _ids(self.underlying_source_families, "reference underlying_source_families")
        stats = (self.aggregate_median_target_price, self.aggregate_min_target_price,
                 self.aggregate_max_target_price)
        if self.reference_kind is BrokerReferenceKind.NAMED_REPORT:
            if self.aggregate_report_count is not None or any(x is not None for x in stats):
                raise ValueError("aggregate statistics require AGGREGATE_SNAPSHOT")
        elif self.aggregate_report_count is not None:
            if isinstance(self.aggregate_report_count, bool) or not isinstance(self.aggregate_report_count, int) or self.aggregate_report_count < 1:
                raise ValueError("aggregate_report_count must be a positive integer")
            if self.target_price is not None and any(x is None for x in stats):
                raise ValueError("numeric aggregate requires complete median/min/max statistics")
        elif self.target_price is not None or any(x is not None for x in stats):
            raise ValueError("numeric aggregate requires aggregate_report_count")
        if any(x is not None for x in stats):
            if any(x is None for x in stats):
                raise ValueError("aggregate statistics cannot be partially supplied")
            for name in ("aggregate_median_target_price", "aggregate_min_target_price", "aggregate_max_target_price"):
                object.__setattr__(self, name, _decimal(getattr(self, name), name))
            if not 0 < self.aggregate_min_target_price <= self.aggregate_median_target_price <= self.aggregate_max_target_price:
                raise ValueError("aggregate range/median is inconsistent")
            if self.target_price is not None and not self.aggregate_min_target_price <= self.target_price <= self.aggregate_max_target_price:
                raise ValueError("aggregate mean is outside the supplied range")

    @property
    def reference_as_of(self) -> str:
        """The date basis is explicit; observing an aggregate never publishes it."""
        if self.reference_kind is BrokerReferenceKind.AGGREGATE_SNAPSHOT:
            return self.snapshot_as_of or self.observed_at
        return self.original_published_at or self.observed_at

    @property
    def reference_date_basis(self) -> str:
        if self.reference_kind is BrokerReferenceKind.AGGREGATE_SNAPSHOT:
            return "SNAPSHOT_AS_OF" if self.snapshot_as_of else "OBSERVED_AT"
        return "ORIGINAL_PUBLICATION" if self.original_published_at else "OBSERVED_AT_ORIGINAL_DATE_UNKNOWN"


@dataclass(frozen=True)
class BrokerReferenceBundle:
    schema_version: str
    target_id: str
    comparison_as_of: str
    references: tuple[BrokerReference, ...]
    search_coverage: str = ""
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != "broker-reference-bundle/v1":
            raise ValueError("unsupported broker reference bundle schema")
        _text(self.target_id, "target_id")
        cutoff = _timestamp(self.comparison_as_of, "comparison_as_of", timezone_required=True)
        if not isinstance(self.references, tuple) or any(not isinstance(r, BrokerReference) for r in self.references):
            raise TypeError("references must be a tuple of BrokerReference")
        ids = [r.reference_id for r in self.references]
        claim_ids = [c.claim_id for r in self.references for c in r.claims]
        if len(ids) != len(set(ids)) or len(claim_ids) != len(set(claim_ids)):
            raise ValueError("reference and claim IDs must be globally unique")
        for reference in self.references:
            if _timestamp(reference.observed_at, "observed_at", timezone_required=True) > cutoff:
                raise ValueError("reference was observed after comparison_as_of")
        if not isinstance(self.search_coverage, str):
            raise ValueError("search_coverage must be a string")
        _ids(self.limitations, "limitations")

    @property
    def reference_hash(self) -> str:
        return broker_content_hash(self)


@dataclass(frozen=True)
class FrozenOperatingMetric:
    metric: BrokerMetric
    scenario_id: str
    artifact_ref: str
    artifact_sha256: str
    json_pointer: str
    freeze_token_hash: str
    valuation_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.metric, BrokerMetric):
            raise TypeError("frozen metric must be BrokerMetric")
        for name in ("scenario_id", "artifact_ref", "freeze_token_hash", "valuation_hash"):
            _text(getattr(self, name), name)
        _hash(self.artifact_sha256, "frozen metric artifact_sha256")
        if not isinstance(self.json_pointer, str) or not self.json_pointer.startswith("/"):
            raise ValueError("frozen metric requires an exact JSON pointer")


@dataclass(frozen=True)
class BrokerMetricComparison:
    reference_id: str
    claim_id: str
    broker_metric: BrokerMetric
    own_metric: FrozenOperatingMetric | None
    status: str
    reasons: tuple[str, ...]
    difference: Decimal | None = None
    difference_low: Decimal | None = None
    difference_high: Decimal | None = None
    difference_pct_of_broker: Decimal | None = None
    candidate_own_metric_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrokerTargetComparison:
    reference_id: str
    reference_kind: BrokerReferenceKind
    reference_as_of: str
    reference_date_basis: str
    target_price: Decimal | None
    target_currency: str | None
    status: str
    reasons: tuple[str, ...]
    envelope: ReferenceGapEnvelope | None = None
    intrinsic_reference_kind: str | None = None
    intrinsic_reference_low: Decimal | None = None
    intrinsic_reference_high: Decimal | None = None
    target_gap_to_low: Decimal | None = None
    target_gap_to_high: Decimal | None = None


@dataclass(frozen=True)
class BrokerComparisonResult:
    schema_version: str
    target_id: str
    intrinsic_as_of: str
    comparison_as_of: str
    freeze_token_hash: str
    valuation_hash: str
    reference_hash: str
    frozen_metrics_hash: str
    references: tuple[BrokerReference, ...]
    metric_comparisons: tuple[BrokerMetricComparison, ...]
    target_comparisons: tuple[BrokerTargetComparison, ...]
    numeric_target_status: str
    research_comparison_status: str
    known_underlying_source_family_count: int
    unknown_underlying_source_reference_count: int
    warnings: tuple[str, ...]
    comparison_hash: str


def canonical_broker_payload(value: Any) -> Any:
    if is_dataclass(value):
        return canonical_broker_payload(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite broker payload")
        return str(value)
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("broker payload mapping keys must be strings")
        return {k: canonical_broker_payload(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_broker_payload(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return str(_decimal(value, "broker payload number"))
    raise TypeError(f"unsupported broker payload type: {type(value).__name__}")


def broker_content_hash(value: Any) -> str:
    return sha256(json.dumps(canonical_broker_payload(value), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _object(payload: Any, cls: type, *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{cls.__name__} requires an object")
    unknown = set(payload) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"{cls.__name__} has unknown fields: {sorted(unknown)}")
    missing = set(required) - set(payload)
    if missing:
        raise ValueError(f"{cls.__name__} missing fields: {sorted(missing)}")
    return dict(payload)


def _tuple(value: Any, label: str) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} requires an array")
    return tuple(value)


def parse_broker_metric(payload: Mapping[str, Any]) -> BrokerMetric:
    raw = _object(payload, BrokerMetric, required=("metric_id", "basis"))
    raw["basis"] = BrokerMetricBasis(**_object(raw["basis"], BrokerMetricBasis,
        required=tuple(f.name for f in fields(BrokerMetricBasis))))
    return BrokerMetric(**raw)


def parse_broker_reference_bundle(payload: Mapping[str, Any]) -> BrokerReferenceBundle:
    raw = _object(payload, BrokerReferenceBundle,
                  required=("schema_version", "target_id", "comparison_as_of", "references"))
    references = []
    for value in _tuple(raw["references"], "references"):
        ref = _object(value, BrokerReference, required=("reference_id", "reference_kind", "provider_name",
            "source_ref", "content_sha256", "authorization_basis", "observed_at"))
        claims = []
        for claim_value in _tuple(ref.get("claims", ()), "claims"):
            claim = _object(claim_value, BrokerClaim, required=("claim_id", "claim_type", "summary"))
            if claim.get("metric") is not None:
                claim["metric"] = parse_broker_metric(claim["metric"])
            claim["underlying_source_families"] = _tuple(claim.get("underlying_source_families", ()), "claim source families")
            claims.append(BrokerClaim(**claim))
        ref["claims"] = tuple(claims)
        ref["underlying_source_families"] = _tuple(ref.get("underlying_source_families", ()), "reference source families")
        references.append(BrokerReference(**ref))
    raw["references"] = tuple(references)
    raw["limitations"] = _tuple(raw.get("limitations", ()), "limitations")
    return BrokerReferenceBundle(**raw)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate broker JSON key: {key}")
        result[key] = value
    return result


def load_broker_reference_bundle(path: str | Path) -> BrokerReferenceBundle:
    return parse_broker_reference_bundle(json.loads(Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object, parse_float=Decimal,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {value}"))))


def parse_frozen_operating_metrics(payload: Any) -> tuple[FrozenOperatingMetric, ...]:
    result = []
    for value in _tuple(payload, "frozen operating metrics"):
        raw = _object(value, FrozenOperatingMetric, required=tuple(f.name for f in fields(FrozenOperatingMetric)))
        raw["metric"] = parse_broker_metric(raw["metric"])
        result.append(FrozenOperatingMetric(**raw))
    keys = [(r.scenario_id, r.metric.metric_id) for r in result]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate frozen metric identity")
    return tuple(result)


def _metric_row(reference_id: str, claim: BrokerClaim,
                own: FrozenOperatingMetric) -> BrokerMetricComparison:
    broker = claim.metric
    reasons = []
    for field in fields(BrokerMetricBasis):
        left, right = getattr(broker.basis, field.name), getattr(own.metric.basis, field.name)
        if left.strip().casefold() in {"unknown", "undisclosed", "미확인", "미공개"} or right.strip().casefold() in {"unknown", "undisclosed", "미확인", "미공개"}:
            reasons.append(f"{field.name}: UNKNOWN")
        elif left != right:
            reasons.append(f"{field.name}: broker={left}; own={right}")
    if reasons:
        status = "BASIS_UNKNOWN" if any("UNKNOWN" in r for r in reasons) else "BASIS_DIFFERENT"
        return BrokerMetricComparison(reference_id, claim.claim_id, broker, own, status, tuple(reasons))
    low, high = broker.bounds
    own_low, own_high = own.metric.bounds
    difference = own.metric.value - broker.value if own.metric.value is not None and broker.value is not None else None
    percent = (difference / broker.value if difference is not None and broker.value > 0
               and own.metric.value >= 0 else None)
    return BrokerMetricComparison(reference_id, claim.claim_id, broker, own, "COMPARABLE", (),
        difference, own_low - high, own_high - low, percent)


def _target_row(reference: BrokerReference, valuation: object, comparison_as_of: str) -> BrokerTargetComparison:
    common = dict(reference_id=reference.reference_id, reference_kind=reference.reference_kind,
        reference_as_of=reference.reference_as_of, reference_date_basis=reference.reference_date_basis,
        target_price=reference.target_price, target_currency=reference.target_currency)
    if reference.target_price is None:
        return BrokerTargetComparison(**common, status="NOT_APPLICABLE_NO_NUMERIC_TARGET",
                                      reasons=("No positive numeric target is published/verified in this reference.",))
    if not reference.target_comparison_eligible:
        return BrokerTargetComparison(**common, status="WITHHELD", reasons=(reference.target_withheld_reason,))
    if reference.target_horizon_end and date.fromisoformat(reference.target_horizon_end) < _timestamp(comparison_as_of, "comparison_as_of").date():
        return BrokerTargetComparison(**common, status="EXPIRED_REFERENCE", reasons=("The declared target horizon has expired.",))
    if valuation is None:
        return BrokerTargetComparison(**common, status="VALUATION_UNAVAILABLE", reasons=("No frozen valuation supplied.",))
    if reference.target_currency != getattr(valuation, "reporting_unit", None):
        return BrokerTargetComparison(**common, status="CURRENCY_DIFFERENT", reasons=("No FX conversion was authorized.",))
    limitations = []
    if reference.target_share_basis == "UNKNOWN":
        limitations.append("Target share/corporate-action basis is unknown; arithmetic is a conditional reference, not a normalized target.")
    if reference.original_published_at is None:
        limitations.append("Original report publication is unknown; the declared snapshot/observation time is not a report publication date.")
    if reference.valuation_method is None:
        limitations.append("Original valuation method is undisclosed.")
    if isinstance(valuation, GenericValuationResult):
        if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
            return BrokerTargetComparison(**common, status="PARTIAL_INTRINSIC_WITHHELD", reasons=("A segment subtotal is not a whole-company target.",))
        envelope = _envelope(valuation,
            reference_name=f"{reference.reference_kind.value}: {reference.provider_name} ({reference.reference_id})",
            reference_as_of=reference.reference_as_of, reference_value=reference.target_price,
            currency=reference.target_currency)
        return BrokerTargetComparison(**common, status="COMPARED_WITH_LIMITATIONS" if limitations else "COMPARED",
                                      reasons=tuple(limitations), envelope=envelope)
    from .distributional_runtime import DistributionalPrimaryValuationResult
    if isinstance(valuation, DistributionalPrimaryValuationResult):
        from .distributional_reporting import _intrinsic_range
        kind, low, high = _intrinsic_range(valuation)
        return BrokerTargetComparison(**common, status="COMPARED_WITH_LIMITATIONS" if limitations else "COMPARED",
            reasons=tuple(limitations), intrinsic_reference_kind=kind,
            intrinsic_reference_low=low, intrinsic_reference_high=high,
            target_gap_to_low=reference.target_price - low, target_gap_to_high=reference.target_price - high)
    raise TypeError("broker target comparison requires an existing frozen valuation result type")


def build_broker_comparison(bundle: BrokerReferenceBundle, *,
                            frozen_metrics: tuple[FrozenOperatingMetric, ...] = (),
                            valuation: object = None, freeze_token_hash: str,
                            intrinsic_as_of: str) -> BrokerComparisonResult:
    """Compare references without updating any assumption, probability or value.

    Unknown provenance/bases remain visible limitations. Numeric mismatches are
    not repaired by selecting a nearby period, substituting EBIT for FCFF, or
    converting currency. Artifact bytes/pointers must be verified by the caller.
    """
    forbid_llm_decision(DecisionDomain.POST_FREEZE)
    if not isinstance(bundle, BrokerReferenceBundle):
        raise TypeError("bundle must be BrokerReferenceBundle")
    _text(freeze_token_hash, "freeze_token_hash")
    intrinsic_cutoff = _timestamp(intrinsic_as_of, "intrinsic_as_of", end_of_day=True)
    comparison_cutoff = _timestamp(bundle.comparison_as_of, "comparison_as_of", timezone_required=True)
    if ((len(intrinsic_as_of) == 10 and comparison_cutoff.date() < intrinsic_cutoff.date())
            or (len(intrinsic_as_of) != 10 and comparison_cutoff < intrinsic_cutoff)):
        raise ValueError("comparison_as_of precedes intrinsic freeze cutoff")
    value_hash = str(getattr(valuation, "valuation_hash", "")
                     or getattr(getattr(valuation, "envelope", None), "envelope_hash", ""))
    if not isinstance(frozen_metrics, tuple) or any(not isinstance(x, FrozenOperatingMetric) for x in frozen_metrics):
        raise TypeError("frozen_metrics must be a tuple of FrozenOperatingMetric")
    metric_keys = [(own.scenario_id, own.metric.metric_id) for own in frozen_metrics]
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("duplicate frozen metric identity")
    if valuation is None and frozen_metrics:
        hashes = {own.valuation_hash for own in frozen_metrics}
        if len(hashes) != 1:
            raise ValueError("frozen metrics belong to different valuations")
        value_hash = next(iter(hashes))
    for own in frozen_metrics:
        if own.freeze_token_hash != freeze_token_hash:
            raise ValueError("frozen metric belongs to a different freeze token")
        if value_hash and own.valuation_hash != value_hash:
            raise ValueError("frozen metric belongs to a different valuation")
    rows = []
    targets = []
    families: set[str] = set()
    family_references: dict[str, set[str]] = {}
    unknown_families = 0
    warnings = list(bundle.limitations)
    for reference in bundle.references:
        source_time = reference.available_at or reference.original_published_at or reference.observed_at
        if _timestamp(source_time, "source availability") > intrinsic_cutoff:
            warnings.append(f"{reference.reference_id}: later comparison evidence; not knowable at the intrinsic cutoff and not retroactive valuation input.")
        if reference.original_published_at is None:
            warnings.append(f"{reference.reference_id}: original publication date unknown.")
        known_families = set(reference.underlying_source_families)
        known_families.update(f for claim in reference.claims for f in claim.underlying_source_families)
        if not known_families:
            unknown_families += 1
        for family in known_families:
            families.add(family)
            family_references.setdefault(family, set()).add(reference.reference_id)
        targets.append(_target_row(reference, valuation, bundle.comparison_as_of))
        for claim in reference.claims:
            if claim.metric is None:
                continue
            candidates = [own for own in frozen_metrics if own.metric.basis.metric == claim.metric.basis.metric]
            if not candidates:
                rows.append(BrokerMetricComparison(reference.reference_id, claim.claim_id, claim.metric, None,
                    "OWN_METRIC_UNAVAILABLE", ("No same-definition own frozen metric was supplied; no proxy substituted.",)))
                continue
            period_candidates = [own for own in candidates if
                own.metric.basis.period_start == claim.metric.basis.period_start and
                own.metric.basis.period_end == claim.metric.basis.period_end]
            if not period_candidates:
                rows.append(BrokerMetricComparison(reference.reference_id, claim.claim_id, claim.metric, None,
                    "PERIOD_DIFFERENT", ("No same-period own metric; annual/quarterly or neighboring-year proxies are not substituted.",),
                    candidate_own_metric_ids=tuple(x.metric.metric_id for x in candidates)))
                continue
            rows.extend(_metric_row(reference.reference_id, claim, own) for own in period_candidates)
    for family, refs in sorted(family_references.items()):
        if len(refs) > 1:
            warnings.append(f"Underlying source family {family} is shared by {len(refs)} references; broker count is not independent evidence count.")
    if any(row.status not in {"COMPARABLE"} for row in rows):
        warnings.append("Some forecasts are only juxtaposed because metric/period/basis evidence is incomplete or incompatible.")
    has_numeric = any(r.target_price is not None for r in bundle.references)
    compared_numeric = any(t.status in {"COMPARED", "COMPARED_WITH_LIMITATIONS"} for t in targets)
    numeric_status = ("COMPARED" if compared_numeric else "WITHHELD" if has_numeric
                      else "NOT_APPLICABLE_NO_NUMERIC_TARGET")
    research_status = ("COMPARED" if any(r.claims for r in bundle.references)
                       else "TARGET_ONLY" if has_numeric
                       else "REFERENCE_METADATA_ONLY" if bundle.references else "NO_REFERENCES")
    body = dict(schema_version="broker-comparison-result/v1", target_id=bundle.target_id,
        intrinsic_as_of=intrinsic_as_of, comparison_as_of=bundle.comparison_as_of,
        freeze_token_hash=freeze_token_hash, valuation_hash=value_hash,
        reference_hash=bundle.reference_hash, frozen_metrics_hash=broker_content_hash(frozen_metrics),
        references=bundle.references, metric_comparisons=tuple(rows), target_comparisons=tuple(targets),
        numeric_target_status=numeric_status, research_comparison_status=research_status,
        known_underlying_source_family_count=len(families),
        unknown_underlying_source_reference_count=unknown_families,
        warnings=tuple(dict.fromkeys(warnings)))
    return BrokerComparisonResult(**body, comparison_hash=broker_content_hash(body))


__all__ = ["BrokerReferenceKind", "BrokerClaimType", "BrokerMetricBasis", "BrokerMetric",
    "BrokerClaim", "BrokerReference", "BrokerReferenceBundle", "FrozenOperatingMetric",
    "BrokerMetricComparison", "BrokerTargetComparison", "BrokerComparisonResult",
    "canonical_broker_payload", "broker_content_hash", "parse_broker_metric",
    "parse_broker_reference_bundle", "load_broker_reference_bundle", "parse_frozen_operating_metrics",
    "build_broker_comparison"]
