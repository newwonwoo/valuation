"""The operator's segment map: disclosed names in, economic identities declared.

The engine's doctrine splits every fact three ways: evidence decides what
exists, declarations carry judgment, deterministic code re-derives. Reportable
segments now follow the same split. *Which* segments exist is evidence — the
IFRS 8 operating-segment note names them and the snapshot loader receipts each
one against the filing's archive hash. *What each segment economically is*
cannot come from evidence: the company-level KSIC code types the whole issuer
(대한제강 is "steel"), and nothing filed says that its 운송부문 should be
valued like a logistics business rather than a rebar mill. That is a judgment,
so it arrives here — one declared classification per disclosed segment, with a
rationale, bound to the target and to the disclosing filing.

The declaration cannot invent or drop segments: ``match_note`` demands an
exact bijection with the note's own names, so a segment the filing discloses
but the operator ignores is a refusal, and so is a declared segment the filing
never mentions. Routing still fails closed downstream — a declared KSIC code
the classification map does not cover stops the run exactly as an unmapped
company does.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
import re

import yaml

from .segment_note import OperatingSegmentDisclosure, SegmentNoteEntry

_MIN_RATIONALE_CHARS = 20
_SEGMENT_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DART_DOCUMENT_ID = re.compile(r"^DART_\d{14}$")
_REPORTING_UNITS = frozenset({"원", "천원", "백만원", "억원"})
_MAX_BOUND_REGION_CHARS = 16_384
_TABLE_ROW = re.compile(r"<TR\b[^>]*>(?P<body>.*?)</TR\s*>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL = re.compile(
    r"<(?P<tag>TH|TD)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ROWSPAN = re.compile(r"\browspan\s*=\s*['\"]?(\d+)", re.IGNORECASE)
_COLSPAN = re.compile(r"\bcolspan\s*=\s*['\"]?(\d+)", re.IGNORECASE)


class DeclaredSegmentsError(ValueError):
    """Raised when the segment declaration cannot be honoured as written."""


def _decimal(value: object, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DeclaredSegmentsError(f"{label} must be a decimal amount") from exc


def _offset(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DeclaredSegmentsError(f"{label} must be a non-negative integer")
    return value


def _source_amount_tokens(value: Decimal) -> tuple[str, ...]:
    integral = value == value.to_integral_value()
    plain = format(abs(value), "f") if not integral else str(abs(int(value)))
    grouped = f"{abs(int(value)):,}" if integral else plain
    if value < 0:
        return (f"({plain})", f"({grouped})", f"-{plain}", f"-{grouped}")
    return (plain, grouped)


@dataclass(frozen=True)
class _SourceCellPosition:
    row: int
    column: int
    visible_text: str
    is_header: bool


def _visible_cell_text(body: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", body)
    return " ".join(unescape(without_tags).replace("\u3000", " ").split())


def _span(attrs: str, pattern: re.Pattern[str]) -> int:
    match = pattern.search(attrs)
    value = int(match.group(1)) if match else 1
    if value < 1:
        raise DeclaredSegmentsError("source table spans must be positive")
    return value


def _source_cell_positions(
    source: str, offsets: tuple[int, ...]
) -> dict[int, _SourceCellPosition]:
    """Locate declared tokens in one table grid without extracting semantics."""

    lowered = source.lower()
    table_starts = {lowered.rfind("<table", 0, offset + 1) for offset in offsets}
    if -1 in table_starts or len(table_starts) != 1:
        raise DeclaredSegmentsError(
            "source-bound segment tokens must belong to one source table"
        )
    table_start = table_starts.pop()
    table_end = lowered.find("</table>", table_start)
    if table_end < 0 or any(offset >= table_end for offset in offsets):
        raise DeclaredSegmentsError(
            "source-bound segment tokens must belong to one closed source table"
        )

    table = source[table_start:table_end]
    pending = set(offsets)
    positions: dict[int, _SourceCellPosition] = {}
    occupied: set[tuple[int, int]] = set()
    for row_index, row_match in enumerate(_TABLE_ROW.finditer(table)):
        column = 0
        row_body_start = table_start + row_match.start("body")
        for cell_match in _TABLE_CELL.finditer(row_match.group("body")):
            while (row_index, column) in occupied:
                column += 1
            colspan = _span(cell_match.group("attrs"), _COLSPAN)
            rowspan = _span(cell_match.group("attrs"), _ROWSPAN)
            body_start = row_body_start + cell_match.start("body")
            body_end = row_body_start + cell_match.end("body")
            contained = tuple(
                offset for offset in pending if body_start <= offset < body_end
            )
            if contained and colspan != 1:
                raise DeclaredSegmentsError(
                    "source-bound segment token belongs to an ambiguous colspan cell"
                )
            for offset in contained:
                positions[offset] = _SourceCellPosition(
                    row_index,
                    column,
                    _visible_cell_text(cell_match.group("body")),
                    cell_match.group("tag").upper() == "TH",
                )
                pending.remove(offset)
            for future_row in range(row_index + 1, row_index + rowspan):
                for covered_column in range(column, column + colspan):
                    occupied.add((future_row, covered_column))
            column += colspan
    if pending:
        raise DeclaredSegmentsError(
            "source-bound segment token is not contained in a source table cell"
        )
    return positions


@dataclass(frozen=True)
class SourceBoundSegmentEntry:
    """One LLM-read IFRS 8 row, still bound to the immutable source member."""

    disclosed_name: str
    revenue: Decimal
    operating_income: Decimal
    name_offset: int
    revenue_offset: int
    operating_income_offset: int


@dataclass(frozen=True)
class SourceBoundSegmentExtraction:
    """Structured LLM reading of a filing table, not a new source of truth.

    The model handles the irregular document layout. Deterministic code only
    checks target document/member identity, the immutable member hash, source
    exact character offsets for every name/amount and the filed subtotal
    residual within one bounded source region.
    """

    extractor: str
    document_id: str
    member_path: str
    member_sha256: str
    reporting_unit: str
    entries: tuple[SourceBoundSegmentEntry, ...]
    filed_total_revenue: Decimal
    filed_total_operating_income: Decimal
    filed_total_revenue_offset: int
    filed_total_operating_income_offset: int
    revenue_row_label: str
    revenue_row_label_offset: int
    operating_income_row_label: str
    operating_income_row_label_offset: int

    def validate(self) -> None:
        if self.extractor != "llm_reviewed":
            raise DeclaredSegmentsError(
                "segment extraction must declare extractor: llm_reviewed"
            )
        if not _DART_DOCUMENT_ID.fullmatch(self.document_id):
            raise DeclaredSegmentsError(
                "segment extraction document_id must be DART_<14-digit receipt>"
            )
        if not self.member_path or Path(self.member_path).name != self.member_path:
            raise DeclaredSegmentsError(
                "segment extraction member_path must name one filing member"
            )
        if not _SHA256.fullmatch(self.member_sha256):
            raise DeclaredSegmentsError(
                "segment extraction member_sha256 must be a lowercase SHA-256"
            )
        if self.reporting_unit not in _REPORTING_UNITS:
            raise DeclaredSegmentsError(
                f"unsupported segment reporting_unit {self.reporting_unit!r}"
            )
        if len(self.entries) < 2:
            raise DeclaredSegmentsError(
                "source-bound segment extraction requires at least two entries"
            )
        names = tuple(_normalize_name(item.disclosed_name) for item in self.entries)
        if any(not name for name in names) or len(set(names)) != len(names):
            raise DeclaredSegmentsError(
                "source-bound segment extraction names must be non-empty and unique"
            )
        offsets = tuple(
            value
            for item in self.entries
            for value in (
                item.name_offset,
                item.revenue_offset,
                item.operating_income_offset,
            )
        ) + (
            self.filed_total_revenue_offset,
            self.filed_total_operating_income_offset,
            self.revenue_row_label_offset,
            self.operating_income_row_label_offset,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in offsets
        ):
            raise DeclaredSegmentsError(
                "source-bound segment extraction offsets must be non-negative integers"
            )
        if len(offsets) != len(set(offsets)):
            raise DeclaredSegmentsError(
                "source-bound segment extraction offsets must identify distinct source tokens"
            )
        revenue_sum = sum((item.revenue for item in self.entries), Decimal(0))
        income_sum = sum(
            (item.operating_income for item in self.entries), Decimal(0)
        )
        # The filing explicitly declares its display precision. Independently
        # rounded components may differ from the independently rounded subtotal
        # by one displayed unit; larger residuals remain fail-closed.
        if abs(revenue_sum - self.filed_total_revenue) > Decimal(1):
            raise DeclaredSegmentsError(
                "source-bound segment revenue does not reconcile to filed total"
            )
        if abs(income_sum - self.filed_total_operating_income) > Decimal(1):
            raise DeclaredSegmentsError(
                "source-bound segment operating income does not reconcile to filed total"
            )

    def bind_source_member(
        self, *, document_id: str, member_path: str, member_sha256: str, text: str
    ) -> OperatingSegmentDisclosure:
        self.validate()
        if document_id != self.document_id:
            raise DeclaredSegmentsError(
                f"segment extraction is bound to {self.document_id}, not {document_id}"
            )
        if member_path != self.member_path or member_sha256 != self.member_sha256:
            raise DeclaredSegmentsError(
                "segment extraction filing member path/hash does not match source"
            )
        source = text or ""
        squeezed_source = re.sub(r"\s+", "", source)
        if self.reporting_unit not in squeezed_source:
            raise DeclaredSegmentsError(
                "segment extraction reporting unit is absent from source member"
            )
        for label, offset in (
            (self.revenue_row_label, self.revenue_row_label_offset),
            (
                self.operating_income_row_label,
                self.operating_income_row_label_offset,
            ),
        ):
            if not label or not source.startswith(label, offset):
                raise DeclaredSegmentsError(
                    "segment extraction metric-row label is not present at its "
                    "declared source offset"
                )
        for entry in self.entries:
            if not source.startswith(entry.disclosed_name, entry.name_offset):
                raise DeclaredSegmentsError(
                    f"segment extraction name {entry.disclosed_name!r} is not present "
                    "at its declared source offset"
                )
            for label, value, offset in (
                ("revenue", entry.revenue, entry.revenue_offset),
                (
                    "operating income",
                    entry.operating_income,
                    entry.operating_income_offset,
                ),
            ):
                if not any(
                    source.startswith(token, offset)
                    for token in _source_amount_tokens(value)
                ):
                    raise DeclaredSegmentsError(
                        f"segment extraction {label} for {entry.disclosed_name!r} "
                        "is not present at its declared source offset"
                    )
        cell_offsets = (
            *(item.name_offset for item in self.entries),
            *(item.revenue_offset for item in self.entries),
            *(item.operating_income_offset for item in self.entries),
            self.filed_total_revenue_offset,
            self.filed_total_operating_income_offset,
            self.revenue_row_label_offset,
            self.operating_income_row_label_offset,
        )
        positions = _source_cell_positions(source, cell_offsets)
        for label, offset in (
            (self.revenue_row_label, self.revenue_row_label_offset),
            (
                self.operating_income_row_label,
                self.operating_income_row_label_offset,
            ),
        ):
            if _normalize_name(positions[offset].visible_text) != _normalize_name(label):
                raise DeclaredSegmentsError(
                    "segment extraction row label does not match the full visible "
                    "source-table cell"
                )
        for entry in self.entries:
            name_position = positions[entry.name_offset]
            revenue_position = positions[entry.revenue_offset]
            income_position = positions[entry.operating_income_offset]
            if _normalize_name(name_position.visible_text) != _normalize_name(
                entry.disclosed_name
            ):
                raise DeclaredSegmentsError(
                    f"segment extraction name {entry.disclosed_name!r} does not "
                    "match the full visible source-table cell"
                )
            for label, value, position in (
                ("revenue", entry.revenue, revenue_position),
                ("operating income", entry.operating_income, income_position),
            ):
                visible_amount = re.sub(r"\s+", "", position.visible_text)
                if visible_amount not in _source_amount_tokens(value):
                    raise DeclaredSegmentsError(
                        f"segment extraction {label} for {entry.disclosed_name!r} "
                        "does not match the full visible source-table cell"
                    )
            if not (
                name_position.column
                == revenue_position.column
                == income_position.column
            ):
                raise DeclaredSegmentsError(
                    f"segment extraction values for {entry.disclosed_name!r} "
                    "are not bound to its source-table column"
                )
            if not (
                name_position.row < revenue_position.row
                and name_position.row < income_position.row
            ):
                raise DeclaredSegmentsError(
                    f"segment extraction name {entry.disclosed_name!r} must "
                    "precede the metric rows as a source-table header"
                )
            if not name_position.is_header:
                raise DeclaredSegmentsError(
                    f"segment extraction name {entry.disclosed_name!r} must "
                    "belong to an actual TH source-table header cell"
                )
        for label, value, offset in (
            (
                "revenue",
                self.filed_total_revenue,
                self.filed_total_revenue_offset,
            ),
            (
                "operating income",
                self.filed_total_operating_income,
                self.filed_total_operating_income_offset,
            ),
        ):
            visible_amount = re.sub(r"\s+", "", positions[offset].visible_text)
            if visible_amount not in _source_amount_tokens(value):
                raise DeclaredSegmentsError(
                    f"segment extraction filed total {label} does not match the "
                    "full visible source-table cell"
                )
        revenue_row = positions[self.revenue_row_label_offset].row
        income_row = positions[self.operating_income_row_label_offset].row
        if any(
            positions[item.revenue_offset].row != revenue_row
            for item in self.entries
        ) or positions[self.filed_total_revenue_offset].row != revenue_row:
            raise DeclaredSegmentsError(
                "segment extraction revenue values and filed total are not bound "
                "to the declared source-table row"
            )
        if any(
            positions[item.operating_income_offset].row != income_row
            for item in self.entries
        ) or positions[self.filed_total_operating_income_offset].row != income_row:
            raise DeclaredSegmentsError(
                "segment extraction operating-income values and filed total are "
                "not bound to the declared source-table row"
            )
        component_columns = tuple(
            positions[item.revenue_offset].column for item in self.entries
        )
        if positions[self.revenue_row_label_offset].column >= min(component_columns):
            raise DeclaredSegmentsError(
                "segment extraction revenue row label must precede component columns"
            )
        if positions[self.operating_income_row_label_offset].column >= min(
            component_columns
        ):
            raise DeclaredSegmentsError(
                "segment extraction operating-income row label must precede component columns"
            )
        if positions[self.filed_total_revenue_offset].column <= max(component_columns):
            raise DeclaredSegmentsError(
                "segment extraction filed revenue total must follow component columns"
            )
        if positions[self.filed_total_operating_income_offset].column <= max(
            component_columns
        ):
            raise DeclaredSegmentsError(
                "segment extraction filed operating-income total must follow component columns"
            )
        revenue_offsets = tuple(item.revenue_offset for item in self.entries)
        income_offsets = tuple(item.operating_income_offset for item in self.entries)
        if revenue_offsets != tuple(sorted(revenue_offsets)):
            raise DeclaredSegmentsError(
                "segment extraction revenue offsets do not preserve declared segment order"
            )
        if income_offsets != tuple(sorted(income_offsets)):
            raise DeclaredSegmentsError(
                "segment extraction operating-income offsets do not preserve declared segment order"
            )
        for label, component_offsets, total, total_offset in (
            (
                "revenue",
                revenue_offsets,
                self.filed_total_revenue,
                self.filed_total_revenue_offset,
            ),
            (
                "operating income",
                income_offsets,
                self.filed_total_operating_income,
                self.filed_total_operating_income_offset,
            ),
        ):
            if not any(
                source.startswith(token, total_offset)
                for token in _source_amount_tokens(total)
            ):
                raise DeclaredSegmentsError(
                    f"segment extraction filed total {label} is not present at "
                    "its declared source offset"
                )
            start = min(component_offsets)
            if total_offset <= max(component_offsets):
                raise DeclaredSegmentsError(
                    f"segment extraction filed total {label} must follow its components"
                )
            if total_offset - start > _MAX_BOUND_REGION_CHARS:
                raise DeclaredSegmentsError(
                    f"segment extraction {label} components and filed total do not "
                    "share one bounded source region"
                )
        all_metric_offsets = (*revenue_offsets, *income_offsets)
        for entry in self.entries:
            nearest_metric = min(
                abs(entry.name_offset - offset) for offset in all_metric_offsets
            )
            if nearest_metric > _MAX_BOUND_REGION_CHARS:
                raise DeclaredSegmentsError(
                    f"segment extraction name {entry.disclosed_name!r} is not bound "
                    "to the same source region as the reported metrics"
                )
        return OperatingSegmentDisclosure(
            entries=tuple(
                SegmentNoteEntry(
                    name=item.disclosed_name,
                    revenue=item.revenue,
                    operating_income=item.operating_income,
                )
                for item in self.entries
            ),
            # Downstream SOTP reconciles the parts to the consolidated whole;
            # preserve the sum of the filed components, not a rounded subtotal.
            total_revenue=sum(
                (item.revenue for item in self.entries), Decimal(0)
            ),
            total_operating_income=sum(
                (item.operating_income for item in self.entries), Decimal(0)
            ),
        )


def _normalize_name(name: str) -> str:
    return re.sub(r"[\s/·&-]+", "", str(name or "")).casefold()


@dataclass(frozen=True)
class DeclaredSegment:
    """One reportable segment's declared economic identity."""

    segment_id: str
    disclosed_name: str
    ksic_code: str
    rationale: str

    def validate(self) -> None:
        if not _SEGMENT_ID.match(self.segment_id):
            raise DeclaredSegmentsError(
                f"segment_id {self.segment_id!r} must be a short lowercase slug "
                "([a-z][a-z0-9_]+); it becomes the run's segment identity"
            )
        if not self.disclosed_name.strip():
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires the disclosed_name the "
                "filing's operating-segment note uses"
            )
        if not self.ksic_code.strip() or not self.ksic_code.strip().isdigit():
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires a numeric ksic_code to "
                "route its archetype through the classification map"
            )
        if len(self.rationale.strip()) < _MIN_RATIONALE_CHARS:
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires a substantive rationale "
                f"(>= {_MIN_RATIONALE_CHARS} chars) for its declared "
                "classification; an untyped segment is a guessed archetype"
            )


@dataclass(frozen=True)
class DeclaredSegments:
    """A loaded, eagerly validated segment map bound to one target."""

    target_id: str
    as_of: str
    source_ref: str
    segments: tuple[DeclaredSegment, ...]
    source_bound_extraction: SourceBoundSegmentExtraction | None = None

    def validate(self) -> None:
        if not self.target_id or not self.as_of:
            raise DeclaredSegmentsError(
                "segment declaration requires target_id and as_of"
            )
        if not self.source_ref.startswith("https://"):
            raise DeclaredSegmentsError(
                "segment declaration source_ref must be an HTTPS reference to "
                "the disclosing filing"
            )
        if len(self.segments) < 2:
            raise DeclaredSegmentsError(
                "a segment declaration exists to type multiple reportable "
                "segments; a single-segment company needs no declaration"
            )
        ids = tuple(item.segment_id for item in self.segments)
        if len(set(ids)) != len(ids):
            raise DeclaredSegmentsError(f"duplicate segment_ids: {ids}")
        names = tuple(_normalize_name(item.disclosed_name) for item in self.segments)
        if len(set(names)) != len(names):
            raise DeclaredSegmentsError(
                "duplicate disclosed_names in the segment declaration"
            )
        for item in self.segments:
            item.validate()
        if self.source_bound_extraction is not None:
            self.source_bound_extraction.validate()
            extracted_names = tuple(
                _normalize_name(item.disclosed_name)
                for item in self.source_bound_extraction.entries
            )
            if extracted_names != names:
                raise DeclaredSegmentsError(
                    "source-bound extraction and declared segment names/order must match exactly"
                )

    def assert_target(self, target_id: str) -> None:
        if self.target_id != target_id:
            raise DeclaredSegmentsError(
                f"segment declaration is bound to {self.target_id}, not "
                f"{target_id}; a declaration cannot be reused across issuers"
            )

    def match_note(
        self, disclosure: OperatingSegmentDisclosure
    ) -> tuple[tuple[DeclaredSegment, SegmentNoteEntry], ...]:
        """Pair every declared segment with the note entry it names — exactly.

        The bijection is the containment: a declared segment the note never
        mentions would let the operator invent a business, and a note segment
        left undeclared would let a run quietly value part of the company as
        if it were the whole. Both refuse.
        """
        by_name = {
            _normalize_name(entry.name): entry for entry in disclosure.entries
        }
        matched: list[tuple[DeclaredSegment, SegmentNoteEntry]] = []
        for declared in self.segments:
            entry = by_name.pop(_normalize_name(declared.disclosed_name), None)
            if entry is None:
                raise DeclaredSegmentsError(
                    f"declared segment {declared.segment_id} names "
                    f"{declared.disclosed_name!r}, which the filing's "
                    "operating-segment note does not disclose"
                )
            matched.append((declared, entry))
        if by_name:
            raise DeclaredSegmentsError(
                "the filing disclosed reportable segments the declaration does "
                f"not cover: {', '.join(entry.name for entry in by_name.values())}; "
                "every disclosed segment must be declared or the run is valuing "
                "part of the company as the whole"
            )
        return tuple(matched)


def load_declared_segments(path: str | Path) -> DeclaredSegments:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DeclaredSegmentsError("segment declaration must be a mapping")
    rows = payload.get("segments")
    if not isinstance(rows, list):
        raise DeclaredSegmentsError("segment declaration requires a segments list")
    extraction_payload = payload.get("source_bound_extraction")
    extraction = None
    if extraction_payload is not None:
        if not isinstance(extraction_payload, dict):
            raise DeclaredSegmentsError(
                "source_bound_extraction must be a mapping"
            )
        extraction_rows = extraction_payload.get("segments")
        totals = extraction_payload.get("filed_totals")
        source_rows = extraction_payload.get("source_rows")
        if (
            not isinstance(extraction_rows, list)
            or not isinstance(totals, dict)
            or not isinstance(source_rows, dict)
        ):
            raise DeclaredSegmentsError(
                "source_bound_extraction requires segments, filed_totals and source_rows"
            )
        revenue_row = source_rows.get("revenue") or {}
        income_row = source_rows.get("operating_income") or {}
        extraction = SourceBoundSegmentExtraction(
            extractor=str(extraction_payload.get("extractor") or ""),
            document_id=str(extraction_payload.get("document_id") or ""),
            member_path=str(extraction_payload.get("member_path") or ""),
            member_sha256=str(extraction_payload.get("member_sha256") or ""),
            reporting_unit=str(extraction_payload.get("reporting_unit") or ""),
            entries=tuple(
                SourceBoundSegmentEntry(
                    disclosed_name=str((row or {}).get("disclosed_name") or ""),
                    revenue=_decimal((row or {}).get("revenue"), "segment revenue"),
                    operating_income=_decimal(
                        (row or {}).get("operating_income"),
                        "segment operating income",
                    ),
                    name_offset=_offset(
                        ((row or {}).get("source_offsets") or {}).get("name"),
                        "segment name offset",
                    ),
                    revenue_offset=_offset(
                        ((row or {}).get("source_offsets") or {}).get("revenue"),
                        "segment revenue offset",
                    ),
                    operating_income_offset=_offset(
                        ((row or {}).get("source_offsets") or {}).get(
                            "operating_income"
                        ),
                        "segment operating-income offset",
                    ),
                )
                for row in extraction_rows
            ),
            filed_total_revenue=_decimal(
                totals.get("revenue"), "filed total revenue"
            ),
            filed_total_operating_income=_decimal(
                totals.get("operating_income"), "filed total operating income"
            ),
            filed_total_revenue_offset=_offset(
                (totals.get("source_offsets") or {}).get("revenue"),
                "filed-total revenue offset",
            ),
            filed_total_operating_income_offset=_offset(
                (totals.get("source_offsets") or {}).get("operating_income"),
                "filed-total operating-income offset",
            ),
            revenue_row_label=str(revenue_row.get("label") or ""),
            revenue_row_label_offset=_offset(
                revenue_row.get("offset"), "revenue row-label offset"
            ),
            operating_income_row_label=str(income_row.get("label") or ""),
            operating_income_row_label_offset=_offset(
                income_row.get("offset"), "operating-income row-label offset"
            ),
        )
    declared = DeclaredSegments(
        target_id=str(payload.get("target_id") or ""),
        as_of=str(payload.get("as_of") or ""),
        source_ref=str(payload.get("source_ref") or ""),
        segments=tuple(
            DeclaredSegment(
                segment_id=str((row or {}).get("segment_id") or ""),
                disclosed_name=str((row or {}).get("disclosed_name") or ""),
                ksic_code=str((row or {}).get("ksic_code") or ""),
                rationale=str((row or {}).get("rationale") or ""),
            )
            for row in rows
        ),
        source_bound_extraction=extraction,
    )
    declared.validate()
    return declared
