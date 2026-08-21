from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class DocumentIndexRecord:
    source_id: str
    document_id: str
    title: str
    published_at: date | None
    url: str
    document_class: str
    period: str | None = None
    locator: str | None = None
    content_fingerprint: str | None = None

    def validate(self) -> None:
        if not self.source_id or not self.document_id or not self.title or not self.url:
            raise ValueError("source_id, document_id, title and url are required")


@dataclass(frozen=True)
class SourceIndexBatch:
    source_id: str
    checked_at: date
    records: tuple[DocumentIndexRecord, ...]
    schema_hash: str
    transport: str
    fetch_ok: bool = True
    warning: str | None = None


@dataclass(frozen=True)
class IncrementalIndexPlan:
    new_document_ids: tuple[str, ...]
    changed_document_ids: tuple[str, ...]
    unchanged_document_ids: tuple[str, ...]


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def schema_hash_from_records(records: Sequence[Mapping[str, Any]]) -> str:
    keys = sorted({key for row in records for key in row.keys()})
    return stable_hash(keys)


def fact_hash_from_records(records: Sequence[Mapping[str, Any]], *, exclude_keys: Iterable[str] = ()) -> str:
    excluded = set(exclude_keys)
    normalized = [
        {k: row[k] for k in sorted(row) if k not in excluded}
        for row in records
    ]
    return stable_hash(normalized)


def plan_incremental_index(
    previous: Iterable[DocumentIndexRecord], current: Iterable[DocumentIndexRecord]
) -> IncrementalIndexPlan:
    old = {r.document_id: r for r in previous}
    now = {r.document_id: r for r in current}
    new_ids: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []
    for doc_id, record in sorted(now.items()):
        if doc_id not in old:
            new_ids.append(doc_id)
            continue
        prior = old[doc_id]
        if (
            prior.title != record.title
            or prior.published_at != record.published_at
            or prior.url != record.url
            or prior.period != record.period
            or (
                prior.content_fingerprint is not None
                and record.content_fingerprint is not None
                and prior.content_fingerprint != record.content_fingerprint
            )
        ):
            changed.append(doc_id)
        else:
            unchanged.append(doc_id)
    return IncrementalIndexPlan(tuple(new_ids), tuple(changed), tuple(unchanged))


def parse_yyyy_mm_dd(value: str) -> date | None:
    match = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", value)
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def slug_document_id(source_id: str, title: str, published_at: date | None) -> str:
    raw = f"{source_id}|{published_at.isoformat() if published_at else 'undated'}|{title.strip()}"
    return f"{source_id}_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def parse_kiet_release_listing(text: str, *, source_id: str = "KR_KIET_PSI", base_url: str = "https://www.kiet.re.kr/communicate/medataList") -> tuple[DocumentIndexRecord, ...]:
    """Parse KIET search/listing text already fetched by a transport layer.

    The parser intentionally does not perform HTTP. It recognizes the recurring
    `title YYYY.MM.DD` form and keeps only industry-survey releases.
    """
    records: list[DocumentIndexRecord] = []
    pattern = re.compile(r"(?P<title>산업경기\s*전문가\s*서베이조사결과[^\n]*?)\s+(?P<date>20\d{2}\.\d{1,2}\.\d{1,2})")
    for m in pattern.finditer(text):
        title = " ".join(m.group("title").split())
        published = parse_yyyy_mm_dd(m.group("date"))
        record = DocumentIndexRecord(
            source_id=source_id,
            document_id=slug_document_id(source_id, title, published),
            title=title,
            published_at=published,
            url=base_url,
            document_class="survey_outlook",
            locator=title,
            content_fingerprint=stable_hash({"title": title, "published_at": published}),
        )
        record.validate()
        records.append(record)
    return tuple(records)


def parse_kisdi_report_metadata(text: str, *, source_id: str = "KR_KISDI_ICT", url: str) -> DocumentIndexRecord:
    title_match = re.search(r"(?:####\s*)?([^\n]*ICT\s*산업[^\n]*(?:전망|Outlook)[^\n]*)", text, flags=re.I)
    if not title_match:
        raise ValueError("KISDI title not found")
    title = title_match.group(1).strip(" #-\t")
    date_match = re.search(r"(?:발행일|발행일자)\s*[: ]?\s*(20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})", text)
    published = parse_yyyy_mm_dd(date_match.group(1)) if date_match else None
    record = DocumentIndexRecord(
        source_id=source_id,
        document_id=slug_document_id(source_id, title, published),
        title=title,
        published_at=published,
        url=url,
        document_class="medium_term_outlook",
        locator="report metadata",
        content_fingerprint=stable_hash({"title": title, "published_at": published}),
    )
    record.validate()
    return record


@dataclass(frozen=True)
class IEADataProductMetadata:
    title: str
    last_updated: date | None
    next_release: date | None
    latest_file_updated: date | None
    schema_transition_note: str | None


def _parse_month_name_date(value: str, *, default_day: int = 1) -> date | None:
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"(?:(\d{1,2})(?:st|nd|rd|th)?\s+)?(" + "|".join(months) + r")\s+(20\d{2})", value, flags=re.I)
    if not m:
        return None
    day = int(m.group(1)) if m.group(1) else default_day
    return date(int(m.group(3)), months[m.group(2).lower()], day)


def parse_iea_data_product_metadata(text: str) -> IEADataProductMetadata:
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.M)
    title = title_match.group(1).strip() if title_match else "IEA data product"
    last_match = re.search(r"Last updated\s+(?:\n\s*)?([^\n]+)", text, flags=re.I)
    next_match = re.search(r"Next release\s+(?:\n\s*)?([^\n]+)", text, flags=re.I)
    last_updated = _parse_month_name_date(last_match.group(1)) if last_match else None
    next_release = _parse_month_name_date(next_match.group(1)) if next_match else None
    # Prefer an explicit dd/mm/yyyy latest schedule/file line when present.
    dated = [
        date(int(y), int(m), int(d))
        for d, m, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    ]
    latest_file_updated = max(dated) if dated else None
    schema_note = None
    if "SDMX" in text and ("legacy" in text.lower() or "discontinue" in text.lower()):
        schema_note = "SDMX migration/legacy-format transition disclosed"
    return IEADataProductMetadata(title, last_updated, next_release, latest_file_updated, schema_note)


def snapshot_hashes_from_json_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Return (fact_hash, schema_hash) for KOSIS/OpenDART-like JSON rows."""
    return fact_hash_from_records(rows), schema_hash_from_records(rows)


def parse_opendart_report_rows(rows: Sequence[Mapping[str, Any]], *, source_id: str = "KR_OPENDART") -> tuple[DocumentIndexRecord, ...]:
    """Normalize OpenDART `list.json` rows into filing index records.

    This indexes filing metadata only. Financial facts remain a separate company-evidence adapter.
    """
    records: list[DocumentIndexRecord] = []
    for row in rows:
        rcept_no = str(row.get("rcept_no") or "").strip()
        title = str(row.get("report_nm") or "").strip()
        if not rcept_no or not title:
            continue
        dt_raw = str(row.get("rcept_dt") or "")
        published = None
        m = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", dt_raw)
        if m:
            published = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
        record = DocumentIndexRecord(
            source_id=source_id,
            document_id=f"DART_{rcept_no}",
            title=title,
            published_at=published,
            url=url,
            document_class="regulatory_filing",
            period=str(row.get("report_nm") or ""),
            locator=rcept_no,
            content_fingerprint=stable_hash({
                "rcept_no": rcept_no,
                "report_nm": title,
                "rcept_dt": dt_raw,
                "corp_code": row.get("corp_code"),
                "stock_code": row.get("stock_code"),
            }),
        )
        record.validate()
        records.append(record)
    return tuple(records)


def parse_kosis_rows_snapshot(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, tuple[str, ...]]:
    """Normalize KOSIS-style data rows without assuming one statistic table schema.

    Returns fact hash, schema hash and period labels. Table/metric definitions must be
    stored separately and cannot be inferred from numeric rows alone.
    """
    fact_hash, schema_hash = snapshot_hashes_from_json_rows(rows)
    period_keys = ("PRD_DE", "PRD_SE", "TIME", "period")
    periods: list[str] = []
    for row in rows:
        for key in period_keys:
            if row.get(key) not in (None, ""):
                periods.append(str(row[key]))
                break
    return fact_hash, schema_hash, tuple(sorted(set(periods)))
