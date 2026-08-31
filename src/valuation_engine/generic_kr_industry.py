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
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import yaml

from .dart_documents import fetch_indexed_opendart_original_document
from .dart_kpi import _visible_text
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
FetchBytes = Callable[[str], bytes]

_SEGMENT_SCOPE_PREFIX = "SEGMENT_SCOPE:SINGLE"
_KOREAN_NAMED_SEGMENT = re.compile(
    r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z0-9·&/-]{2,24}?)\s*부문"
)
_ENGLISH_NAMED_SEGMENT = re.compile(
    r"\b([A-Za-z][A-Za-z0-9&-]{1,24})\s+(?:segment|division)\b",
    flags=re.IGNORECASE,
)
_KOREAN_SEGMENT_CLAUSE = re.compile(
    r"(?:보고|영업)\s*부문(?:은|을|으로|에는|:)\s*(.{0,240})"
)
_ENGLISH_SEGMENT_CLAUSE = re.compile(
    r"(?:reportable|operating)\s+segments?\s+(?:are|include|:)\s*(.{0,240})",
    flags=re.IGNORECASE,
)
_SINGLE_SEGMENT_DECLARATION = re.compile(
    r"(?:연결회사|연결실체|연결그룹)"
    r"(?:(?![.!?。]).){0,160}?"
    r"(?:전체를\s*)?단일\s*(?:보고|영업)\s*부문"
)
_GENERIC_SEGMENT_NAMES = frozenset(
    {
        "영업부문",
        "보고부문",
        "사업부문",
        "주요부문",
        "각부문",
        "operating",
        "reportable",
        "business",
    }
)
_SEGMENT_TABLE_HEADERS = frozenset({"사업부문", "영업부문", "보고부문"})
_SEGMENT_TABLE_TOTALS = frozenset({"합계", "총계", "계", "연결조정", "내부거래"})


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


def _segment_scope_evidence_id(
    identity: ResolvedCompanyIdentity, segment_id: str | None = None
) -> str:
    """Scope receipt id: the whole-company receipt when ``segment_id`` is None
    (byte-identical to the single-segment era, SINGLE token included), else
    one SEGMENT_SCOPE:<segment_id> receipt per declared reportable segment —
    a multi-segment receipt must not claim SINGLE."""
    if segment_id is None:
        return f"E:{identity.target_id}:{_SEGMENT_SCOPE_PREFIX}"
    return f"E:{identity.target_id}:SEGMENT_SCOPE:{segment_id}"


class _SegmentTableParser(HTMLParser):
    """Collect cell text from filing tables without interpreting their values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._table: list[list[tuple[str, int, int]]] | None = None
        self._table_depth = 0
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._cell_rowspan = 1
        self._cell_colspan = 1

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.casefold()
        if lowered == "table":
            if self._table is None:
                self._table = []
            self._table_depth += 1
        elif lowered == "tr" and self._table is not None and self._table_depth == 1:
            self._row = []
        elif (
            lowered in {"td", "th"}
            and self._row is not None
            and self._table_depth == 1
        ):
            self._cell = []
            attributes = dict(attrs)
            try:
                self._cell_rowspan = max(1, int(attributes.get("rowspan", "1")))
                self._cell_colspan = max(1, int(attributes.get("colspan", "1")))
            except ValueError:
                self._cell_rowspan = 1
                self._cell_colspan = 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None and data:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"td", "th"} and self._cell is not None:
            assert self._row is not None
            self._row.append(
                (
                    " ".join(" ".join(self._cell).split()),
                    self._cell_rowspan,
                    self._cell_colspan,
                )
            )
            self._cell = None
        elif lowered == "tr" and self._row is not None and self._table_depth == 1:
            assert self._table is not None
            self._table.append(self._row)
            self._row = None
        elif lowered == "table" and self._table is not None:
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(self._table)
                self._table = None


def _expand_table(
    table: list[list[tuple[str, int, int]]],
) -> list[list[str]]:
    grid: list[list[str | None]] = []
    for row_index, row in enumerate(table):
        while len(grid) <= row_index:
            grid.append([])
        column_index = 0
        for text, rowspan, colspan in row:
            while (
                column_index < len(grid[row_index])
                and grid[row_index][column_index] is not None
            ):
                column_index += 1
            for target_row in range(row_index, row_index + rowspan):
                while len(grid) <= target_row:
                    grid.append([])
                required_width = column_index + colspan
                if len(grid[target_row]) < required_width:
                    grid[target_row].extend(
                        [None] * (required_width - len(grid[target_row]))
                    )
                for target_column in range(column_index, required_width):
                    grid[target_row][target_column] = text
            column_index += colspan
    return [[cell or "" for cell in row] for row in grid]


def _table_segment_names(raw_text: str) -> set[str]:
    parser = _SegmentTableParser()
    parser.feed(raw_text)
    names: set[str] = set()
    canonical_names: dict[str, str] = {}
    for raw_table in parser.tables:
        table = _expand_table(raw_table)
        header_index = None
        segment_column = None
        for row_index, row in enumerate(table):
            for column_index, cell in enumerate(row):
                if cell.replace(" ", "") in _SEGMENT_TABLE_HEADERS:
                    header_index = row_index
                    segment_column = column_index
                    break
            if header_index is not None:
                break
        if header_index is None or segment_column is None:
            continue
        for row in table[header_index + 1 :]:
            if segment_column >= len(row):
                continue
            candidate = " ".join(row[segment_column].split()).strip()
            if candidate and candidate.replace(" ", "") not in _SEGMENT_TABLE_TOTALS:
                canonical = re.sub(r"[\s/·&-]+", "", candidate).casefold()
                if canonical:
                    canonical_names.setdefault(canonical, candidate)
    names.update(canonical_names.values())
    return names


def _disclosed_segment_names(document) -> tuple[str, ...]:
    names: set[str] = set()
    member_texts = tuple(_visible_text(member) for member in document.text_members)
    # Only an explicit *consolidated-entity* accounting declaration may
    # outrank product/process tables whose "사업부문" column merely splits one
    # integrated segment. Parent-only, subsidiary-only and generic industry
    # prose cannot certify the consolidated reporting scope as single segment.
    if any(_SINGLE_SEGMENT_DECLARATION.search(text) for text in member_texts):
        return ()
    for member, text in zip(document.text_members, member_texts, strict=True):
        names.update(_table_segment_names(member.text or ""))
        clauses = _KOREAN_SEGMENT_CLAUSE.findall(text)
        clauses.extend(_ENGLISH_SEGMENT_CLAUSE.findall(text))
        # A common reverse form names the segments first and closes with
        # "2개 보고부문". Screen the immediately preceding clause as well.
        for match in re.finditer(r"(?:2|두)\s*개\s*(?:보고|영업)\s*부문", text):
            clauses.append(text[max(0, match.start() - 240) : match.start()])
        for clause in clauses:
            for stem in _KOREAN_NAMED_SEGMENT.findall(clause):
                name = f"{stem}부문"
                if name not in _GENERIC_SEGMENT_NAMES and not name.endswith(
                    ("영업부문", "보고부문")
                ):
                    names.add(name)
            for name in _ENGLISH_NAMED_SEGMENT.findall(clause):
                normalized = name.casefold()
                if normalized not in _GENERIC_SEGMENT_NAMES:
                    names.add(normalized)
    return tuple(sorted(names))


def opendart_filing_snapshot_loader(
    *,
    fetch_text: FetchText,
    fetch_bytes: FetchBytes,
    as_of: str,
    api_key: str | None = None,
    lookback_days: int = 540,
    max_filings: int = 4,
    declared_segments=None,
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
        newest = periodic[0]
        filing = fetch_indexed_opendart_original_document(
            fetch_bytes,
            newest,
            checked_at=cutoff,
            api_key=api_key,
        )
        disclosed_segments = _disclosed_segment_names(filing)
        newest_lineage = lineage[0]

        def _scope_receipt(segment_id: str | None, revision: str):
            return AuthoritativeEvidenceLineage(
                evidence_id=_segment_scope_evidence_id(identity, segment_id),
                target_id=identity.target_id,
                source_id=newest.source_id,
                observed_date=as_of,
                content_hash=filing.archive_hash,
                event_date=newest_lineage.event_date,
                effective_date=newest_lineage.effective_date,
                published_at=newest_lineage.published_at,
                first_seen_at=newest_lineage.first_seen_at,
                revision_id=f"{newest.document_id}:{revision}",
                revision_at=newest_lineage.revision_at,
            )

        if len(disclosed_segments) > 1:
            if declared_segments is None:
                raise GenericKRIndustryError(
                    f"latest periodic filing for {identity.target_id} discloses "
                    f"multiple operating segments ({', '.join(disclosed_segments)}); "
                    "declare the reportable segments (declarations/segments.yaml) "
                    "so each carries its own classification and evidence scope"
                )
            declared_segments.validate()
            declared_segments.assert_target(identity.target_id)
            # The screen above is an over-inclusive alarm, never a segment
            # list. The reportable set comes from the filing's own IFRS 8
            # note, read member by member with its reconciliation intact.
            from .segment_note import SegmentNoteError, parse_operating_segment_note

            disclosure = None
            for member in filing.text_members:
                try:
                    disclosure = parse_operating_segment_note(member.text or "")
                    break
                except SegmentNoteError:
                    continue
            if disclosure is None:
                raise GenericKRIndustryError(
                    f"filing {newest.document_id} for {identity.target_id} "
                    "screens as multi-segment but its archive carries no "
                    "readable 영업부문 정보 note; add that section to the "
                    "filing archive before declaring segments"
                )
            matched = declared_segments.match_note(disclosure)
            scope_receipts = tuple(
                _scope_receipt(declared.segment_id, f"segment-scope-v2:{declared.segment_id}")
                for declared, _entry in matched
            )
            complete_lineage = (*lineage, *scope_receipts)
            return IndustryKnowledgeSnapshot.build(
                as_of=as_of,
                source_ids=(OPENDART_SOURCE_ID,),
                document_ids=tuple(record.document_id for record in periodic),
                evidence_ids=tuple(item.evidence_id for item in complete_lineage),
                content_hashes=tuple(item.content_hash for item in complete_lineage),
                evidence_lineage=complete_lineage,
            )
        if declared_segments is not None:
            raise GenericKRIndustryError(
                f"a segment declaration is present but the latest periodic "
                f"filing for {identity.target_id} does not disclose multiple "
                "operating segments; remove the declaration rather than "
                "splitting a company its own filing reports whole"
            )
        complete_lineage = (*lineage, _scope_receipt(None, "segment-scope-v1"))
        return IndustryKnowledgeSnapshot.build(
            as_of=as_of,
            source_ids=(OPENDART_SOURCE_ID,),
            document_ids=tuple(record.document_id for record in periodic),
            evidence_ids=tuple(item.evidence_id for item in complete_lineage),
            content_hashes=tuple(item.content_hash for item in complete_lineage),
            evidence_lineage=complete_lineage,
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
    declared_segments=None,
):
    """SegmentDecomposer: descriptors follow the snapshot's scope receipts.

    The snapshot loader screens the latest original filing first. A company
    whose filing reports itself whole carries one whole-company scope receipt
    and becomes the single ``core`` descriptor, exactly as before. A company
    whose filing discloses multiple reportable segments carries one receipt
    per declared segment — issued only after the declaration matched the
    IFRS 8 note bijectively — and each becomes its own descriptor, structured
    by the classification the operator declared for it (the company-level
    KSIC types the issuer, not its 운송부문). No receipt, no descriptor:
    flattening an unscreened company stays impossible.
    """

    def _descriptor(
        segment_id: str, name: str, entry, evidence_ids: tuple[str, ...]
    ) -> SegmentDescriptor:
        descriptor = SegmentDescriptor(
            segment_id=segment_id,
            name=name,
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
        return descriptor

    def decompose(
        identity: ResolvedCompanyIdentity,
        snapshot: IndustryKnowledgeSnapshot,
    ) -> tuple[SegmentDescriptor, ...]:
        active = tuple(
            item
            for item in snapshot.evidence_lineage
            if item.target_id == identity.target_id and item.active
        )
        if declared_segments is not None:
            declared_segments.validate()
            declared_segments.assert_target(identity.target_id)
            scope_ids = {
                _segment_scope_evidence_id(identity, item.segment_id): item
                for item in declared_segments.segments
            }
            shared_ids = tuple(
                item.evidence_id
                for item in active
                if item.evidence_id not in scope_ids
                and not item.evidence_id.startswith(
                    f"E:{identity.target_id}:SEGMENT_SCOPE"
                )
            )
            descriptors = []
            for declared in declared_segments.segments:
                receipt_id = _segment_scope_evidence_id(identity, declared.segment_id)
                receipts = tuple(
                    item for item in active if item.evidence_id == receipt_id
                )
                if len(receipts) != 1:
                    raise GenericKRIndustryError(
                        f"industry snapshot carries no unique scope receipt for "
                        f"declared segment {declared.segment_id}; the declaration "
                        "did not survive the filing screen"
                    )
                entry = classification.lookup(declared.ksic_code)
                descriptors.append(
                    _descriptor(
                        declared.segment_id,
                        f"{declared.disclosed_name} — {entry.label}",
                        entry,
                        (receipt_id, *shared_ids),
                    )
                )
            return tuple(descriptors)

        profile = profile_fetcher(identity)
        entry = classification.lookup(profile.induty_code)
        scope_id = _segment_scope_evidence_id(identity)
        scope = tuple(item for item in active if item.evidence_id == scope_id)
        if len(scope) != 1:
            raise GenericKRIndustryError(
                f"industry snapshot carries no unique single-segment scope receipt "
                f"for {identity.target_id}; refusing to flatten an unscreened company"
            )
        evidence_ids = tuple(
            item.evidence_id
            for item in snapshot.evidence_lineage
            if item.target_id == identity.target_id
            and item.evidence_id != scope_id
        )
        if not evidence_ids:
            raise GenericKRIndustryError(
                f"industry snapshot carries no Evidence lineage for {identity.target_id}"
            )
        return (
            _descriptor(
                CORE_SEGMENT_ID,
                f"{identity.legal_name} — {entry.label}",
                entry,
                evidence_ids,
            ),
        )

    return decompose


def classified_industry_dna_router(
    *,
    profile_fetcher: Callable[[ResolvedCompanyIdentity], OpenDartCompanyProfile],
    classification: KRIndustryClassification,
    declared_segments=None,
):
    """IndustryDNARouter: archetypes come from the classification map, never a guess.

    With a segment declaration, each segment routes through its own declared
    KSIC code — the company-level code types the issuer, and copying it onto
    every reportable segment would hand a logistics segment a steel archetype.
    """

    def route(
        identity: ResolvedCompanyIdentity,
        segments: tuple[SegmentDescriptor, ...],
        snapshot: IndustryKnowledgeSnapshot,
    ) -> tuple[IndustryDNAProfile, ...]:
        if declared_segments is not None:
            declared_segments.assert_target(identity.target_id)
            declared_codes = {
                item.segment_id: item.ksic_code
                for item in declared_segments.segments
            }

            def _entry_for(segment_id: str):
                code = declared_codes.get(segment_id)
                if code is None:
                    raise GenericKRIndustryError(
                        f"segment {segment_id} has no declared ksic_code; the "
                        "DNA route cannot type an undeclared segment"
                    )
                return classification.lookup(code)

        else:
            profile = profile_fetcher(identity)
            company_entry = classification.lookup(profile.induty_code)

            def _entry_for(segment_id: str):
                return company_entry

        def _profile_for(segment: SegmentDescriptor) -> IndustryDNAProfile:
            entry = _entry_for(segment.segment_id)
            return IndustryDNAProfile(
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

        profiles = tuple(_profile_for(segment) for segment in segments)
        for item in profiles:
            item.validate()
        return profiles

    return route
