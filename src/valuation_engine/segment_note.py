"""The IFRS operating-segment note, read as a reconciled table.

A consolidated filing's screen (``_disclosed_segment_names``) is deliberately
over-inclusive: it exists to *refuse* a company whose filing looks
multi-segment, and it happily catches process rows (압연), entity subtotals
(대한제강(주) 합계) and industry labels (철강) alongside real segments. It can
therefore never be the source of truth for *which* segments a company has.

That source is the operating-segment note itself (IFRS 8 "영업부문 정보"),
where the entity names its own reportable segments and gives each one's revenue
and operating result. This module reads that note and nothing else.

Two properties make the read safe rather than positional guesswork:

- the segment block is located **by header name**, not by column index — the
  columns between the first named ``…부문`` cell and the ``부문 합계`` cell
  that closes the ``영업부문`` group;
- every metric row is **reconciled**: the segment values must sum to that
  row's own ``부문 합계`` cell. Real notes drop empty cells, which shifts later
  columns (the FY2025 영업이익 row lands its adjustment two columns early), so
  a parse that did not reconcile would silently read a neighbouring number.
  A row that does not reconcile is refused, not repaired.

The note lists the current period first and the comparative after it, so the
first table that satisfies both properties is the current period's.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

# The filing-table reader already handles colspan/rowspan expansion for the
# segment screen; the note is the same HTML dialect, so it is read with the
# same grid builder rather than a second one that could disagree with it.
from .generic_kr_industry import _SegmentTableParser, _expand_table


class SegmentNoteError(ValueError):
    """Raised when the operating-segment note cannot be read as disclosed."""


#: Header cells that name the table's structure rather than a segment.
_STRUCTURAL_CELLS = frozenset(
    {
        "영업부문",
        "보고부문",
        "부문",
        "부문합계",
        "부문의합계",
        "부문의합계합계",
        "기업전체총계",
        "기업전체총계합계",
        "중요한조정사항",
        "부문간제거한금액",
        "공시금액",
    }
)

#: The cell that closes the reportable-segment group.
_SEGMENT_TOTAL_CELLS = frozenset({"부문합계", "부문의합계"})

_REVENUE_LABELS = frozenset({"수익", "수익(매출액)", "매출액", "영업수익", "매출"})
_OPERATING_INCOME_LABELS = frozenset(
    {"영업이익", "영업이익(손실)", "영업손익", "영업이익(손실)계"}
)

_SEGMENT_NAME = re.compile(r"^\S.*부문$")


def _squeeze(cell: str) -> str:
    return re.sub(r"\s+", "", cell or "")


def _label(cell: str) -> str:
    return _squeeze(cell)


def _amount(cell: str) -> Decimal | None:
    """Parse a filed money cell; ``(x)`` is negative, blank is absent."""
    text = _squeeze(cell).replace(",", "")
    if not text or text in {"-", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if text.startswith("△") or text.startswith("▲"):
        negative = True
        text = text[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - guarded by the regex above
        return None
    return -value if negative else value


@dataclass(frozen=True)
class SegmentNoteEntry:
    """One reportable segment as the note itself names and measures it."""

    name: str
    revenue: Decimal
    operating_income: Decimal


@dataclass(frozen=True)
class OperatingSegmentDisclosure:
    """The current period's reportable segments, reconciled to their total."""

    entries: tuple[SegmentNoteEntry, ...]
    total_revenue: Decimal
    total_operating_income: Decimal

    @property
    def segment_names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    def validate(self) -> None:
        if len(self.entries) < 2:
            raise SegmentNoteError(
                "an operating-segment disclosure needs at least two reportable "
                "segments; a single-segment note is the whole-company case"
            )
        names = self.segment_names
        if len(set(names)) != len(names):
            raise SegmentNoteError(f"duplicate reportable segment names: {names}")
        revenue = sum((entry.revenue for entry in self.entries), Decimal(0))
        income = sum((entry.operating_income for entry in self.entries), Decimal(0))
        if revenue != self.total_revenue or income != self.total_operating_income:
            raise SegmentNoteError(
                "reportable segment values do not sum to the disclosed segment total"
            )


def _segment_columns(grid: list[list[str]]) -> tuple[int, tuple[tuple[int, str], ...], int] | None:
    """Locate the reportable-segment block by header name.

    Returns ``(header_row, ((column, name), …), total_column)`` for the first
    header row that names two or more segments and is closed by a
    ``부문 합계`` cell — the ``영업부문`` group. The mirrored group under
    ``중요한 조정사항`` repeats the same names, so only the first block is taken.
    """
    for row_index, row in enumerate(grid):
        named: list[tuple[int, str]] = []
        for column_index, cell in enumerate(row):
            squeezed = _squeeze(cell)
            if squeezed in _SEGMENT_TOTAL_CELLS and named:
                if len(named) < 2:
                    break
                return row_index, tuple(named), column_index
            if squeezed in _STRUCTURAL_CELLS or not squeezed:
                continue
            name = " ".join(str(cell).split()).strip()
            if _SEGMENT_NAME.match(_squeeze(name)) and (
                not named or name != named[-1][1]
            ):
                named.append((column_index, name))
    return None


def _metric_row(
    grid: list[list[str]], labels: frozenset[str], header_row: int
) -> list[str] | None:
    for row in grid[header_row + 1 :]:
        if row and _label(row[0]) in labels:
            return row
    return None


def _read_metric(
    row: list[str],
    columns: tuple[tuple[int, str], ...],
    total_column: int,
    metric: str,
) -> tuple[tuple[Decimal, ...], Decimal]:
    values: list[Decimal] = []
    for column_index, name in columns:
        if column_index >= len(row):
            raise SegmentNoteError(
                f"{metric} row has no cell for reportable segment {name}"
            )
        amount = _amount(row[column_index])
        if amount is None:
            raise SegmentNoteError(
                f"{metric} row carries no readable amount for segment {name}"
            )
        values.append(amount)
    if total_column >= len(row):
        raise SegmentNoteError(f"{metric} row has no segment-total cell")
    total = _amount(row[total_column])
    if total is None:
        raise SegmentNoteError(f"{metric} row carries no readable segment total")
    return tuple(values), total


def parse_operating_segment_note(text: str) -> OperatingSegmentDisclosure:
    """Read the current period's reportable segments from the note's HTML."""
    parser = _SegmentTableParser()
    parser.feed(text or "")
    for raw_table in parser.tables:
        grid = _expand_table(raw_table)
        located = _segment_columns(grid)
        if located is None:
            continue
        header_row, columns, total_column = located
        revenue_row = _metric_row(grid, _REVENUE_LABELS, header_row)
        income_row = _metric_row(grid, _OPERATING_INCOME_LABELS, header_row)
        if revenue_row is None or income_row is None:
            continue
        revenues, revenue_total = _read_metric(
            revenue_row, columns, total_column, "revenue"
        )
        incomes, income_total = _read_metric(
            income_row, columns, total_column, "operating income"
        )
        disclosure = OperatingSegmentDisclosure(
            entries=tuple(
                SegmentNoteEntry(
                    name=name, revenue=revenue, operating_income=income
                )
                for (_, name), revenue, income in zip(
                    columns, revenues, incomes, strict=True
                )
            ),
            total_revenue=revenue_total,
            total_operating_income=income_total,
        )
        disclosure.validate()
        return disclosure
    raise SegmentNoteError(
        "no reconciled operating-segment table found in the note; the filing "
        "does not disclose reportable segments in the expected IFRS 8 layout"
    )
