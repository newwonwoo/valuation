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
    """Locate reportable-segment columns in one- or two-tier IFRS 8 headers."""
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

    # Some statutory notes group columns under 보고부문/기타부문 and put the
    # actual reportable names one row lower. The economic names may not end in
    # '부문', so locate the grouped columns first, then read the next header row.
    for group_row_index, row in enumerate(grid):
        total_columns = tuple(
            column_index
            for column_index, cell in enumerate(row)
            if _squeeze(cell) in _SEGMENT_TOTAL_CELLS
        )
        for total_column in total_columns:
            if total_column < 3:
                continue
            group_columns = tuple(
                column_index
                for column_index in range(1, total_column)
                if (
                    _squeeze(row[column_index]) == "보고부문"
                    or (
                        _SEGMENT_NAME.match(_squeeze(row[column_index]))
                        and _squeeze(row[column_index]) not in _SEGMENT_TOTAL_CELLS
                    )
                )
            )
            if len(group_columns) < 2:
                continue
            for name_row_index in range(
                group_row_index + 1, min(len(grid), group_row_index + 4)
            ):
                name_row = grid[name_row_index]
                named: list[tuple[int, str]] = []
                valid = True
                for column_index in group_columns:
                    if column_index >= len(name_row):
                        valid = False
                        break
                    candidate = " ".join(str(name_row[column_index]).split()).strip()
                    squeezed = _squeeze(candidate)
                    if (
                        not candidate
                        or squeezed in _STRUCTURAL_CELLS
                        or _amount(candidate) is not None
                    ):
                        valid = False
                        break
                    named.append((column_index, candidate))
                if not valid:
                    continue
                canonical = tuple(
                    re.sub(r"[\s/·&-]+", "", name).casefold()
                    for _, name in named
                )
                if len(named) >= 2 and len(set(canonical)) == len(named):
                    return name_row_index, tuple(named), total_column
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


@dataclass(frozen=True)
class SegmentReconciliation:
    """The parts, the whole, and the difference between them — all three kept.

    A sum-of-the-parts valuation is only honest if the parts are known to be
    *all* of the company. The segment note gives the parts; the consolidated
    income statement gives the whole; the difference is the inter-segment
    elimination the entity itself reports. For 대한제강 FY2025 that difference
    is -75,737,636,257 KRW of revenue — a real quantity that would silently
    inflate a segment-summed valuation if it were dropped.

    So it is not dropped: nothing here compares it to a threshold, judges it,
    or nets it away. It is computed from two authoritative figures and carried,
    which is what makes a downstream sum-of-the-parts auditable rather than
    optimistic. A consumer that wants the segments must take this record, and
    taking it means holding the elimination.
    """

    disclosure: OperatingSegmentDisclosure
    consolidated_revenue: Decimal
    consolidated_operating_income: Decimal

    @property
    def revenue_elimination(self) -> Decimal:
        """Consolidated revenue less the segment total: the inter-segment sales
        the entity eliminates on consolidation (negative when segments trade
        with each other, which is the normal case)."""
        return self.consolidated_revenue - self.disclosure.total_revenue

    @property
    def operating_income_elimination(self) -> Decimal:
        return (
            self.consolidated_operating_income
            - self.disclosure.total_operating_income
        )

    def validate(self) -> None:
        self.disclosure.validate()
        if self.consolidated_revenue <= 0:
            raise SegmentNoteError(
                "segment reconciliation requires the consolidated revenue the "
                "income statement reports; a non-positive whole cannot anchor "
                "a sum of parts"
            )


def reconcile_segments(
    disclosure: OperatingSegmentDisclosure,
    *,
    consolidated_revenue: Decimal,
    consolidated_operating_income: Decimal,
) -> SegmentReconciliation:
    """Bind disclosed segments to the consolidated whole they must add up to."""
    reconciliation = SegmentReconciliation(
        disclosure=disclosure,
        consolidated_revenue=consolidated_revenue,
        consolidated_operating_income=consolidated_operating_income,
    )
    reconciliation.validate()
    return reconciliation
