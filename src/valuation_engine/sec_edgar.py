from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Callable, Iterable

from .authorized_primary_sources import (
    AuthorizedPrimaryDocument,
    PrimaryMetricObservation,
    PrimarySourceKind,
    authorized_primary_source_collector,
)
from .evidence_collection import EvidenceCollector


FetchText = Callable[[str], str]
_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ALLOWED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "8-K", "8-K/A"}
_SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/{primary_document}"


@dataclass(frozen=True)
class SECCompanyIdentity:
    cik: str
    legal_name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    source_ref: str
    checked_at: str
    submissions_hash: str

    def validate(self) -> None:
        if not _CIK.fullmatch(self.cik):
            raise ValueError("SEC CIK must be exactly 10 digits")
        if not self.legal_name or not self.source_ref or not self.checked_at:
            raise ValueError("SEC company identity is incomplete")
        _parse_aware(self.checked_at, "SEC identity checked_at")
        _require_hash(self.submissions_hash, "SEC submissions_hash")
        if len(self.tickers) != len(self.exchanges):
            raise ValueError("SEC ticker/exchange arrays must have the same length")


@dataclass(frozen=True)
class SECFilingMetadata:
    cik: str
    accession_no: str
    form: str
    filing_date: str
    report_date: str
    acceptance_at: str
    primary_document: str
    is_xbrl: bool
    is_inline_xbrl: bool

    @property
    def archive_url(self) -> str:
        self.validate()
        return _SEC_ARCHIVES.format(
            cik_int=str(int(self.cik)),
            accession_compact=self.accession_no.replace("-", ""),
            primary_document=self.primary_document,
        )

    def validate(self) -> None:
        if not _CIK.fullmatch(self.cik):
            raise ValueError("SEC filing CIK must be exactly 10 digits")
        if not _ACCESSION.fullmatch(self.accession_no):
            raise ValueError("SEC accession number must use ##########-##-######")
        if self.form not in _ALLOWED_FORMS:
            raise ValueError(f"unsupported SEC filing form: {self.form}")
        filing_date = date.fromisoformat(self.filing_date[:10])
        report_date = date.fromisoformat(self.report_date[:10])
        acceptance = _parse_aware(self.acceptance_at, "SEC acceptance_at")
        if report_date > filing_date:
            raise ValueError("SEC report date cannot follow filing date")
        if acceptance.date() < filing_date:
            raise ValueError("SEC acceptance time cannot precede filing date")
        if (
            not self.primary_document
            or self.primary_document.startswith(("/", "."))
            or "/" in self.primary_document
            or "\\" in self.primary_document
            or ".." in self.primary_document
        ):
            raise ValueError("SEC primary document must be one safe basename")


@dataclass(frozen=True)
class SECSubmissionsSnapshot:
    identity: SECCompanyIdentity
    filings: tuple[SECFilingMetadata, ...]

    def filing(self, accession_no: str) -> SECFilingMetadata:
        matches = tuple(item for item in self.filings if item.accession_no == accession_no)
        if len(matches) != 1:
            raise ValueError(f"expected exactly one SEC filing for accession {accession_no}")
        return matches[0]


@dataclass(frozen=True)
class SECCompanyFactsSnapshot:
    cik: str
    entity_name: str
    checked_at: str
    source_ref: str
    snapshot_hash: str
    payload: dict

    def validate(self) -> None:
        if not _CIK.fullmatch(self.cik):
            raise ValueError("SEC companyfacts CIK must be exactly 10 digits")
        if not self.entity_name or not self.source_ref:
            raise ValueError("SEC companyfacts snapshot is incomplete")
        _parse_aware(self.checked_at, "SEC companyfacts checked_at")
        _require_hash(self.snapshot_hash, "SEC companyfacts snapshot_hash")
        if not isinstance(self.payload.get("facts"), dict):
            raise ValueError("SEC companyfacts payload requires facts mapping")


@dataclass(frozen=True)
class SECMetricSpec:
    metric: str
    taxonomy: str
    concept: str
    unit: str
    segment: str
    critical: bool = False

    def validate(self) -> None:
        if not all((self.metric, self.taxonomy, self.concept, self.unit, self.segment)):
            raise ValueError("SEC metric spec is incomplete")
        if any(character.isspace() for character in self.taxonomy + self.concept):
            raise ValueError("SEC taxonomy/concept must use exact whitespace-free identifiers")


@dataclass(frozen=True)
class SECPrimaryDocument:
    filing: SECFilingMetadata
    checked_at: str
    source_ref: str
    document_hash: str
    text: str

    def validate(self) -> None:
        self.filing.validate()
        _parse_aware(self.checked_at, "SEC document checked_at")
        if _parse_aware(self.checked_at, "SEC document checked_at") < _parse_aware(
            self.filing.acceptance_at, "SEC acceptance_at"
        ):
            raise ValueError("SEC document cannot be checked before filing acceptance")
        if self.source_ref != self.filing.archive_url:
            raise ValueError("SEC document source_ref must equal the canonical archive URL")
        _require_hash(self.document_hash, "SEC document_hash")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.document_hash:
            raise ValueError("SEC primary document hash does not reproduce from text")

    def authorized_document(self) -> AuthorizedPrimaryDocument:
        self.validate()
        return AuthorizedPrimaryDocument(
            source_id="SEC_EDGAR_PRIMARY_DOCUMENT",
            target_id=f"SEC:CIK{self.filing.cik}",
            kind=PrimarySourceKind.REGULATORY_FILING,
            document_id=self.filing.accession_no,
            document_hash=self.document_hash,
            source_ref=self.source_ref,
            published_at=self.filing.acceptance_at,
            checked_at=self.checked_at,
            access_basis="public",
        )


def normalize_sec_cik(value: str | int) -> str:
    text = str(value).strip()
    if not text.isdigit() or len(text) > 10:
        raise ValueError("SEC CIK must be numeric and at most 10 digits")
    cik = text.zfill(10)
    if int(cik) <= 0:
        raise ValueError("SEC CIK must be positive")
    return cik


def load_sec_submissions(
    *,
    cik: str | int,
    fetch_text: FetchText,
    checked_at: str,
) -> SECSubmissionsSnapshot:
    normalized = normalize_sec_cik(cik)
    _parse_aware(checked_at, "SEC submissions checked_at")
    url = _SEC_SUBMISSIONS.format(cik=normalized)
    raw = fetch_text(url)
    payload = _json_mapping(raw, "SEC submissions")
    payload_cik = normalize_sec_cik(payload.get("cik", ""))
    if payload_cik != normalized:
        raise ValueError("SEC submissions CIK does not match requested issuer")
    name = str(payload.get("name") or "").strip()
    tickers = tuple(str(item).strip() for item in (payload.get("tickers") or ()))
    exchanges = tuple(str(item).strip() for item in (payload.get("exchanges") or ()))
    identity = SECCompanyIdentity(
        cik=normalized,
        legal_name=name,
        tickers=tickers,
        exchanges=exchanges,
        source_ref=url,
        checked_at=checked_at,
        submissions_hash=sha256(raw.encode("utf-8")).hexdigest(),
    )
    identity.validate()
    recent = (payload.get("filings") or {}).get("recent")
    if not isinstance(recent, dict):
        raise ValueError("SEC submissions payload requires filings.recent")
    filings = _parse_recent_filings(normalized, recent)
    return SECSubmissionsSnapshot(identity, filings)


def load_sec_companyfacts(
    *,
    cik: str | int,
    fetch_text: FetchText,
    checked_at: str,
) -> SECCompanyFactsSnapshot:
    normalized = normalize_sec_cik(cik)
    _parse_aware(checked_at, "SEC companyfacts checked_at")
    url = _SEC_COMPANYFACTS.format(cik=normalized)
    raw = fetch_text(url)
    payload = _json_mapping(raw, "SEC companyfacts")
    payload_cik = normalize_sec_cik(payload.get("cik", ""))
    if payload_cik != normalized:
        raise ValueError("SEC companyfacts CIK does not match requested issuer")
    snapshot = SECCompanyFactsSnapshot(
        cik=normalized,
        entity_name=str(payload.get("entityName") or "").strip(),
        checked_at=checked_at,
        source_ref=url,
        snapshot_hash=sha256(raw.encode("utf-8")).hexdigest(),
        payload=payload,
    )
    snapshot.validate()
    return snapshot


def fetch_sec_primary_document(
    *,
    filing: SECFilingMetadata,
    fetch_text: FetchText,
    checked_at: str,
) -> SECPrimaryDocument:
    filing.validate()
    text = fetch_text(filing.archive_url)
    result = SECPrimaryDocument(
        filing=filing,
        checked_at=checked_at,
        source_ref=filing.archive_url,
        document_hash=sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )
    result.validate()
    return result


def sec_companyfacts_collector(
    *,
    snapshot: SECCompanyFactsSnapshot,
    filing: SECFilingMetadata,
    specs: tuple[SECMetricSpec, ...],
) -> EvidenceCollector:
    snapshot.validate()
    filing.validate()
    if filing.cik != snapshot.cik:
        raise ValueError("SEC filing and companyfacts snapshot CIK must match")
    if not specs:
        raise ValueError("SEC companyfacts collector requires metric specs")
    observations = tuple(
        _extract_company_fact(snapshot=snapshot, filing=filing, spec=spec)
        for spec in specs
    )
    authorized = AuthorizedPrimaryDocument(
        source_id="SEC_EDGAR_COMPANYFACTS",
        target_id=f"SEC:CIK{snapshot.cik}",
        kind=PrimarySourceKind.REGULATORY_FILING,
        document_id=f"companyfacts:{filing.accession_no}",
        document_hash=snapshot.snapshot_hash,
        source_ref=snapshot.source_ref,
        published_at=filing.acceptance_at,
        checked_at=snapshot.checked_at,
        access_basis="public",
    )
    return authorized_primary_source_collector(
        document=authorized,
        observations=observations,
        allowed_metrics=tuple(spec.metric for spec in specs),
        allowed_segments=tuple(dict.fromkeys(spec.segment for spec in specs)),
    )


def sec_primary_document_collector(
    *,
    document: SECPrimaryDocument,
    observations: tuple[PrimaryMetricObservation, ...],
    allowed_metrics: tuple[str, ...],
    allowed_segments: tuple[str, ...],
) -> EvidenceCollector:
    return authorized_primary_source_collector(
        document=document.authorized_document(),
        observations=observations,
        allowed_metrics=allowed_metrics,
        allowed_segments=allowed_segments,
    )


def _extract_company_fact(
    *,
    snapshot: SECCompanyFactsSnapshot,
    filing: SECFilingMetadata,
    spec: SECMetricSpec,
) -> PrimaryMetricObservation:
    spec.validate()
    facts = snapshot.payload["facts"]
    taxonomy = facts.get(spec.taxonomy)
    if not isinstance(taxonomy, dict):
        raise ValueError(f"SEC companyfacts has no exact taxonomy {spec.taxonomy}")
    concept = taxonomy.get(spec.concept)
    if not isinstance(concept, dict):
        raise ValueError(
            f"SEC companyfacts has no exact concept {spec.taxonomy}:{spec.concept}"
        )
    units = concept.get("units")
    if not isinstance(units, dict):
        raise ValueError("SEC companyfacts concept has no units mapping")
    rows = units.get(spec.unit)
    if not isinstance(rows, list):
        raise ValueError(
            f"SEC companyfacts has no exact unit {spec.unit} for {spec.taxonomy}:{spec.concept}"
        )
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("accn") or "") == filing.accession_no
        and str(row.get("form") or "") == filing.form
        and str(row.get("end") or "")[:10] == filing.report_date[:10]
    ]
    if not matches:
        raise ValueError(
            f"SEC companyfacts has no exact filing fact for {spec.taxonomy}:{spec.concept}"
        )
    normalized_values = {_decimal_text(row.get("val")) for row in matches}
    if len(normalized_values) != 1:
        raise ValueError(
            f"SEC companyfacts contains conflicting values for {spec.taxonomy}:{spec.concept}"
        )
    value = next(iter(normalized_values))
    filed_dates = {str(row.get("filed") or "")[:10] for row in matches}
    if filed_dates != {filing.filing_date[:10]}:
        raise ValueError("SEC companyfacts filing date does not match submissions metadata")
    locator = f"{spec.taxonomy}:{spec.concept}/{spec.unit}/{filing.accession_no}"
    return PrimaryMetricObservation(
        metric=spec.metric,
        segment=spec.segment,
        value=value,
        unit=spec.unit,
        effective_date=filing.report_date[:10],
        locator=locator,
        critical=spec.critical,
        notes=(
            f"SEC Company Facts exact concept {spec.taxonomy}:{spec.concept}; "
            f"form={filing.form}; accession={filing.accession_no}"
        ),
    )


def _parse_recent_filings(cik: str, recent: dict) -> tuple[SECFilingMetadata, ...]:
    required = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "form",
        "primaryDocument",
        "isXBRL",
        "isInlineXBRL",
    )
    arrays: dict[str, list] = {}
    for key in required:
        value = recent.get(key)
        if not isinstance(value, list):
            raise ValueError(f"SEC filings.recent requires list {key}")
        arrays[key] = value
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("SEC filings.recent arrays must have equal length")
    result: list[SECFilingMetadata] = []
    for index in range(next(iter(lengths), 0)):
        form = str(arrays["form"][index] or "")
        if form not in _ALLOWED_FORMS:
            continue
        acceptance = _normalize_sec_datetime(str(arrays["acceptanceDateTime"][index] or ""))
        item = SECFilingMetadata(
            cik=cik,
            accession_no=str(arrays["accessionNumber"][index] or ""),
            form=form,
            filing_date=str(arrays["filingDate"][index] or "")[:10],
            report_date=str(arrays["reportDate"][index] or "")[:10],
            acceptance_at=acceptance,
            primary_document=str(arrays["primaryDocument"][index] or ""),
            is_xbrl=bool(int(arrays["isXBRL"][index] or 0)),
            is_inline_xbrl=bool(int(arrays["isInlineXBRL"][index] or 0)),
        )
        item.validate()
        result.append(item)
    accessions = [item.accession_no for item in result]
    if len(accessions) != len(set(accessions)):
        raise ValueError("SEC recent filings contain duplicate accession numbers")
    return tuple(result)


def _normalize_sec_datetime(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("SEC acceptanceDateTime is required")
    if text.endswith("Z"):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            # SEC submissions historically emits acceptance timestamps without a zone;
            # interpret that wire representation as UTC rather than local machine time.
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _parse_aware(value: str, label: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _json_mapping(raw: str, label: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} response root must be a mapping")
    return payload


def _decimal_text(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("SEC companyfacts value must be numeric")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("SEC companyfacts value must be numeric") from exc
    if not result.is_finite():
        raise ValueError("SEC companyfacts value must be finite")
    return str(result)


def _require_hash(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value.casefold()):
        raise ValueError(f"{label} must be an exact SHA-256 hex digest")
