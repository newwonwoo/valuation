"""Company-neutral KR industry providers: snapshot, freshness, segments, DNA route.

These four providers close the cold-start gap between COMPANY_RESOLUTION and
MODULE_REQUIREMENT_PLAN. None of them contains a company fact: identity comes
from resolution, filings come from OpenDART by corp code, and the mapping from a
company to an economic archetype is a *classification file* keyed by KSIC
industry code — data the router reads, never judgment the router invents.

Fail-closed rules:

- a corp code with no periodic filing inside the lookback window cannot get a
  snapshot (an empty snapshot would silently disable every downstream
  evidence-lineage check);
- a KSIC code the classification map does not cover cannot be routed — guessing
  an archetype is a valuation decision, so the run stops and names the code;
- a snapshot whose newest filing is older than the declared cadence produces an
  EXPECTED_RELEASE_MISSED warning, and an empty lineage is a SOURCE_FAILURE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .industry_dna import EconomicArchetype, IndustryDNAProfile
from .live_indexers import index_opendart_filing_list
from .live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    IndustryKnowledgeSnapshot,
    LiveFreshnessAssessment,
    ResolvedCompanyIdentity,
    SegmentDescriptor,
)
from .source_index import DocumentIndexRecord
from .source_watch import WatchFinding, WatchStatus
from .runtime_resources import runtime_registry_path


DEFAULT_CLASSIFICATION_MAP_PATH = runtime_registry_path(
    "kr_industry_classification_map.yaml"
)
OPENDART_SOURCE_ID = "KR_OPENDART"

#: Report-name fragments that identify a periodic statutory filing. Ad-hoc
#: disclosures are real Evidence too, but the snapshot's job is the company's
#: authoritative recurring record.
_PERIODIC_REPORT_TOKENS = ("사업보고서", "반기보고서", "분기보고서")

FetchText = Callable[[str], str]


class GenericKRIndustryError(ValueError):
    """Raised when a company-neutral industry provider must fail closed."""


def _corp_code(identity: ResolvedCompanyIdentity) -> str:
    for key, value in identity.external_ids:
        if key == "opendart_corp_code":
            return value
    raise GenericKRIndustryError(
        f"{identity.target_id} carries no opendart_corp_code external id"
    )


# ------------------------------------------------------------------- snapshot


def _is_periodic(record: DocumentIndexRecord) -> bool:
    return any(token in record.title for token in _PERIODIC_REPORT_TOKENS)


def _lineage_from_record(
    record: DocumentIndexRecord,
    *,
    identity: ResolvedCompanyIdentity,
    as_of: str,
) -> AuthoritativeEvidenceLineage:
    if record.published_at is None or record.content_fingerprint is None:
        raise GenericKRIndustryError(
            f"filing index record is missing publication date or fingerprint: {record.document_id}"
        )
    published = f"{record.published_at.isoformat()}T09:00:00+09:00"
    day = record.published_at.isoformat()
    return AuthoritativeEvidenceLineage(
        evidence_id=f"E:{identity.target_id}:{record.document_id}",
        target_id=identity.target_id,
        source_id=record.source_id,
        observed_date=as_of,
        content_hash=record.content_fingerprint,
        event_date=day,
        effective_date=day,
        published_at=published,
        first_seen_at=published,
        revision_id=record.document_id,
        revision_at=published,
    )


def opendart_filing_snapshot_loader(
    *,
    fetch_text: FetchText,
    as_of: str,
    api_key: str | None = None,
    lookback_days: int = 540,
    max_filings: int = 4,
):
    """IndustrySnapshotLoader: periodic DART filings become the authoritative snapshot.

    Filings are filtered to receipt dates at or before ``as_of``, so the
    snapshot can never carry a document that was not publicly knowable at the
    requested cutoff — the same knowledge-time rule the probability route
    enforces on its calibration artifacts.
    """
    cutoff = date.fromisoformat(as_of[:10])
    if lookback_days <= 0 or max_filings <= 0:
        raise GenericKRIndustryError("lookback_days and max_filings must be positive")

    def load(identity: ResolvedCompanyIdentity) -> IndustryKnowledgeSnapshot:
        identity.validate()
        corp_code = _corp_code(identity)
        batch = index_opendart_filing_list(
            fetch_text,
            checked_at=cutoff,
            corp_code=corp_code,
            begin_date=(cutoff - timedelta(days=lookback_days)).strftime("%Y%m%d"),
            end_date=cutoff.strftime("%Y%m%d"),
            api_key=api_key,
        )
        periodic = sorted(
            (
                record
                for record in batch.records
                if _is_periodic(record)
                and record.published_at is not None
                and record.published_at <= cutoff
            ),
            key=lambda record: (record.published_at, record.document_id),
            reverse=True,
        )[:max_filings]
        if not periodic:
            raise GenericKRIndustryError(
                f"no periodic DART filing for {identity.target_id} within "
                f"{lookback_days} days of {as_of}; cannot build an authoritative snapshot"
            )
        lineage = tuple(
            _lineage_from_record(record, identity=identity, as_of=as_of)
            for record in periodic
        )
        return IndustryKnowledgeSnapshot.build(
            as_of=as_of,
            source_ids=(OPENDART_SOURCE_ID,),
            document_ids=tuple(record.document_id for record in periodic),
            evidence_ids=tuple(item.evidence_id for item in lineage),
            content_hashes=tuple(item.content_hash for item in lineage),
            evidence_lineage=lineage,
        )

    return load


# ------------------------------------------------------------------ freshness


def filing_cadence_freshness_loader(*, as_of: str, max_age_days: int = 120):
    """FreshnessLoader: the newest periodic filing must be younger than the cadence.

    Purely a policy over the snapshot's own lineage — no hand-written CLEAN rows.
    ``max_age_days`` defaults to a quarterly cadence plus filing grace.
    """
    cutoff = date.fromisoformat(as_of[:10])
    if max_age_days <= 0:
        raise GenericKRIndustryError("max_age_days must be positive")

    def load(
        identity: ResolvedCompanyIdentity,
        snapshot: IndustryKnowledgeSnapshot,
    ) -> LiveFreshnessAssessment:
        if not snapshot.evidence_lineage:
            findings = (
                WatchFinding(
                    WatchStatus.SOURCE_FAILURE,
                    OPENDART_SOURCE_ID,
                    "industry snapshot carries no Evidence lineage to assess",
                    (),
                    True,
                ),
            )
        else:
            newest = max(
                date.fromisoformat(item.published_at[:10])
                for item in snapshot.evidence_lineage
            )
            age = (cutoff - newest).days
            if age > max_age_days:
                findings = (
                    WatchFinding(
                        WatchStatus.EXPECTED_RELEASE_MISSED,
                        OPENDART_SOURCE_ID,
                        f"newest periodic filing is {age} days old "
                        f"(cadence policy {max_age_days} days)",
                        (),
                        False,
                    ),
                )
            else:
                findings = (
                    WatchFinding(
                        WatchStatus.CLEAN,
                        OPENDART_SOURCE_ID,
                        f"newest periodic filing is {age} days old, within the "
                        f"{max_age_days}-day cadence policy",
                        (),
                        False,
                    ),
                )
        assessment = LiveFreshnessAssessment(
            checked_at=as_of,
            findings=findings,
            source_snapshot_hash=snapshot.snapshot_hash,
        )
        assessment.validate()
        return assessment

    return load


# -------------------------------------------------------------- classification


_STRUCTURE_FIELDS = (
    "revenue_recognition",
    "price_formation",
    "asset_ownership",
    "capital_intensity",
    "regulation_intensity",
    "customer_structure",
    "reinvestment_model",
    "cashflow_duration",
)


@dataclass(frozen=True)
class IndustryClassificationEntry:
    ksic_prefix: str
    label: str
    sector_adapter: str
    archetypes: tuple[EconomicArchetype, ...]
    structure: Mapping[str, str]

    def validate(self) -> None:
        if not self.ksic_prefix or not self.ksic_prefix.isdigit():
            raise GenericKRIndustryError(
                f"classification entry requires a numeric KSIC prefix: {self.ksic_prefix!r}"
            )
        if not self.label or not self.sector_adapter:
            raise GenericKRIndustryError(
                f"classification entry {self.ksic_prefix} requires label and sector_adapter"
            )
        if not self.archetypes:
            raise GenericKRIndustryError(
                f"classification entry {self.ksic_prefix} requires archetypes"
            )
        missing = tuple(
            name for name in _STRUCTURE_FIELDS if not str(self.structure.get(name, "")).strip()
        )
        if missing:
            raise GenericKRIndustryError(
                f"classification entry {self.ksic_prefix} is missing structure fields: "
                + ", ".join(missing)
            )


@dataclass(frozen=True)
class KRIndustryClassification:
    entries: tuple[IndustryClassificationEntry, ...]

    def validate(self) -> None:
        if not self.entries:
            raise GenericKRIndustryError("industry classification map is empty")
        prefixes = tuple(item.ksic_prefix for item in self.entries)
        if len(prefixes) != len(set(prefixes)):
            raise GenericKRIndustryError(
                "industry classification map has duplicate KSIC prefixes"
            )
        for item in self.entries:
            item.validate()

    def lookup(self, induty_code: str) -> IndustryClassificationEntry:
        """Longest-prefix match; unmapped codes fail closed by design."""
        code = str(induty_code or "").strip()
        if not code:
            raise GenericKRIndustryError("company profile carries no KSIC industry code")
        best: IndustryClassificationEntry | None = None
        for entry in self.entries:
            if code.startswith(entry.ksic_prefix):
                if best is None or len(entry.ksic_prefix) > len(best.ksic_prefix):
                    best = entry
        if best is None:
            raise GenericKRIndustryError(
                f"KSIC industry code {code} is not covered by the classification map; "
                "routing an unmapped company would be a guessed archetype"
            )
        return best


def load_kr_industry_classification(
    path: str | Path = DEFAULT_CLASSIFICATION_MAP_PATH,
) -> KRIndustryClassification:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise GenericKRIndustryError("industry classification map must be a mapping")
    rows = payload.get("classifications")
    if not isinstance(rows, list) or not rows:
        raise GenericKRIndustryError(
            "industry classification map requires a classifications list"
        )
    entries = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise GenericKRIndustryError("classification row must be a mapping")
        entries.append(
            IndustryClassificationEntry(
                ksic_prefix=str(row.get("ksic_prefix", "")),
                label=str(row.get("label", "")),
                sector_adapter=str(row.get("sector_adapter", "")),
                archetypes=tuple(
                    EconomicArchetype(str(item))
                    for item in (row.get("archetypes") or ())
                ),
                structure=dict(row.get("structure") or {}),
            )
        )
    classification = KRIndustryClassification(tuple(entries))
    classification.validate()
    return classification


# ------------------------------------------------------------ company profile


@dataclass(frozen=True)
class OpenDartCompanyProfile:
    corp_code: str
    corp_name: str
    induty_code: str
    stock_code: str

    def validate(self) -> None:
        if not self.corp_code or not self.induty_code:
            raise GenericKRIndustryError(
                "OpenDART company profile requires corp_code and induty_code"
            )


def build_opendart_company_url(*, corp_code: str, api_key: str | None = None) -> str:
    from urllib.parse import urlencode

    from .live_indexers import require_env_credential

    key = api_key or require_env_credential("DART_API_KEY")
    return "https://opendart.fss.or.kr/api/company.json?" + urlencode(
        {"crtfc_key": key, "corp_code": corp_code}
    )


def fetch_opendart_company_profile(
    fetch_text: FetchText,
    *,
    corp_code: str,
    api_key: str | None = None,
) -> OpenDartCompanyProfile:
    raw = fetch_text(build_opendart_company_url(corp_code=corp_code, api_key=api_key))
    payload: Any = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise GenericKRIndustryError("OpenDART company payload must be a mapping")
    status = str(payload.get("status") or "")
    if status not in {"000", ""}:
        raise GenericKRIndustryError(
            f"OpenDART company lookup failed for {corp_code}: status {status}"
        )
    profile = OpenDartCompanyProfile(
        corp_code=str(payload.get("corp_code") or corp_code),
        corp_name=str(payload.get("corp_name") or ""),
        induty_code=str(payload.get("induty_code") or ""),
        stock_code=str(payload.get("stock_code") or ""),
    )
    profile.validate()
    return profile


@dataclass
class CachedCompanyProfileFetcher:
    """One profile fetch per corp code per run, shared by decomposer and router."""

    fetch_text: FetchText
    api_key: str | None = None
    _cache: dict[str, OpenDartCompanyProfile] = field(default_factory=dict)

    def __call__(self, identity: ResolvedCompanyIdentity) -> OpenDartCompanyProfile:
        corp_code = _corp_code(identity)
        if corp_code not in self._cache:
            self._cache[corp_code] = fetch_opendart_company_profile(
                self.fetch_text,
                corp_code=corp_code,
                api_key=self.api_key,
            )
        return self._cache[corp_code]


# ----------------------------------------------------- segments and DNA route

CORE_SEGMENT_ID = "core"


def classified_segment_decomposer(
    *,
    profile_fetcher: Callable[[ResolvedCompanyIdentity], OpenDartCompanyProfile],
    classification: KRIndustryClassification,
):
    """SegmentDecomposer: one whole-company segment, structured by classification.

    Finer segmentation without disclosure-backed segment-note extraction would be
    invented structure, so the generic decomposer deliberately emits a single
    ``core`` segment whose economic-structure fields come from the KSIC
    classification entry and whose evidence is the snapshot's filing lineage.
    """

    def decompose(
        identity: ResolvedCompanyIdentity,
        snapshot: IndustryKnowledgeSnapshot,
    ) -> tuple[SegmentDescriptor, ...]:
        profile = profile_fetcher(identity)
        entry = classification.lookup(profile.induty_code)
        evidence_ids = tuple(
            item.evidence_id
            for item in snapshot.evidence_lineage
            if item.target_id == identity.target_id
        )
        if not evidence_ids:
            raise GenericKRIndustryError(
                f"industry snapshot carries no Evidence lineage for {identity.target_id}"
            )
        descriptor = SegmentDescriptor(
            segment_id=CORE_SEGMENT_ID,
            name=f"{identity.legal_name} — {entry.label}",
            revenue_recognition=str(entry.structure["revenue_recognition"]),
            price_formation=str(entry.structure["price_formation"]),
            asset_ownership=str(entry.structure["asset_ownership"]),
            capital_intensity=str(entry.structure["capital_intensity"]),
            regulation_intensity=str(entry.structure["regulation_intensity"]),
            customer_structure=str(entry.structure["customer_structure"]),
            reinvestment_model=str(entry.structure["reinvestment_model"]),
            cashflow_duration=str(entry.structure["cashflow_duration"]),
            evidence_ids=evidence_ids,
        )
        descriptor.validate()
        return (descriptor,)

    return decompose


def classified_industry_dna_router(
    *,
    profile_fetcher: Callable[[ResolvedCompanyIdentity], OpenDartCompanyProfile],
    classification: KRIndustryClassification,
):
    """IndustryDNARouter: archetypes come from the classification map, never a guess."""

    def route(
        identity: ResolvedCompanyIdentity,
        segments: tuple[SegmentDescriptor, ...],
        snapshot: IndustryKnowledgeSnapshot,
    ) -> tuple[IndustryDNAProfile, ...]:
        profile = profile_fetcher(identity)
        entry = classification.lookup(profile.induty_code)
        profiles = tuple(
            IndustryDNAProfile(
                segment_id=segment.segment_id,
                sector_adapter=entry.sector_adapter,
                archetypes=entry.archetypes,
                revenue_recognition=segment.revenue_recognition,
                price_formation=segment.price_formation,
                asset_ownership=segment.asset_ownership,
                capital_intensity=segment.capital_intensity,
                regulation_intensity=segment.regulation_intensity,
                customer_structure=segment.customer_structure,
                reinvestment_model=segment.reinvestment_model,
                cashflow_duration=segment.cashflow_duration,
                evidence_keys=segment.evidence_ids,
            )
            for segment in segments
        )
        for item in profiles:
            item.validate()
        return profiles

    return route
