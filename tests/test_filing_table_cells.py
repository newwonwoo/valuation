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
    """Registered is not enough: the unit has to be read from the filing."""
    with pytest.raises(ProposalParseError, match="does not appear with the table"):
        _read(unit_token="원/kg")


def test_a_unit_present_in_the_filing_but_unregistered_is_refused():
    """'%' is right there in the section, and is still not a price per tonne."""
    with pytest.raises(ProposalParseError, match="does not register unit token"):
        _read(unit_token="%")


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
        source_unit_map=(("%", "ratio_percent"),),
    )
    with pytest.raises(ProposalParseError, match="cannot be converted"):
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
