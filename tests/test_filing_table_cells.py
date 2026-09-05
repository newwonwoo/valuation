"""A metric is found by its table's identity, not by an anchor list that grows.

config/kr_filing_kpi_patterns.yaml shows the cost of the anchor approach in its
own comments: realized_price reached five anchor terms across three issuers,
each addition annotated with the laundering it might open. The list has to grow
with every new company, and every growth widens what may be pointed at.

The fixture here is the real thing that list was grown for — 대한제강's H1 2026
product price table, whose heading is "제품별 구체적인 가격변동추이" and whose
unit "(단위: 천원/톤)" sits above the markup rather than inside it. 철근 at 823
천원/톤 is the disclosed figure.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.filing_table_cells import (
    TableCellProposal,
    _is_label,
    read_table_cell_observation,
    TableIdentity,
    TableReadingTask,
    _table_captions,
    load_table_reading_tasks,
    read_table_cell,
)
from valuation_engine.proposal_parsing import ProposalParseError

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "daehan_product_price_table_h1_2026.xml"
)
MEMBER = FIXTURE.read_text(encoding="utf-8")
TASKS = load_table_reading_tasks()


def _read(**overrides):
    row = {
        "metric": "realized_price",
        "member_path": "20260814003201_11.xml",
        "table_index": 0,
        "row_path": ["대한제강(주)", "철 근"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
    }
    row.update(overrides)
    return read_table_cell(
        MEMBER,
        TableCellProposal.from_row(row),
        TASKS[row["metric"]],
        effective_date="2026-06-30",
    )


def test_the_disclosed_cell_is_read_from_the_grid():
    reading = _read()
    assert reading.value == Decimal("823")
    assert reading.unit == "KRW_thousand_per_ton"
    assert reading.row_path == ("대한제강(주)", "철 근")
    assert reading.column_path == ("2026년 반기",)


def test_a_second_row_of_the_same_table_reads_its_own_cell():
    assert _read(row_path=["대한제강(주)", "빌 릿"]).value == Decimal("740")
    assert _read(row_path=["와이케이스틸(주)", "철 근"]).value == Decimal("807")


def test_the_receipt_lets_a_reviewer_reopen_the_cell():
    receipt = _read().receipt()
    assert receipt["cell"] == [1, 2]
    assert receipt["row_path"] == ["대한제강(주)", "철 근"]
    assert len(receipt["grid_sha256"]) == 64
    assert receipt["unit_token"] == "천원/톤"


def test_a_prior_period_column_is_refused():
    """The number is real and sits in the same table; it is last year's."""
    with pytest.raises(ProposalParseError, match="current-period marker"):
        _read(column_path=["2025년"])


def test_a_row_path_that_fits_several_rows_is_refused_not_guessed():
    with pytest.raises(ProposalParseError, match="fit 3 rows"):
        _read(row_path=["대한제강(주)"])


def test_a_row_path_that_fits_nothing_is_refused():
    with pytest.raises(ProposalParseError, match="no row in the table"):
        _read(row_path=["없는회사", "철 근"])


def test_a_column_path_that_fits_nothing_is_refused():
    with pytest.raises(ProposalParseError, match="no column in the table"):
        _read(column_path=["2099년"])


def test_a_unit_not_present_at_the_table_is_refused():
    """Registered is not enough: the unit has to be read from the filing.

    원/kg is registered for this metric and simply is not what this table
    declares."""
    with pytest.raises(ProposalParseError, match="not declared with the table"):
        _read(unit_token="원/kg")


def test_a_unit_the_task_does_not_register_is_refused():
    """A price per tonne cannot be reported in percent, however the table is
    worded. The registry check is separate from the presence check, so it is
    exercised directly."""
    with pytest.raises(ProposalParseError, match="does not register unit token"):
        TASKS["realized_price"].unit_for("%")


def test_the_raw_material_task_may_not_read_the_product_table():
    """The exclusion list is the decisive half: the two tables sit beside each
    other and read almost identically, and this is what stops a purchase price
    entering as a selling price."""
    with pytest.raises(ProposalParseError, match="that is a different table"):
        read_table_cell(
            MEMBER,
            TableCellProposal.from_row(
                {
                    "metric": "input_price",
                    "member_path": "m",
                    "table_index": 0,
                    "row_path": ["대한제강(주)", "철 근"],
                    "column_path": ["2026년 반기"],
                    "unit_token": "천원/톤",
                }
            ),
            TASKS["input_price"],
            effective_date="2026-06-30",
        )


def test_a_table_index_outside_the_member_is_refused():
    with pytest.raises(ProposalParseError, match="but the member holds"):
        _read(table_index=9)


def test_the_caption_above_the_markup_is_what_names_the_table():
    """The heading and the unit are outside the table element; identity read
    from the grid alone would refuse the very table it is looking for."""
    caption = _table_captions(MEMBER)[0]
    assert "제품별 구체적인 가격변동추이" in caption
    assert "천원/톤" in caption


def test_a_task_may_not_mix_unit_dimensions():
    task = TableReadingTask(
        metric="realized_price",
        definition="d",
        canonical_unit="KRW_per_ton",
        unit_dimension="PRICE_PER_MASS",
        table_identity=TableIdentity(("판매",), ()),
        # A real, registered unit — of the wrong dimension for this metric.
        source_unit_map=(("%", "%"),),
    )
    with pytest.raises(ProposalParseError, match="cannot be converted"):
        task.validate()


def test_a_task_may_not_name_a_unit_the_registry_does_not_know():
    task = TableReadingTask(
        metric="utilization",
        definition="d",
        canonical_unit="%",
        unit_dimension="RATIO",
        table_identity=TableIdentity(("가동률",), ()),
        source_unit_map=(("%", "ratio_percent"),),
    )
    with pytest.raises(ProposalParseError, match="unregistered unit"):
        task.validate()


def test_the_committed_registry_is_coherent():
    assert set(TASKS) >= {"realized_price", "input_price", "utilization"}
    for task in TASKS.values():
        task.validate()
        assert task.table_identity.must_not_have, (
            f"{task.metric} has no exclusion vocabulary; the exclusion half is "
            "what keeps a neighbouring table from being read as this one"
        )


@pytest.mark.parametrize(
    "missing", ("metric", "member_path", "table_index", "row_path", "column_path", "unit_token")
)
def test_an_incomplete_proposal_is_refused(missing):
    row = {
        "metric": "realized_price",
        "member_path": "m",
        "table_index": 0,
        "row_path": ["대한제강(주)"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
    }
    row.pop(missing)
    with pytest.raises(ProposalParseError):
        TableCellProposal.from_row(row)


def test_a_single_heading_may_be_given_as_a_string():
    assert TableCellProposal.from_row(
        {
            "metric": "realized_price",
            "member_path": "m",
            "table_index": 0,
            "row_path": "철 근",
            "column_path": "2026년 반기",
            "unit_token": "천원/톤",
        }
    ).row_path == ("철 근",)


def _filing():
    """The fixture as an original filing document, the way a run holds one."""
    from datetime import date
    from io import BytesIO
    from zipfile import ZipFile

    from valuation_engine.dart_documents import (
        parse_opendart_original_document_archive,
    )

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"{RCEPT}.xml", MEMBER)
    return parse_opendart_original_document_archive(
        buffer.getvalue(),
        rcept_no=RCEPT,
        checked_at=date(2026, 8, 29),
        source_ref=(
            "https://opendart.fss.or.kr/api/document.xml?rcept_no=" + RCEPT
        ),
    )


RCEPT = "20260814003201"


def _observe(**overrides):
    row = {
        "metric": "realized_price",
        "member_path": f"{RCEPT}.xml",
        "table_index": 0,
        "row_path": ["대한제강(주)", "철 근"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
    }
    row.update(overrides)
    return read_table_cell_observation(
        _filing(),
        TableCellProposal.from_row(row),
        TASKS[row["metric"]],
        segment="steel",
        effective_date="2026-06-30",
    )


def test_a_verified_cell_becomes_an_ordinary_observation():
    """Nothing downstream should have to know that a coordinate rather than a
    phrase is what found the number."""
    observation = _observe()
    assert observation.measure.amount == Decimal("823000")
    assert observation.measure.unit == "KRW_per_ton"
    assert observation.source_unit_token == "천원/톤"
    assert observation.source_unit == "KRW_thousand_per_ton"
    assert observation.metric == "realized_price"
    assert observation.segment == "steel"


def test_the_receipts_are_the_same_ones_the_static_path_leaves():
    observation = _observe()
    assert observation.rcept_no == RCEPT
    assert len(observation.member_content_hash) == 64
    assert observation.text_end > observation.text_start >= 0
    assert observation.matched_text.endswith("823")


def test_the_quoted_span_carries_the_unit_that_governs_the_figure():
    """A table declares its unit in the caption, so the span has to run from
    that declaration through the cell — otherwise the receipt shows a bare
    number and a reviewer cannot tell what it is measured in."""
    matched = _observe().matched_text
    assert "천원/톤" in matched
    assert matched.index("천원/톤") < matched.index("823")


def test_the_span_names_the_row_it_came_from():
    matched = _observe().matched_text
    assert "대한제강(주)" in matched and "철 근" in matched


def test_a_second_row_reads_and_re_extracts_its_own_figure():
    assert _observe(row_path=["와이케이스틸(주)", "철 근"]).measure.amount == Decimal(
        "807000"
    )


def test_an_unknown_member_is_refused():
    with pytest.raises(ProposalParseError, match="unknown member"):
        _observe(member_path="absent.xml")


def test_the_prior_period_column_is_still_refused_on_the_full_path():
    with pytest.raises(ProposalParseError, match="current-period marker"):
        _observe(column_path=["2025년"])


class _Scripted:
    """A transport whose answers are written in advance, like a run's staff file."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, *, role: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]


def _answer(**overrides) -> str:
    import json

    cell = {
        "metric": "realized_price",
        "member_path": f"{RCEPT}.xml",
        "table_index": 0,
        "row_path": ["대한제강(주)", "철 근"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
    }
    cell.update(overrides)
    return json.dumps({"cells": [cell], "not_found": []}, ensure_ascii=False)


def _propose(transport):
    from valuation_engine.filing_table_cells import propose_and_verify_table_cells

    return propose_and_verify_table_cells(
        transport=transport,
        filing=_filing(),
        tasks=[TASKS["realized_price"]],
        segment="steel",
        effective_date="2026-06-30",
    )


def test_a_verifiable_coordinate_becomes_evidence():
    observations = _propose(_Scripted(_answer()))
    assert [item.metric for item in observations] == ["realized_price"]
    assert observations[0].measure.amount == Decimal("823000")


def test_the_model_is_shown_the_grid_it_must_address():
    """Coordinates are only answerable against the same grid the verifier
    reads, so the prompt carries the expanded table and its caption."""
    transport = _Scripted(_answer())
    _propose(transport)
    prompt = transport.prompts[0]
    assert "2026년 반기" in prompt
    assert "천원/톤" in prompt
    assert "대한제강(주)" in prompt


def test_a_prior_period_coordinate_yields_a_gap_not_a_number():
    assert _propose(_Scripted(_answer(column_path=["2025년"]))) == ()


def test_an_unrequested_metric_yields_a_gap():
    assert _propose(_Scripted(_answer(metric="utilization"))) == ()


def test_a_not_found_answer_is_a_gap_and_not_a_blocked_collection():
    import json

    transport = _Scripted(json.dumps({"cells": [], "not_found": ["realized_price"]}))
    assert _propose(transport) == ()


def test_an_unparseable_answer_leaves_a_gap_rather_than_raising():
    assert _propose(_Scripted("not json at all")) == ()


def test_two_cells_for_one_metric_are_refused():
    import json

    both = json.dumps(
        {
            "cells": [
                {
                    "metric": "realized_price",
                    "member_path": f"{RCEPT}.xml",
                    "table_index": 0,
                    "row_path": ["대한제강(주)", "철 근"],
                    "column_path": ["2026년 반기"],
                    "unit_token": "천원/톤",
                },
                {
                    "metric": "realized_price",
                    "member_path": f"{RCEPT}.xml",
                    "table_index": 0,
                    "row_path": ["대한제강(주)", "빌 릿"],
                    "column_path": ["2026년 반기"],
                    "unit_token": "천원/톤",
                },
            ]
        },
        ensure_ascii=False,
    )
    assert _propose(_Scripted(both)) == ()


def test_a_shorter_unit_inside_the_declared_one_is_refused():
    """천원/톤 contains 원/톤. A substring test would let the model read a table
    declared in thousands as though it were in won — a factor of a thousand on
    a valuation input, chosen by spelling."""
    with pytest.raises(ProposalParseError, match="as a unit of its own"):
        _read(unit_token="원/톤")


def test_the_declared_unit_still_reads():
    assert _read().unit == "KRW_thousand_per_ton"


@pytest.mark.parametrize("path_kind", ("row_path", "column_path"))
def test_a_figure_may_not_be_used_as_a_heading(path_kind):
    """The prompt shows every number in the table, so a path made of figures
    would let the model pick any cell and relabel it as this metric."""
    with pytest.raises(ProposalParseError, match="figure rather than a heading"):
        _read(**{path_kind: ["823"]})


def test_a_figure_shaped_cell_is_not_treated_as_a_label():
    assert _is_label("철 근") is True
    assert _is_label("823") is False
    assert _is_label("99.23%") is False
    assert _is_label("(13,599,031)") is False
    assert _is_label("") is False


def test_every_task_declares_a_unit_the_measure_registry_knows():
    """A task mapping to a unit the registry does not know could never produce
    evidence: re-extraction would fail and read as a gap, silently."""
    from decimal import Decimal as _D

    from valuation_engine.actual_units import Measure

    for task in TASKS.values():
        Measure(_D("1"), task.canonical_unit, "2026-06-30")
        for _token, unit in task.source_unit_map:
            Measure(_D("1"), unit, "2026-06-30")


def test_a_caption_stops_at_the_previous_table():
    """Splitting on opening tags alone puts the whole previous table into the
    next one's caption, so a neighbouring grid could lend vocabulary to
    validate the wrong table, or a term inside it could reject the right one."""
    body = (
        "<p>원재료 매입 현황 (단위: 원/kg)</p>"
        "<table><tr><td>구분</td><td>값</td></tr><tr><td>고철</td><td>500</td></tr></table>"
        "<p>제품별 가격변동추이 (단위: 천원/톤)</p>"
        "<table><tr><td>품 목</td><td>2026년 반기</td></tr>"
        "<tr><td>철 근</td><td>823</td></tr></table>"
    )
    captions = _table_captions(body)
    assert len(captions) == 2
    assert "원재료" in captions[0] and "천원/톤" not in captions[0]
    # The second caption must not carry the first table's vocabulary, which
    # would otherwise reject this table under the metric's exclusion list.
    assert "원재료" not in captions[1]
    assert "고철" not in captions[1]
    assert "천원/톤" in captions[1]


def test_the_metric_reads_from_a_table_whose_neighbour_is_the_excluded_one():
    """The exact shape the caption bug would have broken: the raw-material
    table sits right above the product table."""
    body = (
        "<p>원재료 매입 현황 (단위: 원/kg)</p>"
        "<table><tr><td>구분</td><td>2026년 반기</td></tr>"
        "<tr><td>고 철</td><td>500</td></tr></table>"
        "<p>제품별 가격변동추이 (단위: 천원/톤)</p>"
        "<table><tr><td>품 목</td><td>2026년 반기</td></tr>"
        "<tr><td>철 근</td><td>823</td></tr></table>"
    )
    from valuation_engine.filing_table_cells import read_table_cell

    reading = read_table_cell(
        body,
        TableCellProposal.from_row(
            {
                "metric": "realized_price",
                "member_path": "m",
                "table_index": 1,
                "row_path": ["철 근"],
                "column_path": ["2026년 반기"],
                "unit_token": "천원/톤",
            }
        ),
        TASKS["realized_price"],
        effective_date="2026-06-30",
    )
    assert reading.value == Decimal("823")
