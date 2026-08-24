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
            )
        ):
            raise DartKPIExtractionError(
                "DART KPI extraction spec requires metric, segment, member path, "
                "value pattern, unit, effective date and locator label"
            )
        try:
            date.fromisoformat(self.effective_date[:10])
        except ValueError as exc:
            raise DartKPIExtractionError(
                "DART KPI extraction effective_date must be ISO date"
            ) from exc
        member_pattern = _compile_regex(
            self.member_path_pattern,
            label="member_path_pattern",
        )
        value_pattern = _compile_regex(
            self.value_pattern,
            label="value_pattern",
        )
        if "value" not in value_pattern.groupindex:
            raise DartKPIExtractionError(
                "DART KPI value_pattern requires a named (?P<value>...) capture"
            )
        if member_pattern.match(""):
            raise DartKPIExtractionError(
                "member_path_pattern may not match an empty path"
            )
        Measure(Decimal("0"), self.canonical_unit, self.effective_date)


@dataclass(frozen=True)
class DartKPIObservation:
    metric: str
    segment: str
    measure: Measure
    rcept_no: str
    member_path: str
    member_content_hash: str
    source_ref: str
    text_start: int
    text_end: int
    matched_text: str
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
                self.source_ref,
                self.matched_text,
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
                str(self.text_start),
                str(self.text_end),
                self.matched_text,
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
    amount = _parse_decimal(raw_value)
    measure = Measure(amount, spec.canonical_unit, spec.effective_date)
    matched_text = plain_text[match.start() : match.end()]
    observation = DartKPIObservation(
        metric=spec.metric,
        segment=spec.segment,
        measure=measure,
        rcept_no=filing.rcept_no,
        member_path=member.path,
        member_content_hash=member.content_hash,
        source_ref=filing.source_ref,
        text_start=match.start(),
        text_end=match.end(),
        matched_text=matched_text,
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
    try:
        date.fromisoformat(observed_date[:10])
    except ValueError as exc:
        raise DartKPIExtractionError("observed_date must be ISO date") from exc
    if not source_grade:
        raise DartKPIExtractionError("source_grade is required")
    locator = (
        f"{observation.source_ref}#member={observation.member_path}"
        f"&span={observation.text_start}:{observation.text_end}"
        f"&member_sha256={observation.member_content_hash}"
    )
    evidence_id = (
        f"DARTKPI:{observation.rcept_no}:{observation.segment}:"
        f"{observation.metric}:{observation.observation_hash[:16]}"
    )
    return EvidenceRecord(
        id=evidence_id,
        target=target_id,
        metric=observation.metric,
        value=observation.measure.amount,
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
