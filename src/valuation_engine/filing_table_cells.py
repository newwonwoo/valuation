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

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .dart_documents import DartOriginalFilingDocument
from .dart_kpi import (
    DartKPIExtractionError,
    DartKPIExtractionSpec,
    DartKPIObservation,
    extract_dart_kpi,
    _visible_text,
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
    "ratio_percent": "RATIO",
    "tons_per_year": "MASS_RATE",
    "count": "COUNT",
}


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _amount(cell: str) -> Decimal | None:
    """Parse a filed money or ratio cell; ``(x)`` and ``△x`` are negative."""
    text = _squeeze(cell).replace(",", "").replace("%", "")
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
        }


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


def _table_captions(html_text: str) -> list[str]:
    """The text standing immediately above each table, in document order."""
    parts = re.split(r"<\s*table", str(html_text or ""), flags=re.I)
    captions: list[str] = []
    for preceding in parts[:-1]:
        text = _TAG.sub(" ", preceding)
        text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
        captions.append(re.sub(r"\s+", " ", text)[-_CAPTION_WINDOW:].strip())
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
    width = max((len(row) for row in grid), default=0)
    candidates = []
    for column in range(width):
        stack = [
            _squeeze(row[column])
            for row in grid
            if column < len(row) and str(row[column]).strip()
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


def _locate_row(
    grid: Sequence[Sequence[str]], row_path: Sequence[str], *, metric: str
) -> int:
    candidates = []
    for index, row in enumerate(grid):
        labels = [_squeeze(cell) for cell in row if str(cell).strip()]
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

    # The unit has to be present where the table is, not merely registered:
    # otherwise a model could attach any registered token to any number.
    grid_text = " ".join(str(cell) for row in grid for cell in row)
    if _squeeze(proposal.unit_token) not in _squeeze(caption + " " + grid_text):
        raise ProposalParseError(
            f"the unit {proposal.unit_token!r} proposed for {task.metric} does "
            "not appear with the table; a unit has to be read from the filing, "
            "not chosen from the registry"
        )

    column = _locate_column(grid, proposal.column_path, metric=task.metric)
    row = _locate_row(grid, proposal.row_path, metric=task.metric)

    # The column's own headings are what date the figure, so chronology is
    # checked against them rather than against the whole table.
    column_text = " ".join(
        str(item[column]) for item in grid if column < len(item) and str(item[column]).strip()
    )
    validate_filing_period_context(
        column_text,
        metric=task.metric,
        effective_date=effective_date,
        require_current_period_marker=task.require_current_period_marker,
    )

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
    )


def _ordered_span(text: str, needles: Sequence[str]) -> tuple[int, int]:
    """Where the needles occur in order, as the earliest such run."""
    cursor = 0
    first = -1
    for needle in needles:
        found = text.find(needle, cursor)
        if found < 0:
            raise ProposalParseError(
                f"the filing text does not carry {needle!r} after the previous "
                "heading; the proposed coordinate is not readable as one span"
            )
        if first < 0:
            first = found
        cursor = found + len(needle)
    return first, cursor


def evidence_span(member, reading: TableCellReading) -> str:
    """The stretch of the filing that carries the unit and then the cell.

    The locator path proves a number by quoting it; a table cell has to prove
    the same thing, and its unit is declared in the caption rather than beside
    the figure. So the span runs from that declaration through the cell: a
    reviewer reading it sees what the number is measured in and which row it
    came from, and the machine can re-extract it from the member exactly as it
    re-extracts a quoted locator.
    """
    text = _visible_text(member)
    labels = [label for label in reading.row_path if label.strip()]
    value_text = _format_cell_value(reading.value)
    _, end = _ordered_span(text, labels + [value_text])
    label_start, _ = _ordered_span(text, labels[:1] or [value_text])
    unit_at = text.rfind(reading.unit_token, 0, label_start)
    if unit_at < 0:
        raise ProposalParseError(
            f"the unit {reading.unit_token!r} is not declared before the cell "
            f"proposed for {reading.metric}; a unit that follows its figure "
            "cannot be shown to govern it"
        )
    span = text[unit_at:end]
    occurrences = text.count(span)
    if occurrences != 1:
        raise ProposalParseError(
            f"the span carrying the cell proposed for {reading.metric} occurs "
            f"{occurrences} times in the member; the coordinate does not name "
            "one place in the filing"
        )
    return span


def _format_cell_value(value: Decimal) -> str:
    """Render the value the way the filing writes it, with thousands commas."""
    text = format(value, "f")
    if "." in text:
        whole, _, fraction = text.partition(".")
        return f"{int(whole):,}.{fraction}"
    return f"{int(text):,}"


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
    span = evidence_span(member, reading)
    value_text = _format_cell_value(reading.value)

    escaped = re.escape(span)
    escaped_value = re.escape(value_text)
    escaped_unit = re.escape(reading.unit_token)
    head, _, tail = escaped.rpartition(escaped_value)
    pattern = head + f"(?P<value>{escaped_value})" + tail
    if escaped_unit not in pattern:  # pragma: no cover - span starts at the unit
        raise ProposalParseError(
            f"the span for {task.metric} lost its unit token while compiling"
        )
    pattern = pattern.replace(escaped_unit, f"(?P<unit>{escaped_unit})", 1)

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
        source_unit_map=task.source_unit_map,
    )
    try:
        return extract_dart_kpi(filing, spec)
    except DartKPIExtractionError as error:
        raise ProposalParseError(
            f"deterministic re-extraction rejected the cell proposed for "
            f"{task.metric}: {error}"
        ) from error


ROLE_TABLE_READER = "filing_table_reader"


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
                "  | ".join(str(cell) for cell in row) for row in grid[:20]
            )
            blocks.append(
                f"=== member: {member.path} table {index} ===\n"
                f"caption: {caption[-300:]}\n{rows}"
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
) -> tuple[DartKPIObservation, ...]:
    """Ask the model which cell holds each metric; keep only what re-reads.

    A rejected coordinate produces no observation, exactly as an undisclosed
    metric produces none: both surface downstream as a named coverage gap, and
    neither blocks the run. A model that cannot point at a verifiable cell has
    lost the round, not the collection.
    """
    if not tasks:
        return ()
    for task in tasks:
        task.validate()
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
        str_tuple(payload.get("not_found", []), "not_found")
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
        return tuple(observations)

    with llm_proposal_scope():
        try:
            return complete_with_repair(
                transport=transport,
                role=ROLE_TABLE_READER,
                prompt=prompt,
                parse=parse,
                max_attempts=max_attempts,
            )
        except ProposalParseError:
            # Unreadable and undisclosed end the same way: a named gap, not a
            # blocked run.
            return ()
