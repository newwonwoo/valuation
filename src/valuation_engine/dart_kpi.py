from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Pattern

from .actual_units import Measure
from .dart_documents import DartDocumentMember, DartOriginalFilingDocument
from .records import EvidenceRecord, EvidenceSourceLayer


_NORMALIZATION_VERSION = "DART_VISIBLE_TEXT_V1"


class DartKPIExtractionError(ValueError):
    pass


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return _normalize_space(" ".join(self.parts))


@dataclass(frozen=True)
class DartKPIExtractionSpec:
    metric: str
    segment: str
    member_path_pattern: str
    value_pattern: str
    canonical_unit: str
    effective_date: str
    locator_label: str
    source_unit_map: tuple[tuple[str, str], ...]
    critical: bool = False

    def validate(self) -> None:
        if not all(
            (
                self.metric,
                self.segment,
                self.member_path_pattern,
                self.value_pattern,
                self.canonical_unit,
                self.effective_date,
                self.locator_label,
                self.source_unit_map,
            )
        ):
            raise DartKPIExtractionError(
                "DART KPI extraction spec requires metric, segment, member path, "
                "value pattern, canonical unit, effective date, locator label and "
                "source-unit mapping"
            )
        _validate_exact_iso_date(
            self.effective_date,
            label="DART KPI extraction effective_date",
        )
        member_pattern = _compile_regex(
            self.member_path_pattern,
            label="member_path_pattern",
        )
        value_pattern = _compile_regex(
            self.value_pattern,
            label="value_pattern",
        )
        missing_groups = tuple(
            name for name in ("value", "unit") if name not in value_pattern.groupindex
        )
        if missing_groups:
            raise DartKPIExtractionError(
                "DART KPI value_pattern requires named (?P<value>...) and "
                "(?P<unit>...) captures"
            )
        if member_pattern.match(""):
            raise DartKPIExtractionError(
                "member_path_pattern may not match an empty path"
            )

        tokens = tuple(token for token, _ in self.source_unit_map)
        if any(not token for token in tokens) or len(tokens) != len(set(tokens)):
            raise DartKPIExtractionError(
                "DART KPI source_unit_map requires unique non-empty source tokens"
            )
        try:
            canonical = Measure(
                Decimal("0"),
                self.canonical_unit,
                self.effective_date,
            )
            for token, source_unit in self.source_unit_map:
                if not source_unit:
                    raise DartKPIExtractionError(
                        f"DART KPI source unit mapping is blank for token {token!r}"
                    )
                source = Measure(
                    Decimal("0"),
                    source_unit,
                    self.effective_date,
                )
                source.convert_to(canonical.unit)
        except ValueError as exc:
            raise DartKPIExtractionError(
                f"DART KPI unit mapping is invalid: {exc}"
            ) from exc

    def source_unit_for(self, token: str) -> str:
        for source_token, source_unit in self.source_unit_map:
            if token == source_token:
                return source_unit
        raise DartKPIExtractionError(
            f"DART KPI source unit token is not mapped: {token!r}"
        )


@dataclass(frozen=True)
class DartKPIObservation:
    metric: str
    segment: str
    measure: Measure
    rcept_no: str
    member_path: str
    member_content_hash: str
    normalized_text_hash: str
    normalization_version: str
    source_ref: str
    text_start: int
    text_end: int
    matched_text: str
    source_unit_token: str
    source_unit: str
    locator_label: str
    critical: bool

    def validate(self) -> None:
        if not all(
            (
                self.metric,
                self.segment,
                self.rcept_no,
                self.member_path,
                self.member_content_hash,
                self.normalized_text_hash,
                self.normalization_version,
                self.source_ref,
                self.matched_text,
                self.source_unit_token,
                self.source_unit,
                self.locator_label,
            )
        ):
            raise DartKPIExtractionError("DART KPI observation is incomplete")
        if self.text_start < 0 or self.text_end <= self.text_start:
            raise DartKPIExtractionError("DART KPI observation text span is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.member_content_hash):
            raise DartKPIExtractionError(
                "DART KPI observation requires member SHA-256"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.normalized_text_hash):
            raise DartKPIExtractionError(
                "DART KPI observation requires normalized-text SHA-256"
            )
        if self.normalization_version != _NORMALIZATION_VERSION:
            raise DartKPIExtractionError(
                "DART KPI observation uses an unsupported normalization version"
            )
        _validate_exact_iso_date(
            self.measure.as_of,
            label="DART KPI observation effective date",
        )

    @property
    def observation_hash(self) -> str:
        payload = "|".join(
            (
                self.metric,
                self.segment,
                str(self.measure.amount),
                self.measure.unit,
                self.measure.as_of,
                self.rcept_no,
                self.member_path,
                self.member_content_hash,
                self.normalized_text_hash,
                self.normalization_version,
                str(self.text_start),
                str(self.text_end),
                self.matched_text,
                self.source_unit_token,
                self.source_unit,
                self.locator_label,
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()


def extract_dart_kpi(
    filing: DartOriginalFilingDocument,
    spec: DartKPIExtractionSpec,
) -> DartKPIObservation:
    filing.validate()
    spec.validate()
    member_pattern = re.compile(spec.member_path_pattern)
    value_pattern = re.compile(spec.value_pattern, flags=re.MULTILINE | re.DOTALL)

    matches: list[
        tuple[
            DartDocumentMember,
            str,
            re.Match[str],
        ]
    ] = []
    for member in filing.text_members:
        if member_pattern.fullmatch(member.path) is None:
            continue
        plain_text = _visible_text(member)
        for match in value_pattern.finditer(plain_text):
            matches.append((member, plain_text, match))

    if not matches:
        raise DartKPIExtractionError(
            f"DART KPI {spec.segment}/{spec.metric} matched no filing location"
        )
    if len(matches) != 1:
        rendered = ", ".join(
            f"{member.path}@{match.start()}:{match.end()}"
            for member, _, match in matches
        )
        raise DartKPIExtractionError(
            f"DART KPI {spec.segment}/{spec.metric} is ambiguous; "
            f"expected exactly one match, got {len(matches)}: {rendered}"
        )

    member, plain_text, match = matches[0]
    raw_value = match.group("value")
    source_unit_token = match.group("unit")
    amount = _parse_decimal(raw_value)
    source_unit = spec.source_unit_for(source_unit_token)
    try:
        source_measure = Measure(
            amount,
            source_unit,
            spec.effective_date,
        )
        measure = source_measure.convert_to(spec.canonical_unit)
    except ValueError as exc:
        raise DartKPIExtractionError(
            f"DART KPI unit conversion failed: {exc}"
        ) from exc
    matched_text = plain_text[match.start() : match.end()]
    normalized_text_hash = sha256(plain_text.encode("utf-8")).hexdigest()
    observation = DartKPIObservation(
        metric=spec.metric,
        segment=spec.segment,
        measure=measure,
        rcept_no=filing.rcept_no,
        member_path=member.path,
        member_content_hash=member.content_hash,
        normalized_text_hash=normalized_text_hash,
        normalization_version=_NORMALIZATION_VERSION,
        source_ref=filing.source_ref,
        text_start=match.start(),
        text_end=match.end(),
        matched_text=matched_text,
        source_unit_token=source_unit_token,
        source_unit=source_unit,
        locator_label=spec.locator_label,
        critical=spec.critical,
    )
    observation.validate()
    return observation


def dart_kpi_observation_to_evidence(
    observation: DartKPIObservation,
    *,
    target_id: str,
    observed_date: str,
    source_grade: str = "A",
    confidence: float = 1.0,
) -> EvidenceRecord:
    observation.validate()
    if not target_id:
        raise DartKPIExtractionError("target_id is required")
    _validate_exact_iso_date(observed_date, label="observed_date")
    if not source_grade:
        raise DartKPIExtractionError("source_grade is required")
    locator = (
        f"{observation.source_ref}#member={observation.member_path}"
        f"&member_sha256={observation.member_content_hash}"
        f"&normalization={observation.normalization_version}"
        f"&normalized_sha256={observation.normalized_text_hash}"
        f"&normalized_span={observation.text_start}:{observation.text_end}"
    )
    evidence_id = (
        f"DARTKPI:{observation.rcept_no}:{observation.segment}:"
        f"{observation.metric}:{observation.observation_hash[:16]}"
    )
    amount = observation.measure.amount
    integral = amount.to_integral_value()
    # Preserve exact precision while keeping Evidence snapshots JSON-serializable,
    # matching the dart_facts convention (int when integral, decimal string otherwise).
    json_safe_value = int(integral) if amount == integral else format(amount, "f")
    return EvidenceRecord(
        id=evidence_id,
        target=target_id,
        metric=observation.metric,
        value=json_safe_value,
        unit=observation.measure.unit,
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date=observation.measure.as_of,
        observed_date=observed_date,
        source_name="OpenDART original filing",
        source_ref=locator,
        source_grade=source_grade,
        confidence=confidence,
        segment=observation.segment,
        notes=(
            f"exact deterministic extraction: {observation.locator_label}; "
            f"source_unit_token={observation.source_unit_token!r}; "
            f"source_unit={observation.source_unit}; "
            f"matched_text={observation.matched_text!r}"
        ),
        critical=observation.critical,
    )


def _visible_text(member: DartDocumentMember) -> str:
    if member.text is None:
        raise DartKPIExtractionError(
            f"DART KPI extraction requires text member: {member.path}"
        )
    parser = _VisibleTextParser()
    try:
        parser.feed(member.text)
        parser.close()
    except Exception as exc:
        raise DartKPIExtractionError(
            f"failed to normalize filing text for {member.path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return parser.text()


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _compile_regex(value: str, *, label: str) -> Pattern[str]:
    try:
        return re.compile(value, flags=re.MULTILINE | re.DOTALL)
    except re.error as exc:
        raise DartKPIExtractionError(
            f"invalid DART KPI {label}: {exc}"
        ) from exc


def _validate_exact_iso_date(value: str, *, label: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise DartKPIExtractionError(f"{label} must be exact YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DartKPIExtractionError(
            f"{label} must be a valid calendar date"
        ) from exc


def _parse_decimal(value: str) -> Decimal:
    text = value.strip().replace(",", "").replace(" ", "")
    if not text:
        raise DartKPIExtractionError("DART KPI value capture is blank")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise DartKPIExtractionError(
            f"DART KPI value capture is not a strict decimal: {value!r}"
        )
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise DartKPIExtractionError(
            f"DART KPI value capture is invalid: {value!r}"
        ) from exc
    if negative:
        amount = -amount
    if not amount.is_finite():
        raise DartKPIExtractionError("DART KPI amount must be finite")
    return amount
