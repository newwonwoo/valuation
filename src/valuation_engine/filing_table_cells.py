"""Point at a cell, not at a phrase: reading a filing table by its identity.

The locator cage asks the model for a verbatim quote and checks that the quote
carries one of the metric's registered anchor terms. That works, and it has a
cost the registry shows plainly: ``realized_price`` reached five anchors across
three issuers, each one commented with the laundering it might open. The list
has to grow with every new company, and every growth widens what may be pointed
at — the opposite of what a cage is for.

A table cell can be verified without that list. The proposal names a table and
a cell inside it by the headings that lead to it; the verifier then asks a
different question:

1. **Is this the right table?** Its own headings must carry some of the
   metric's vocabulary and none of the vocabulary that would make it a
   different table. The exclusion half is the decisive one — a raw-material
   price table sits beside the product one and reads almost identically, so
   ``must_not_have`` is what stops a purchase price entering as a selling
   price. Exclusions are stable; inclusions are what would otherwise grow.
2. **Does the coordinate hold?** The row path must match the row's own label
   cells and the column path the headers standing over that column, after
   rowspan and colspan are expanded. A coordinate that does not fit the grid
   is refused rather than repaired.
3. **Is the cell a number in a registered unit, from this period?** The unit
   token must be one the task registered, its dimension must be the task's
   dimension, and the column must not be a prior period or a forecast — the
   same chronology rule the quote path uses.

What survives is read out of the grid by the machine. The model never supplies
the number; it supplies where to look, and every step of that pointing is
checked against the filing's own structure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
from calendar import monthrange
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import unescape
from pathlib import Path
import re
import json
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .dart_documents import DartOriginalFilingDocument
from .dart_kpi import (
    DartKPIExtractionError,
    DartKPIExtractionSpec,
    DartKPIObservation,
    extract_dart_kpi,
    _visible_text,
    _normalize_space,
)
from .generic_kr_industry import _SegmentTableParser, _expand_table
from .llm_filing_locators import validate_filing_period_context
from .llm_transport import ProposalTransport
from .proposal_parsing import (
    ProposalParseError,
    complete_with_repair,
    parse_json_object,
    require_keys,
    str_tuple,
    text_field,
)
from .runtime_authority import llm_proposal_scope
from .runtime_resources import runtime_registry_path


DEFAULT_TABLE_READING_TASKS = runtime_registry_path(
    "kr_filing_table_reading_tasks.yaml"
)

#: Unit dimensions a task may declare. A token's dimension has to be the task's
#: own: a price per kilogram is not a price per tonne told differently, and a
#: ratio is not a price at all.
_UNIT_DIMENSIONS: dict[str, str] = {
    "KRW_per_ton": "PRICE_PER_MASS",
    "KRW_thousand_per_ton": "PRICE_PER_MASS",
    "KRW_per_kg": "PRICE_PER_MASS",
    "ratio": "RATIO",
    "%": "RATIO",
    "tons_per_year": "MASS_RATE",
    "count": "COUNT",
}


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _amount(cell: str) -> Decimal | None:
    """Parse a filed money or ratio cell; ``(x)`` and ``△x`` are negative."""
    text = _squeeze(cell).replace(",", "").removesuffix("%")
    if not text or text in {"-", "—", "–"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if text[:1] in {"△", "▲", "-"}:
        negative = True
        text = text[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - guarded by the regex
        return None
    return -value if negative else value


@dataclass(frozen=True)
class TableIdentity:
    """What makes this the metric's table, and what makes it a different one."""

    must_have_any: tuple[str, ...]
    must_not_have: tuple[str, ...]

    def check(self, headings: Iterable[str], *, metric: str) -> None:
        joined = " ".join(_squeeze(item) for item in headings)
        forbidden = tuple(term for term in self.must_not_have if _squeeze(term) in joined)
        if forbidden:
            raise ProposalParseError(
                f"the table proposed for {metric} carries "
                f"{', '.join(forbidden)}; that is a different table, and "
                "reading it here would relabel its figures as this metric's"
            )
        if self.must_have_any and not any(
            _squeeze(term) in joined for term in self.must_have_any
        ):
            raise ProposalParseError(
                f"the table proposed for {metric} carries none of its "
                f"vocabulary ({', '.join(self.must_have_any)}); the proposal "
                "has not shown that this table is about the metric"
            )


@dataclass(frozen=True)
class TableReadingTask:
    """One metric, described by what it is rather than by one issuer's wording."""

    metric: str
    definition: str
    canonical_unit: str
    unit_dimension: str
    table_identity: TableIdentity
    source_unit_map: tuple[tuple[str, str], ...]
    require_current_period_marker: bool = False

    def validate(self) -> None:
        if not self.metric or not self.definition:
            raise ProposalParseError("a reading task requires metric and definition")
        if not self.source_unit_map:
            raise ProposalParseError(
                f"reading task {self.metric} requires a registered unit map"
            )
        if not self.table_identity.must_have_any:
            raise ProposalParseError(
                f"reading task {self.metric} requires table vocabulary"
            )
        for token, unit in self.source_unit_map:
            dimension = _UNIT_DIMENSIONS.get(unit)
            if dimension is None:
                raise ProposalParseError(
                    f"reading task {self.metric} maps {token!r} to unregistered "
                    f"unit {unit!r}"
                )
            if dimension != self.unit_dimension:
                raise ProposalParseError(
                    f"reading task {self.metric} is {self.unit_dimension} but "
                    f"{token!r} is {dimension}; a unit of the wrong dimension "
                    "cannot be converted into this metric"
                )

    def unit_for(self, token: str) -> str:
        for registered, unit in self.source_unit_map:
            if _squeeze(registered) == _squeeze(token):
                return unit
        raise ProposalParseError(
            f"reading task {self.metric} does not register unit token {token!r}; "
            f"registered: {', '.join(item for item, _ in self.source_unit_map)}"
        )


def load_table_reading_tasks(
    path: str | Path = DEFAULT_TABLE_READING_TASKS,
) -> dict[str, TableReadingTask]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows = (payload or {}).get("tasks")
    if not isinstance(rows, Mapping) or not rows:
        raise ProposalParseError("the reading-task registry requires tasks")
    tasks: dict[str, TableReadingTask] = {}
    for metric, spec in rows.items():
        identity = (spec or {}).get("table_identity") or {}
        task = TableReadingTask(
            metric=str(metric),
            definition=str((spec or {}).get("definition") or ""),
            canonical_unit=str((spec or {}).get("canonical_unit") or ""),
            unit_dimension=str((spec or {}).get("unit_dimension") or ""),
            table_identity=TableIdentity(
                must_have_any=tuple(
                    str(item) for item in identity.get("must_have_any") or ()
                ),
                must_not_have=tuple(
                    str(item) for item in identity.get("must_not_have") or ()
                ),
            ),
            source_unit_map=tuple(
                (str(token), str(unit))
                for token, unit in ((spec or {}).get("source_unit_map") or {}).items()
            ),
            require_current_period_marker=bool(
                (spec or {}).get("require_current_period_marker", False)
            ),
        )
        task.validate()
        tasks[task.metric] = task
    return tasks


@dataclass(frozen=True)
class TableCellProposal:
    """Where the model says the number is. It never says what the number is."""

    metric: str
    member_path: str
    table_index: int
    row_path: tuple[str, ...]
    column_path: tuple[str, ...]
    unit_token: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "TableCellProposal":
        require_keys(
            row,
            required=(
                "metric",
                "member_path",
                "table_index",
                "row_path",
                "column_path",
                "unit_token",
            ),
            label="table_cell",
        )
        try:
            table_index = int(row["table_index"])
        except (TypeError, ValueError) as error:
            raise ProposalParseError(
                "table_cell.table_index must be an integer"
            ) from error
        if table_index < 0:
            raise ProposalParseError("table_cell.table_index must not be negative")
        def _path(value: object, label: str) -> tuple[str, ...]:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, (list, tuple)) or not value:
                raise ProposalParseError(f"table_cell.{label} requires at least one heading")
            return tuple(text_field(item, f"table_cell.{label}") for item in value)

        return cls(
            metric=text_field(row["metric"], "table_cell.metric"),
            member_path=text_field(row["member_path"], "table_cell.member_path"),
            table_index=table_index,
            row_path=_path(row["row_path"], "row_path"),
            column_path=_path(row["column_path"], "column_path"),
            unit_token=text_field(row["unit_token"], "table_cell.unit_token"),
        )


@dataclass(frozen=True)
class TableCellReading:
    """A verified cell, with the receipts a reviewer needs to reopen it."""

    metric: str
    member_path: str
    table_index: int
    row_index: int
    column_index: int
    row_path: tuple[str, ...]
    column_path: tuple[str, ...]
    value: Decimal
    unit: str
    unit_token: str
    grid_sha256: str
    governing_unit_cells: tuple[tuple[int, int], ...]

    def receipt(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "member_path": self.member_path,
            "table_index": self.table_index,
            "cell": [self.row_index, self.column_index],
            "row_path": list(self.row_path),
            "column_path": list(self.column_path),
            "unit_token": self.unit_token,
            "unit": self.unit,
            "grid_sha256": self.grid_sha256,
            "governing_unit_cells": [list(cell) for cell in self.governing_unit_cells],
        }


#: Characters that may sit around a unit token in a filing. A token found with
#: any other character against it is part of a longer unit, not this one.
_UNIT_BOUNDARY = re.compile(r"[\s:：()（）\[\]{},]|^|$")


def _require_declared_unit(
    unit_token: str, text: str, *, task: TableReadingTask
) -> None:
    """The token must be the whole unit the filing declares, not part of one.

    ``원/톤`` occurs inside ``천원/톤``, so a substring test lets a proposal read
    a table declared in thousands of won as though it were in won — the model
    changing a valuation input by a factor of a thousand through its choice of
    spelling. The token has to stand alone.
    """
    if not _squeeze(unit_token):
        raise ProposalParseError(f"reading task {task.metric} needs a unit token")
    if not _unit_matches(unit_token, text):
        raise ProposalParseError(
            f"the unit {unit_token!r} proposed for {task.metric} is not "
            "declared with the table as a unit of its own; a unit has to be "
            "read from the filing, not chosen from the registry"
        )


def _unit_matches(unit_token: str, text: str) -> list[re.Match[str]]:
    """Permit internal spacing while preserving original token boundaries."""
    needle = _squeeze(unit_token)
    if not needle:
        return []
    pattern = r"\s*".join(re.escape(char) for char in needle)
    return [
        match for match in re.finditer(pattern, text)
        if (match.start() == 0 or _UNIT_BOUNDARY.fullmatch(text[match.start() - 1])
            or (needle == "%" and text[match.start() - 1].isdigit()))
        and (match.end() == len(text) or _UNIT_BOUNDARY.fullmatch(text[match.end()]))
        and not text[:match.start()].rstrip(" \t\r\n()（）[]{}＜＞").endswith(("/", "／", "·", "⋅", "*", "×", "^"))
        and not text[match.end():].lstrip(" \t\r\n()（）[]{}＜＞").startswith(("/", "／", "·", "⋅", "*", "×", "^"))
    ]


def _authorized_unit_spans(unit_token: str, text: str, *, caption: bool,
                           inline_value: bool = False) -> tuple[tuple[int, int], ...]:
    """Accept a complete declaration, never a token found inside prose.

    Captions require an explicit unit marker. Cells may themselves be a unit,
    or end in a parenthesized unit. Only the selected numeric cell may declare
    a trailing percent. Unknown operators remain part of the complete token
    and therefore fail equality without a blacklist of compound spellings.
    """
    needle = _squeeze(unit_token).strip("()").casefold()
    candidates: list[tuple[int, int]] = []
    marker = r"(?:단위|units?)\s*[:：]?\s*"
    for match in re.finditer(r"[（(]" + marker + r"([^()（）]+)[）)]", text, re.IGNORECASE):
        remainder = text[match.end():].strip()
        note = re.match(
            r"(?:주\s*\d*\s*[)）:：.]|주석\b|비고\s*[:：]|notes?\b\s*[:：]?|※)", remainder, re.IGNORECASE
        )
        # Only these scope-only notes have a defined interpretation here.
        # Unknown note semantics (including unit overrides) require a separate
        # declaration contract, rather than an ever-growing symbol blacklist.
        neutral_note = note is not None and _squeeze(remainder[note.end():]).casefold() in {
            "국내판매기준", "domesticsales", "연결기준", "별도기준",
        }
        if not remainder or (caption and neutral_note):
            candidates.append(match.span(1))
    plain = re.search(marker + r"([^()（）]+)$", text, re.IGNORECASE)
    if plain:
        candidates.append(plain.span(1))
    if not caption:
        candidates.append((0, len(text)))
        suffix = re.search(r"[（(]([^()（）]+)[）)]\s*$", text)
        if suffix and all(char.isalnum() or char.isspace() for char in text[:suffix.start()]):
            candidates.append(suffix.span(1))
    approved: list[tuple[int, int]] = []
    for start, end in candidates:
        declaration = text[start:end]
        tokens = list(re.finditer(r"[^,]+", declaration))
        # Comma aliases are allowed only when every full token is the same
        # unit (e.g. 원/kg, 원/KG), never an unrecognized second dimension.
        if not tokens or any(_squeeze(token.group()).casefold() != needle for token in tokens):
            continue
        for token in tokens:
            value = token.group()
            if _squeeze(value) != _squeeze(unit_token).strip("()"):
                continue
            left = start + token.start() + len(value) - len(value.lstrip())
            approved.append((left, start + token.end() - len(value) + len(value.rstrip())))
    if inline_value and needle == "%" and text.rstrip().endswith("%"):
        numeric = text.rstrip()[:-1].strip()
        if _amount(numeric) is not None:
            end = len(text.rstrip())
            approved.append((end - 1, end))
    return tuple(dict.fromkeys(approved))


def _grids(html_text: str) -> list[list[list[str]]]:
    parser = _SegmentTableParser()
    parser.feed(html_text or "")
    return [_expand_table(table) for table in parser.tables]


#: How much of the text before a table is read as its caption. A filing puts a
#: table's title and its unit above the table, outside the markup — 대한제강's
#: product price table is headed "제품별 구체적인 가격변동추이" and "(단위: 천원/톤)"
#: with neither word inside the grid. Identity read from the grid alone would
#: therefore refuse the very table it is looking for.
_CAPTION_WINDOW = 600

_TAG = re.compile(r"<[^>]+>")


_TABLE_BOUNDARY = re.compile(r"<\s*(/?)\s*table\b", re.I)


def _table_captions(html_text: str) -> list[str]:
    """The text between the previous table and this one, in document order.

    Only that gap: splitting on opening tags alone would put the whole previous
    table into the next one's caption, and then a neighbouring grid could lend
    its vocabulary to validate the wrong table, or a term inside it could reject
    the right one.
    """
    text = str(html_text or "")
    captions: list[str] = []
    cursor = 0
    depth = 0
    for match in _TABLE_BOUNDARY.finditer(text):
        closing = bool(match.group(1))
        if closing:
            depth = max(0, depth - 1)
            if depth == 0:
                cursor = match.end()
            continue
        if depth == 0:
            gap = _TAG.sub(" ", text[cursor:match.start()])
            gap = unescape(gap)
            captions.append(re.sub(r"\s+", " ", gap)[-_CAPTION_WINDOW:].strip())
        depth += 1
    return captions


def _grid_sha256(grid: Sequence[Sequence[str]]) -> str:
    body = "\n".join("\t".join(_squeeze(cell) for cell in row) for row in grid)
    return sha256(body.encode("utf-8")).hexdigest()


def _headings(grid: Sequence[Sequence[str]], *, rows: int = 4) -> tuple[str, ...]:
    """The cells that say what a table is: its first rows and its row labels."""
    collected: list[str] = []
    for row in grid[:rows]:
        collected.extend(str(cell) for cell in row if str(cell).strip())
    for row in grid:
        if row and str(row[0]).strip():
            collected.append(str(row[0]))
    return tuple(collected)


def _locate_column(
    grid: Sequence[Sequence[str]], column_path: Sequence[str], *, metric: str
) -> int:
    """The column every heading in the path stands over."""
    for item in column_path:
        if not _is_label(item):
            raise ProposalParseError(
                f"column path for {metric} uses {item!r}, which is a figure "
                "rather than a heading; a column is addressed by what stands "
                "over it, never by a number inside it"
            )
    width = max((len(row) for row in grid), default=0)
    candidates = []
    for column in range(width):
        stack = [
            _squeeze(row[column])
            for row in grid
            if column < len(row) and _is_label(row[column])
        ]
        if all(
            any(_squeeze(heading) == cell for cell in stack) for heading in column_path
        ):
            candidates.append(column)
    if not candidates:
        raise ProposalParseError(
            f"no column in the table carries the headings proposed for {metric} "
            f"({' / '.join(column_path)}); the coordinate does not fit the grid"
        )
    if len(candidates) > 1:
        raise ProposalParseError(
            f"the headings proposed for {metric} ({' / '.join(column_path)}) fit "
            f"{len(candidates)} columns; extend the path until it names one"
        )
    return candidates[0]


def _is_missing_value(cell: str) -> bool:
    return _squeeze(cell).casefold() in {"-", "–", "—", "n/a", "na", "해당없음"}


def _top_header_block(grid: Sequence[Sequence[str]]) -> Sequence[Sequence[str]]:
    """Only a single top header block may govern this rectangular table.

    A new label-only row after numeric data can introduce a different period
    section. Reject it instead of borrowing a heading across sections.
    """
    first_data = next((i for i, row in enumerate(grid)
                       if any(_amount(cell) is not None or _is_missing_value(cell)
                              for cell in row)), len(grid))
    if not first_data or first_data == len(grid):
        raise ProposalParseError("table has no distinct top header block")
    for row in grid[first_data:]:
        if any(str(cell).strip() for cell in row) and not any(
            _amount(cell) is not None or _is_missing_value(cell) for cell in row
        ):
            raise ProposalParseError("table contains ambiguous vertical header sections")
    return grid[:first_data]


def _is_label(cell: str) -> bool:
    """A heading names something; a figure is what the heading points at.

    A path made of figures would let a proposal address any cell by quoting its
    own value — the prompt shows every number, so the model could pick one and
    relabel it as whatever metric it liked. Paths therefore match label cells
    only, and a cell that reads as a number is not a label.
    """
    text = str(cell or "").strip()
    return bool(text) and not _is_missing_value(text) and _amount(text) is None


def _validate_complete_reporting_period(text: str, effective_date: str) -> None:
    """Bind an explicit Korean reporting-period header to its period end.

    Relative current-period labels retain the existing filing contract. An
    explicit calendar year without an interim marker denotes the annual
    period, not any interim period in that year.
    """
    effective = date.fromisoformat(effective_date)
    years = {int(year) for year in re.findall(r"(?<!\d)(\d{4})\s*년", text)}
    if years and years != {effective.year}:
        raise ProposalParseError("column header does not match the complete reporting period")
    months = set()
    for quarter, english in re.findall(r"([1-4])\s*분기|Q\s*([1-4])", text, re.IGNORECASE):
        months.add(3 * int(quarter or english))
    if re.search(r"하반기|H\s*2|2\s*H", text, re.IGNORECASE):
        raise ProposalParseError("second-half duration requires an explicit reporting-period contract")
    if re.search(r"반기|H\s*1|1\s*H", text, re.IGNORECASE):
        months.add(6)
    if years and not months and "당" not in text:
        months.add(12)
    if months and (months != {effective.month} or effective.day != monthrange(effective.year, effective.month)[1]):
        raise ProposalParseError("column header does not match the complete reporting period")


def _locate_row(
    grid: Sequence[Sequence[str]], row_path: Sequence[str], *, metric: str
) -> int:
    for item in row_path:
        if not _is_label(item):
            raise ProposalParseError(
                f"row path for {metric} uses {item!r}, which is a figure rather "
                "than a heading; a cell is addressed by what names it, never by "
                "the number it holds"
            )
    candidates = []
    for index, row in enumerate(grid):
        labels = [_squeeze(cell) for cell in row if _is_label(cell)]
        if all(any(_squeeze(item) == cell for cell in labels) for item in row_path):
            candidates.append(index)
    if not candidates:
        raise ProposalParseError(
            f"no row in the table carries the labels proposed for {metric} "
            f"({' / '.join(row_path)}); the coordinate does not fit the grid"
        )
    if len(candidates) > 1:
        raise ProposalParseError(
            f"the labels proposed for {metric} ({' / '.join(row_path)}) fit "
            f"{len(candidates)} rows; extend the path until it names one"
        )
    return candidates[0]


def read_table_cell(
    member_text: str,
    proposal: TableCellProposal,
    task: TableReadingTask,
    *,
    effective_date: str,
) -> TableCellReading:
    """Verify a proposed coordinate and read the machine's own value from it."""
    if proposal.metric != task.metric:
        raise ProposalParseError(
            f"table_cell names {proposal.metric} but was checked against "
            f"{task.metric}"
        )
    grids = _grids(member_text)
    if proposal.table_index >= len(grids):
        raise ProposalParseError(
            f"table_cell for {task.metric} names table {proposal.table_index} "
            f"but the member holds {len(grids)}"
        )
    grid = grids[proposal.table_index]
    if not grid:
        raise ProposalParseError(
            f"table {proposal.table_index} in {proposal.member_path} is empty"
        )

    captions = _table_captions(member_text)
    caption = (
        captions[proposal.table_index]
        if proposal.table_index < len(captions)
        else ""
    )
    # A filing titles its tables and states their units above the markup, so
    # identity is read from the caption together with the grid's own headings.
    task.table_identity.check(
        _headings(grid) + (caption,), metric=task.metric
    )

    headers = _top_header_block(grid)
    column = _locate_column(headers, proposal.column_path, metric=task.metric)
    if any(column >= len(item) or (_amount(item[column]) is None and not _is_missing_value(item[column]))
           for item in grid[len(headers):]):
        raise ProposalParseError("selected column crosses non-data or vertical header sections")
    # Numeric years are readable decimals but may introduce a new vertical
    # period section. This contract has no section boundaries, so neither a
    # repeated header label nor an unqualified calendar year is valid data.
    for item in grid[len(headers):]:
        if any(index != column and _squeeze(cell) and any(
            index < len(header) and _squeeze(cell) == _squeeze(header[index])
            for header in headers
        ) for index, cell in enumerate(item)):
            raise ProposalParseError("table contains repeated vertical header labels")
        if any(value is not None and Decimal("1900") <= value <= Decimal("2199")
               and value == value.to_integral_value()
               for value in (_amount(cell) for cell in item)):
            raise ProposalParseError("bare calendar year is ambiguous with a vertical period header")
    row = _locate_row(grid, proposal.row_path, metric=task.metric)
    if row < len(headers):
        raise ProposalParseError("selected row belongs to the header block")
    if "%" in grid[row][column] and (task.unit_dimension != "RATIO" or proposal.unit_token != "%"):
        raise ProposalParseError("selected percentage cell conflicts with the task unit")

    # An explicit unit column governs its own row. Unknown tokens must reject
    # even when a different body row happens to carry a registered unit.
    unit_columns = []
    for index in range(max(map(len, headers))):
        explicit_unit = any(index < len(header) and re.search(
            r"단위|통화|unit|currency", _squeeze(header[index]), re.IGNORECASE
        ) for header in headers)
        # Column roles can also be demonstrated by actual registered unit
        # cells, independent of the issuer's chosen column heading.
        demonstrated_unit = any(index < len(item) and any(
            _squeeze(item[index]).strip("()") == _squeeze(token).strip("()")
            for token, _ in task.source_unit_map
        ) for item in grid[len(headers):])
        if explicit_unit or demonstrated_unit:
            unit_columns.append(index)
            row_unit = grid[row][index] if index < len(grid[row]) else ""
            if _squeeze(row_unit).strip("()") != _squeeze(proposal.unit_token).strip("()"):
                raise ProposalParseError("mixed units: selected row unit does not match proposed unit")
            task.unit_for(proposal.unit_token)

    # A dimension-shaped row label cannot silently override a global unit.
    # Unknown currencies/denominators need no blacklist: any slash-bearing
    # nonnumeric cell must be an exact declared token to enter this contract.
    for cell in grid[row]:
        if _amount(cell) is None and not _is_missing_value(cell) and any(mark in cell for mark in ("/", "%", "／", "％")):
            if _squeeze(cell).strip("()") != _squeeze(proposal.unit_token).strip("()"):
                raise ProposalParseError("selected row contains an unverified unit-shaped cell")

    # The unit has to be present where the table is, not merely registered:
    # otherwise a model could attach any registered token to any number.
    governing_cells = tuple(dict.fromkeys(
        [(index, column) for index in range(len(headers))]
        + [(row, index) for index in unit_columns] + [(row, column)]
    ))
    authorized = _authorized_unit_spans(proposal.unit_token, caption, caption=True)
    if not authorized and re.search(r"(?:단위|units?)(?:\s*[:：]|\s+)", caption, re.IGNORECASE):
        raise ProposalParseError("caption unit is not declared with the table as a unit of its own under a verified complete contract")
    if not authorized and not any(_authorized_unit_spans(
        proposal.unit_token, grid[r][c], caption=False, inline_value=(r, c) == (row, column)
    ) for r, c in governing_cells if c < len(grid[r])):
        raise ProposalParseError(
            "proposed unit is not declared with the table as a unit of its own "
            "in a governing cell or caption"
        )
    declared_units: set[str] = set()
    for token, unit in task.source_unit_map:
        # Cell boundaries delimit tokens too. Squeezing the entire grid would
        # turn a bare unit cell into e.g. '빌릿원/톤740000' and hide it.
        for context in (caption, *(cell for row in grid for cell in row)):
            try:
                _require_declared_unit(token, context, task=task)
            except ProposalParseError:
                continue
            declared_units.add(unit)
            break
    if len(declared_units) > 1 and not unit_columns:
        raise ProposalParseError(
            f"table {proposal.table_index} for {task.metric} declares mixed units; "
            "the selected cell has no verified governing unit"
        )

    # The column's own headings are what date the figure, so chronology is
    # checked against them rather than against the whole table.
    column_text = " ".join(
        str(item[column]) for item in headers if column < len(item) and str(item[column]).strip()
    )
    validate_filing_period_context(
        column_text,
        metric=task.metric,
        effective_date=effective_date,
        require_current_period_marker=task.require_current_period_marker,
    )
    _validate_complete_reporting_period(column_text, effective_date)

    if column >= len(grid[row]):
        raise ProposalParseError(
            f"the cell proposed for {task.metric} is outside its row"
        )
    value = _amount(grid[row][column])
    if value is None:
        raise ProposalParseError(
            f"the cell proposed for {task.metric} is not a readable number: "
            f"{grid[row][column]!r}"
        )
    unit = task.unit_for(proposal.unit_token)
    return TableCellReading(
        metric=task.metric,
        member_path=proposal.member_path,
        table_index=proposal.table_index,
        row_index=row,
        column_index=column,
        row_path=proposal.row_path,
        column_path=proposal.column_path,
        value=value,
        unit=unit,
        unit_token=proposal.unit_token,
        grid_sha256=_grid_sha256(grid),
        governing_unit_cells=governing_cells,
    )


class _CoordinateTextParser(_SegmentTableParser):
    """Track original text offsets while reusing the canonical table expansion."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.cell_spans = []
        self.table_spans = []
        self._cell_start = None
        self._table_start = 0

    def _position(self):
        return len(_normalize_space(" ".join(self.parts)))

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "table":
            if self._table_depth:
                raise ProposalParseError("nested table coordinate provenance is unsupported")
            self._table_start = self._position()
        if tag.lower() in {"td", "th"} and self._row is not None:
            self._cell_start = len(self.parts)
        super().handle_starttag(tag, attrs)

    def handle_data(self, data):
        self.parts.append(data)
        super().handle_data(data)

    def handle_endtag(self, tag):
        if tag.lower() in {"td", "th"} and self._cell_start is not None:
            before = _normalize_space(" ".join(self.parts[:self._cell_start]))
            cell = _normalize_space(" ".join(self.parts[self._cell_start:]))
            start = len(before) + (1 if before and cell else 0)
            self.cell_spans.append((start, start + len(cell)))
            self._cell_start = None
        if tag.lower() == "table" and self._table_depth == 1:
            self.table_spans.append((self._table_start, self._position()))
        super().handle_endtag(tag)


def _coordinate_span(member, reading: TableCellReading) -> tuple[int, int, int, int, int, int]:
    parser = _CoordinateTextParser()
    parser.feed(member.text)
    parser.close()
    text = _visible_text(member)
    if _normalize_space(" ".join(parser.parts)) != text:
        raise ProposalParseError("coordinate text normalization differs from evidence")
    cell_number = 0
    for index, table in enumerate(parser.tables):
        numbered = []
        for row in table:
            numbered_row = []
            for _, rowspan, colspan in row:
                numbered_row.append((str(cell_number), rowspan, colspan))
                cell_number += 1
            numbered.append(numbered_row)
        if index == reading.table_index:
            grid = _expand_table(numbered)
            cell_id = int(grid[reading.row_index][reading.column_index])
            value_start, end = parser.cell_spans[cell_id]
            raw_value = text[value_start:end]
            if _amount(raw_value) != reading.value:
                raise ProposalParseError("coordinate provenance does not match verified value")
            previous_end = parser.table_spans[index - 1][1] if index else 0
            governing_spans = [
                (previous_end, parser.table_spans[index][0], True, False),
                *((*parser.cell_spans[int(grid[r][c])], False,
                   (r, c) == (reading.row_index, reading.column_index))
                  for r, c in reading.governing_unit_cells if grid[r][c]),
            ]
            # The unit must come from this table or its own caption, never a
            # preceding table. It may also be the selected cell's inline unit.
            # Match the same declared token, at token boundaries.
            units = [
                (left + start, left + stop) for left, right, is_caption, inline in governing_spans
                for start, stop in _authorized_unit_spans(
                    reading.unit_token, text[left:right], caption=is_caption, inline_value=inline
                )
            ]
            if not units:
                raise ProposalParseError("unit is not declared at the selected cell")
            unit_start, unit_end = units[-1]
            start = min(unit_start, value_start)
            # A ratio cell may carry its own trailing percent token. Keep it
            # in the evidence span/unit capture, outside the decimal capture.
            numeric_value = raw_value.rstrip()
            if reading.unit == "%":
                numeric_value = numeric_value.removesuffix("%")
            numeric_value = numeric_value.strip()
            numeric_start = value_start + raw_value.index(numeric_value)
            return (start, max(end, unit_end), numeric_start,
                    numeric_start + len(numeric_value), unit_start, unit_end)
    raise ProposalParseError("coordinate table is missing")


def evidence_span(member, reading: TableCellReading) -> str:
    """The stretch of the filing containing the verified unit and value.

    The locator path proves a number by quoting it; a table cell has to prove
    the same thing, and its unit is declared in the caption rather than beside
    the figure. The span covers both coordinates in either column order: a
    reviewer reading it sees what the number is measured in and which row it
    came from, and the machine can re-extract it from the member exactly as it
    re-extracts a quoted locator.
    """
    start, end, *_ = _coordinate_span(member, reading)
    return _visible_text(member)[start:end]


def read_table_cell_observation(
    filing: DartOriginalFilingDocument,
    proposal: TableCellProposal,
    task: TableReadingTask,
    *,
    segment: str,
    effective_date: str,
) -> DartKPIObservation:
    """Verify the coordinate, then let the ordinary extractor read the number.

    The observation is the same object the static patterns and the quoted
    locators produce, with the same receipts — member hash, normalized-text
    span, matched text — so nothing downstream has to know that a coordinate
    rather than a phrase is what found it.
    """
    member = next(
        (item for item in filing.members if item.path == proposal.member_path),
        None,
    )
    if member is None:
        raise ProposalParseError(
            f"table_cell names an unknown member: {proposal.member_path}"
        )
    reading = read_table_cell(
        member.text, proposal, task, effective_date=effective_date
    )
    start, end, value_start, value_end, unit_start, unit_end = _coordinate_span(member, reading)
    text = _visible_text(member)
    original_unit_token = text[unit_start:unit_end]
    pattern = rf"(?<=\A[\s\S]{{{start}}})"
    cursor = start
    for left, right, name in sorted(((value_start, value_end, "value"),
                                     (unit_start, unit_end, "unit"))):
        if left < cursor or right > end:
            raise ProposalParseError("verified value and unit coordinates overlap or exceed evidence")
        pattern += re.escape(text[cursor:left]) + f"(?P<{name}>{re.escape(text[left:right])})"
        cursor = right
    pattern += re.escape(text[cursor:end])

    spec = DartKPIExtractionSpec(
        metric=task.metric,
        segment=segment,
        member_path_pattern=re.escape(proposal.member_path),
        value_pattern=pattern,
        canonical_unit=task.canonical_unit,
        effective_date=effective_date,
        locator_label=(
            "LLM table cell (verified): table "
            f"{proposal.table_index} / {' / '.join(proposal.row_path)} / "
            f"{' / '.join(proposal.column_path)}"
        ),
        source_unit_map=tuple(dict(task.source_unit_map + ((original_unit_token, reading.unit),)).items()),
    )
    try:
        observation = extract_dart_kpi(filing, spec)
        receipt = reading.receipt() | {
            "version": "TABLE_CELL_RECEIPT_V1",
            "rcept_no": filing.rcept_no,
            "member_sha256": member.content_hash,
            "task_sha256": sha256(_canonical_json(asdict(task)).encode()).hexdigest(),
            "segment": segment,
            "effective_date": effective_date,
            "value": format(reading.value, "f"),
            "canonical_value": format(observation.measure.amount, "f"),
            "canonical_unit": observation.measure.unit,
            "normalized_span": [start, end],
            "normalized_text_sha256": observation.normalized_text_hash,
        }
        return replace(observation, table_cell_receipt=_canonical_json(receipt))
    except DartKPIExtractionError as error:
        raise ProposalParseError(
            f"deterministic re-extraction rejected the cell proposed for "
            f"{task.metric}: {error}"
        ) from error


ROLE_TABLE_READER = "filing_table_reader"


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def replay_table_cell_observation(
    filing: DartOriginalFilingDocument, receipt: str | Mapping[str, Any],
    task: TableReadingTask, *, segment: str, effective_date: str,
) -> DartKPIObservation:
    """Reopen a sealed coordinate without a model; reject any changed binding."""
    try:
        saved = json.loads(receipt) if isinstance(receipt, str) else dict(receipt)
        proposal = TableCellProposal.from_row({
            key: saved[key] for key in (
                "metric", "member_path", "table_index", "row_path", "column_path", "unit_token"
            )
        })
        observation = read_table_cell_observation(
            filing, proposal, task, segment=segment, effective_date=effective_date
        )
        if observation.table_cell_receipt != _canonical_json(saved):
            raise ValueError("sealed table receipt differs from replay")
        return observation
    except (ValueError, KeyError, TypeError) as error:
        raise ProposalParseError(f"EVIDENCE_RECONCILIATION_REQUIRED: {error}") from error


def _render_tables(filing: DartOriginalFilingDocument) -> str:
    """Show the model each member's tables as numbered grids with their caption.

    The grids are what the coordinates address, so the model is shown exactly
    what the verifier will read — not the raw markup, where a row and its
    heading are far apart, and not the flattened text, where a table's shape is
    lost.
    """
    blocks: list[str] = []
    for member in filing.members:
        captions = _table_captions(member.text)
        for index, grid in enumerate(_grids(member.text)):
            caption = captions[index] if index < len(captions) else ""
            rows = "\n".join(
                "  | ".join(str(cell) for cell in row) for row in grid
            )
            blocks.append(
                f"=== member: {member.path} table {index} ===\n"
                f"caption: {caption}\n{rows}"
            )
    return "\n\n".join(blocks)


def _table_prompt(
    filing: DartOriginalFilingDocument, tasks: Sequence[TableReadingTask], rendered: str
) -> str:
    task_lines = "\n".join(
        f"- {task.metric}: {task.definition}"
        f" | this table must mention some of: {', '.join(task.table_identity.must_have_any)}"
        f" | and none of: {', '.join(task.table_identity.must_not_have)}"
        f" | allowed unit tokens: {', '.join(token for token, _ in task.source_unit_map)}"
        for task in tasks
    )
    return (
        "You are a filing table reader. For each target metric, say WHICH CELL "
        "of which table holds it. You do not report numbers; you report "
        "COORDINATES. A deterministic reader opens the table at your "
        "coordinates and only what it reads there becomes evidence.\n\n"
        f"Filing {filing.rcept_no} tables:\n{rendered}\n\n"
        f"Target metrics:\n{task_lines}\n\n"
        """Return ONE JSON object:
{
 "cells": [
   {"metric": "...", "member_path": "...", "table_index": 0,
    "row_path": ["heading cells that identify the row, in order"],
    "column_path": ["heading cells that stand over the column"],
    "unit_token": "one allowed unit token, as written in the caption or the grid"}
 ],
 "not_found": ["metrics this filing does not disclose in a table"]
}
Rules enforced mechanically: the row path must fit exactly one row and the
column path exactly one column; the table must carry the metric's vocabulary
and none of its excluded vocabulary; the unit token must appear with the table;
the column must be the current period, not a prior year or a plan. Do not
guess — report a metric in not_found when the filing does not disclose it."""
    )


def propose_and_verify_table_cells(
    *,
    transport: ProposalTransport,
    filing: DartOriginalFilingDocument,
    tasks: Sequence[TableReadingTask],
    segment: str,
    effective_date: str,
    max_attempts: int = 2,
    receipts: Sequence[str | Mapping[str, Any]] = (),
) -> tuple[DartKPIObservation, ...]:
    """Ask the model which cell holds each metric; keep only what re-reads.

    Explicit not-found declarations remain unverified coverage gaps. Invalid
    proposals exhaust repair and fail closed, never masquerading as absence.
    """
    if not tasks:
        return ()
    for task in tasks:
        task.validate()
    by_metric = {task.metric: task for task in tasks}
    replayed = []
    replayed_metrics = set()
    for receipt in receipts:
        try:
            saved = json.loads(receipt) if isinstance(receipt, str) else dict(receipt)
            metric = saved["metric"]
            if metric not in by_metric or metric in replayed_metrics:
                raise ValueError("receipt names an unrequested or duplicate metric")
            replayed.append(replay_table_cell_observation(
                filing, saved, by_metric[metric], segment=segment, effective_date=effective_date
            ))
            replayed_metrics.add(metric)
        except (ValueError, TypeError, KeyError) as error:
            raise ProposalParseError(f"EVIDENCE_RECONCILIATION_REQUIRED: {error}") from error
    tasks = [task for task in tasks if task.metric not in replayed_metrics]
    if not tasks:
        return tuple(replayed)
    by_metric = {task.metric: task for task in tasks}
    rendered = _render_tables(filing)
    prompt = _table_prompt(filing, tasks, rendered)

    def parse(text: str) -> tuple[DartKPIObservation, ...]:
        payload = parse_json_object(text)
        require_keys(
            payload,
            required=("cells",),
            optional=("not_found",),
            label="table cell proposal",
        )
        rows = payload["cells"]
        if not isinstance(rows, list):
            raise ProposalParseError("cells must be a list")
        not_found = str_tuple(payload.get("not_found", []), "not_found")
        if len(set(not_found)) != len(not_found) or set(not_found) - set(by_metric):
            raise ProposalParseError("not_found must name unique requested metrics")
        observations: list[DartKPIObservation] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ProposalParseError("a table cell proposal must be an object")
            proposal = TableCellProposal.from_row(row)
            task = by_metric.get(proposal.metric)
            if task is None:
                raise ProposalParseError(
                    f"table cell names an unrequested metric: {proposal.metric}"
                )
            if proposal.metric in seen:
                raise ProposalParseError(
                    f"duplicate table cell for metric {proposal.metric}"
                )
            seen.add(proposal.metric)
            observations.append(
                read_table_cell_observation(
                    filing,
                    proposal,
                    task,
                    segment=segment,
                    effective_date=effective_date,
                )
            )
        if seen & set(not_found) or seen | set(not_found) != set(by_metric):
            raise ProposalParseError("each requested metric must be accounted for exactly once")
        return tuple(observations)

    with llm_proposal_scope():
        try:
            return tuple(replayed) + complete_with_repair(
                transport=transport,
                role=ROLE_TABLE_READER,
                prompt=prompt,
                parse=parse,
                max_attempts=max_attempts,
            )
        except ProposalParseError as error:
            raise ProposalParseError(f"PROPOSAL_REJECTED: {error}") from error
