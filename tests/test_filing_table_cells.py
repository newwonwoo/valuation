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

import re
from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.filing_table_cells import (
    SourceRef,
    _declared_units,
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



def _ref(source: dict) -> "SourceRef":
    """A source dict as the frozen value the proposal carries."""
    return (SourceRef(cell=tuple(source["cell"])) if "cell" in source
            else SourceRef(quote=source["quote"]))


def _period_source_in(member: str, table_index: int, column_path) -> dict:
    """The header cell that dates the column, as a proposal would name it.

    The old verifier assembled this text itself; the proposal names it now, so
    the fixtures name the same cell they always relied on.
    """
    from valuation_engine.filing_table_cells import _grids, _squeeze
    wanted = _squeeze(column_path[-1] if isinstance(column_path, (list, tuple)) else column_path)
    grids = _grids(member)
    if table_index < len(grids):
        for r, row in enumerate(grids[table_index][:4]):
            for c, cell in enumerate(row):
                if _squeeze(cell) == wanted:
                    return {"cell": [table_index, r, c]}
    return {"quote": "기간 없음"}


def _unit_source_in(member: str, token: str, table_index: int = 0) -> dict:
    """Where a fixture writes its unit, as a proposal would point at it.

    A caption when the fixture has one, otherwise the cell that holds the unit.
    Fixtures that declare no unit at all get a source that is not there, so the
    tests about an undeclared unit still refuse.
    """
    from valuation_engine.filing_table_cells import _grids, _squeeze
    hits = [
        (ti, r, c)
        for ti, grid in enumerate(_grids(member))
        for r, row in enumerate(grid)
        for c, cell in enumerate(row)
        if _squeeze(cell).strip("()（）") == _squeeze(token).strip("()（）")
    ]
    if len(hits) == 1:
        return {"cell": list(hits[0])}
    # Duplicate tables repeat their captions and their unit cells, so a quote
    # cannot disambiguate; the coordinate in the selected table can.
    in_table = [hit for hit in hits if hit[0] == table_index]
    if len(in_table) == 1:
        return {"cell": list(in_table[0])}
    # A source is a whole text node, so quote the node the declaration sits in.
    for node in re.findall(r"(?<=>)([^<>]*단위[^<>]*)(?=<)", member):
        if member.count(node) == 1:
            return {"quote": re.sub(r"\s+", " ", node).strip()}
    hits = [
        (t, r, c)
        for t, grid in enumerate(_grids(member))
        for r, row in enumerate(grid)
        for c, cell in enumerate(row)
        if _squeeze(cell).strip("()（）") == _squeeze(token).strip("()（）")
    ]
    return {"cell": list(hits[0])} if len(hits) == 1 else {"quote": "(단위: 없음)"}


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
    # The proposal names where the filing writes the unit, per
    # docs/LLM_READING_HANDOFF_DESIGN.md §3.2. These fixtures declare it the
    # ordinary way, in a caption, so the default points at the declaration the
    # fixture actually carries — not at the token the proposal claims, which is
    # exactly what the mismatch tests below are about. A test about some other
    # placement names its own source.
    row.setdefault("unit_source", _unit_source_in(MEMBER, row["unit_token"], row["table_index"]))
    row.setdefault("period_source", _period_source_in(MEMBER, row["table_index"], row["column_path"]))
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


@pytest.mark.parametrize("unit", [
    "원/톤/월", "USD/원/톤", "원/톤 / 월", "USD / 원/톤", "원/톤 ／ 월",
    "(원/톤)/월", "USD/(원/톤)", "(원/톤) / 월",
    "원/톤·월", "원/톤 · 월", "원/톤 * 월", "원/톤 × 월", "원/톤 ^ 월",
    "원/톤 ∙ 월", "원/톤 ⨯ 월", "원/톤 ⚙ 월",
])
def test_compound_units_cannot_be_read_as_registered_subtokens(monkeypatch, unit):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: {unit})</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>
    <tr><td>제품</td><td>600</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="not declared"):
        _observe(row_path=["제품"], unit_token="원/톤")





def test_narrative_percentage_cannot_replace_explicit_unit_receipt(monkeypatch):
    import sys
    member = """<p>전년 대비 3% 증가한 가동률 (단위: %)</p><table>
    <tr><td>공장</td><td>2026년 반기</td></tr>
    <tr><td>제1공장</td><td>85</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    observation = _observe(metric="utilization", row_path=["제1공장"], unit_token="%")
    assert observation.measure.amount == Decimal("0.85")
    assert "3%" not in observation.matched_text


@pytest.mark.parametrize("note", ["주1) 국내 판매 기준", "주) 국내 판매 기준", "Note: domestic sales", "※ 국내 판매 기준"])
def test_complete_caption_declaration_allows_separate_note(monkeypatch, note):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><p>{note}</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>
    <tr><td>제품</td><td>600</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    assert _observe(row_path=["제품"], unit_token="원/톤").measure.amount == Decimal("600")


def test_trailing_unit_keeps_numeric_capture_on_selected_cell(monkeypatch):
    import re
    import sys
    import valuation_engine.filing_table_cells as module
    member = """<p>제품 가격변동추이</p><table>
    <tr><td>품목</td><td>2026년 반기</td><td>2025년</td><td>단위</td></tr>
    <tr><td>제품</td><td>600</td><td>1600</td><td>원/톤</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    extract = module.extract_dart_kpi
    def inspect_capture(filing, spec):
        text = module._visible_text(filing.members[0])
        match = re.search(spec.value_pattern, text)
        assert match.start("value") == text.index("600")
        assert match.start("unit") > match.end("value")
        return extract(filing, spec)
    monkeypatch.setattr(module, "extract_dart_kpi", inspect_capture)
    observation = _observe(row_path=["제품"], unit_token="원/톤")
    assert observation.measure.amount == Decimal("600")
    assert observation.matched_text.endswith("원/톤")


@pytest.mark.parametrize("note", ["수출은 USD/톤", "∙ 월 기준", "월 기준", "단위는 천원/톤"])
@pytest.mark.parametrize("row_unit", ["", "<td>원/톤</td>"])
def test_trailing_note_cannot_override_unit_declaration(monkeypatch, note, row_unit):
    import sys
    unit_header = "<td>단위</td>" if row_unit else ""
    member = f"""<p>제품 가격변동추이 (단위: 원/톤) 주1) {note}</p><table>
    <tr><td>품목</td><td>2026년 반기</td>{unit_header}</tr>
    <tr><td>수출</td><td>600</td>{row_unit}</tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="not declared|does not settle|does not govern|not a complete text"):
        _observe(row_path=["수출"], unit_token="원/톤")


@pytest.mark.parametrize("missing", ["-", "N/A", "해당없음"])
@pytest.mark.parametrize("missing_first", [True, False])
def test_unrelated_missing_value_row_does_not_block_selected_value(monkeypatch, missing, missing_first):
    import sys
    rows = [f"<tr><td>제품A</td><td>{missing}</td></tr>", "<tr><td>제품B</td><td>600</td></tr>"]
    if not missing_first:
        rows.reverse()
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>{''.join(rows)}</table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    assert _observe(row_path=["제품B"], unit_token="원/톤").measure.amount == Decimal("600")
    with pytest.raises(ProposalParseError, match="not a readable number"):
        _observe(row_path=["제품A"], unit_token="원/톤")




@pytest.mark.parametrize("value", ["600%", "600 %"])
def test_percentage_cell_cannot_be_relabelled_as_price(monkeypatch, value):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>
    <tr><td>제품</td><td>{value}</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="percentage cell conflicts"):
        _observe(row_path=["제품"], unit_token="원/톤")


@pytest.mark.parametrize("value", ["8%5", "85%%", "%85"])
def test_malformed_inline_percent_cannot_become_a_number(monkeypatch, value):
    import sys
    member = f"""<p>가동률 (단위: %)</p><table>
    <tr><td>공장</td><td>2026년 반기</td></tr>
    <tr><td>제1공장</td><td>{value}</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError):
        _observe(metric="utilization", row_path=["제1공장"], unit_token="%")


@pytest.mark.parametrize("other_first", [True, False])
@pytest.mark.parametrize("unit_in_header", [True, False])
def test_ratio_unit_cannot_come_from_other_numeric_column(monkeypatch, other_first, unit_in_header):
    import sys
    other_header = "비교지표 (%)" if unit_in_header else "비교지표"
    other_cell = "3" if unit_in_header else "3%"
    headings, cells = ([other_header, "2026년 반기"], [other_cell, "85"])
    if not other_first:
        headings.reverse()
        cells.reverse()
    member = f"""<p>가동률</p><table>
    <tr><td>공장</td><td>{headings[0]}</td><td>{headings[1]}</td></tr>
    <tr><td>제1공장</td><td>{cells[0]}</td><td>{cells[1]}</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="not declared|does not settle|does not govern|not a complete text"):
        _observe(metric="utilization", row_path=["제1공장"], unit_token="%")


@pytest.mark.parametrize("caption", ["제품 가격변동추이", "제품 가격변동추이 (단위: 원/톤)"])
@pytest.mark.parametrize("heading", ["단위", "통화단위", "Currency Unit", "Unit of measure", "임의분류", ""])
def test_unknown_row_unit_cannot_borrow_registered_unit(monkeypatch, caption, heading):
    import sys
    member = f"""<p>{caption}</p><table>
    <tr><td>품목</td><td>{heading}</td><td>2026년 반기</td></tr>
    <tr><td>국내</td><td>원/톤</td><td>740000</td></tr>
    <tr><td>수출</td><td>USD/톤</td><td>600</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="states its own unit"):
        _read(row_path=["수출"], unit_token="원/톤")


@pytest.mark.parametrize("unit", ["XYZ/unknown", "XYZ／unknown", "XYZ％"])
def test_unknown_unit_shape_rejects_without_known_unit_column(monkeypatch, unit):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>분류</td><td>2026년 반기</td></tr>
    <tr><td>수출</td><td>{unit}</td><td>600</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="states its own unit"):
        _read(row_path=["수출"], unit_token="원/톤")


@pytest.mark.parametrize("label", ["품목", "별도구분", ""])
@pytest.mark.parametrize("year", ["2025", "2026", "1999", "2025.0", "2,025"])
def test_numeric_period_sections_are_not_decimal_data(monkeypatch, label, year):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>
    <tr><td>제품A</td><td>600</td></tr>
    <tr><td>{label}</td><td>{year}</td></tr>
    <tr><td>제품B</td><td>700</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="header"):
        _read(row_path=["제품B"], unit_token="원/톤")


@pytest.mark.parametrize("first,second,target", [
    ("2025년 반기", "2026년 반기", "이전제품"),
    ("2026년 반기", "2025년 반기", "이후제품"),
])
def test_vertical_period_sections_cannot_lend_headers(monkeypatch, first, second, target):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>{first}</td></tr>
    <tr><td>이전제품</td><td>600</td></tr>
    <tr><td>품목</td><td>{second}</td></tr>
    <tr><td>이후제품</td><td>700</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="vertical header sections"):
        _read(row_path=[target], unit_token="원/톤")


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
                    "unit_source": {"quote": "(단위: 천원/톤)"},
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
    "missing",
    ("metric", "member_path", "table_index", "row_path", "column_path",
     "unit_token", "unit_source"),
)
def test_an_incomplete_proposal_is_refused(missing):
    row = {
        "metric": "realized_price",
        "member_path": "m",
        "table_index": 0,
        "row_path": ["대한제강(주)"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
        # The proposal names where the filing writes the unit, per
        # docs/LLM_READING_HANDOFF_DESIGN.md §3.2. Tests that vary the
        # unit override this alongside unit_token.
        "unit_source": {"quote": "(단위: 천원/톤)"},
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
            "unit_source": {"quote": "(단위: 천원/톤)"},
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
    # The proposal names where the filing writes the unit, per
    # docs/LLM_READING_HANDOFF_DESIGN.md §3.2. These fixtures declare it the
    # ordinary way, in a caption, so the default points at the declaration the
    # fixture actually carries — not at the token the proposal claims, which is
    # exactly what the mismatch tests below are about. A test about some other
    # placement names its own source.
    row.setdefault("unit_source", _unit_source_in(MEMBER, row["unit_token"], row["table_index"]))
    row.setdefault("period_source", _period_source_in(MEMBER, row["table_index"], row["column_path"]))
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


def test_receipt_uses_governing_caption_not_a_previous_body_row(monkeypatch):
    import sys
    member = """<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>비고</td><td>2026년 반기</td></tr>
    <tr><td>이전제품</td><td>참고 (원/톤)</td><td>600</td></tr>
    <tr><td>선택제품</td><td>공시</td><td>700</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    observation = _observe(row_path=["선택제품"], unit_token="원/톤")
    assert observation.matched_text.startswith("원/톤) 품목")
    assert observation.measure.amount == Decimal("700")


def test_receipt_replays_without_a_model_and_survives_evidence_conversion():
    import json
    from valuation_engine.filing_table_cells import replay_table_cell_observation
    from valuation_engine.dart_kpi import dart_kpi_observation_to_evidence

    original = _observe()
    replay = replay_table_cell_observation(
        _filing(), original.table_cell_receipt, TASKS["realized_price"],
        segment="steel", effective_date="2026-06-30",
    )
    assert replay == original
    receipt = json.loads(original.table_cell_receipt)
    assert receipt["cell"] == [1, 2]
    assert receipt["canonical_value"] == "823000"
    evidence = dart_kpi_observation_to_evidence(
        original, target_id="KR:084010", observed_date="2026-08-29"
    )
    assert original.table_cell_receipt in evidence.notes


@pytest.mark.parametrize("field,value", [
    ("grid_sha256", "0" * 64), ("task_sha256", "0" * 64),
    ("member_sha256", "0" * 64), ("rcept_no", "other"),
    ("segment", "other"), ("effective_date", "2025-06-30"),
    ("canonical_value", "999999"), ("cell", [1, 3]),
    ("governing_unit_cells", [[9, 9]]),
])
def test_changed_receipt_bindings_require_reconciliation(field, value):
    import json
    from valuation_engine.filing_table_cells import replay_table_cell_observation

    receipt = json.loads(_observe().table_cell_receipt)
    receipt[field] = value
    with pytest.raises(ProposalParseError, match="EVIDENCE_RECONCILIATION_REQUIRED"):
        replay_table_cell_observation(
            _filing(), receipt, TASKS["realized_price"],
            segment="steel", effective_date="2026-06-30",
        )


def test_duplicate_tables_keep_the_selected_table_offset(monkeypatch):
    import sys
    from valuation_engine.dart_kpi import _visible_text

    monkeypatch.setattr(sys.modules[__name__], "MEMBER", MEMBER + MEMBER)
    first = _observe(table_index=0)
    second = _observe(table_index=1)
    assert first.measure == second.measure
    assert second.text_start >= first.text_end
    text = _visible_text(_filing().members[0])
    assert text[second.text_start:second.text_end] == second.matched_text
    assert first.observation_hash != second.observation_hash


def test_identical_prior_and_current_values_still_select_current_column(monkeypatch):
    import sys
    from valuation_engine.dart_kpi import _visible_text

    # A repeated value in a preceding cell cannot move the selected cell's end.
    modified = MEMBER.replace("2026년 반기", "CURRENT_TEMP").replace(
        "2025년", "2026년 반기"
    ).replace("CURRENT_TEMP", "2025년").replace(
        ">793</TD>", ">823</TD>"
    )
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", modified)
    observation = _observe()
    text = _visible_text(_filing().members[0])
    assert text[observation.text_start:observation.text_end].endswith("823 823")


def test_persisted_receipt_bypasses_model_completely():
    from valuation_engine.filing_table_cells import propose_and_verify_table_cells

    class NoModel:
        def complete(self, **kwargs):
            pytest.fail("replay must not invoke the model")

    observation = _observe()
    replayed = propose_and_verify_table_cells(
        transport=NoModel(), filing=_filing(), tasks=[TASKS["realized_price"]],
        segment="steel", effective_date="2026-06-30",
        receipts=[observation.table_cell_receipt],
    )
    assert replayed == (observation,)


def test_rendering_does_not_silently_omit_rows_after_twenty(monkeypatch):
    import sys
    from valuation_engine.filing_table_cells import _render_tables

    rows = "".join(f"<tr><td>row{i}</td><td>{i}</td></tr>" for i in range(30))
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", f"<table>{rows}</table>")
    assert "row29" in _render_tables(_filing())


@pytest.mark.parametrize("unit_token", ["천원/톤", "원/톤"])
@pytest.mark.parametrize("second_unit", ["(원/톤)", "원/톤", "단위 원/톤", "단위 원 / 톤"])
def test_mixed_units_require_exact_cell_governance(monkeypatch, unit_token, second_unit):
    import sys

    mixed = """<p>제품 가격변동추이</p><table>
    <tr><td>품목</td><td>단위</td><td>2026년 반기</td></tr>
    <tr><td>철근</td><td>(천원/톤)</td><td>823</td></tr>
    <tr><td>빌릿</td><td>(원/톤)</td><td>740000</td></tr>
    </table>"""
    mixed = mixed.replace("(원/톤)", second_unit)
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", mixed)
    if unit_token == "원/톤" and second_unit in {"원/톤", "(원/톤)"}:
        assert _observe(row_path=["빌릿"], unit_token=unit_token).measure.amount == Decimal("740000")
    else:
        with pytest.raises(ProposalParseError, match="states its own unit"):
            _observe(row_path=["빌릿"], unit_token=unit_token)
    assert _observe(row_path=["철근"], unit_token="천원/톤").measure.amount == Decimal("823000")


@pytest.mark.parametrize("header", ["2026년 반기 단위당 가격", "2026년 반기 unit price"])
def test_per_unit_price_header_is_not_a_unit_column(monkeypatch, header):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>{header}</td></tr>
    <tr><td>철근</td><td>600</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    assert _observe(row_path=["철근"], column_path=[header], unit_token="원/톤").measure.amount == Decimal("600")


@pytest.mark.parametrize("unit_header", ["단위", "통화", "Unit", "Units", "Currency", "표시"])
@pytest.mark.parametrize("row_path", [["천원/톤"], ["빌릿", "천원/톤"]])
def test_unit_cells_cannot_identify_a_product_row(monkeypatch, unit_header, row_path):
    import sys
    member = f"""<p>제품 가격변동추이</p><table>
    <tr><td>품목</td><td>{unit_header}</td><td>2026년 반기</td></tr>
    <tr><td>철근</td><td>원/톤</td><td>600</td></tr>
    <tr><td>빌릿</td><td>천원/톤</td><td>823</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="no row"):
        _observe(row_path=row_path, unit_token="천원/톤")
    assert _observe(row_path=["빌릿"], unit_token="천원/톤").measure.amount == Decimal("823000")


@pytest.mark.parametrize("effective", ["2026-03-31", "2026-09-30", "2026-12-31"])
def test_half_year_header_cannot_be_sealed_as_other_same_year_period(effective):
    proposal = TableCellProposal(
        "realized_price", "member.xml", 0, ("대한제강(주)", "철 근"),
        ("2026년 반기",), "천원/톤",
        unit_source=_ref(_unit_source_in(MEMBER, "천원/톤", 0)),
        period_source=_ref(_period_source_in(MEMBER, 0, ["2026년 반기"])),
    )
    with pytest.raises(ProposalParseError, match="complete reporting period"):
        read_table_cell(MEMBER, proposal, TASKS["realized_price"], effective_date=effective)


@pytest.mark.parametrize("header,effective,accepted", [
    ("2026년 1분기", "2026-03-31", True),
    ("2026년 1분기", "2026-09-30", False),
    ("2026년 3분기", "2026-09-30", True),
    ("2026년 3분기", "2026-06-30", False),
    ("2026년", "2026-12-31", True),
    ("2026년", "2026-06-30", False),
])
def test_named_quarter_and_annual_headers_bind_full_period(header, effective, accepted):
    member = MEMBER.replace("2026년 반기", header)
    proposal = TableCellProposal(
        "realized_price", "member.xml", 0, ("대한제강(주)", "철 근"), (header,),
        "천원/톤",
        unit_source=_ref(_unit_source_in(member, "천원/톤", 0)),
        period_source=_ref(_period_source_in(member, 0, [header])),
    )
    if accepted:
        assert read_table_cell(member, proposal, TASKS["realized_price"], effective_date=effective).value == Decimal("823")
    else:
        with pytest.raises(ProposalParseError, match="complete reporting period"):
            read_table_cell(member, proposal, TASKS["realized_price"], effective_date=effective)


@pytest.mark.parametrize("marker", ["N/A", "-", "해당없음"])
def test_missing_value_marker_cannot_address_a_row(monkeypatch, marker):
    import sys
    member = f"""<p>제품 가격변동추이 (단위: 원/톤)</p><table>
    <tr><td>품목</td><td>2026년 반기</td><td>비교</td></tr>
    <tr><td>제품A</td><td>600</td><td>{marker}</td></tr></table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", member)
    with pytest.raises(ProposalParseError, match="rather than a heading"):
        _observe(row_path=[marker], unit_token="원/톤")


def test_equivalent_unit_spellings_do_not_create_false_ambiguity(monkeypatch):
    import sys

    table = """<p>제품 가격변동추이 (단위: 원/kg, 원/KG)</p><table>
    <tr><td>품목</td><td>2026년 반기</td></tr>
    <tr><td>빌릿</td><td>740</td></tr>
    </table>"""
    monkeypatch.setattr(sys.modules[__name__], "MEMBER", table)
    assert _observe(row_path=["빌릿"], unit_token="원/kg").measure.amount == Decimal("740000")



    monkeypatch.setattr(sys.modules[__name__], "MEMBER", MEMBER.replace(
        "(단위: 천원/톤)", "단위 천원 / 톤"
    ))
    assert _observe().measure.amount == Decimal("823000")


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
    # The proposal names where the filing writes the unit, and a source is a
    # whole text node — the same contract a live reader answers under.
    cell.setdefault("unit_source", _unit_source_in(MEMBER, cell["unit_token"], cell["table_index"]))
    cell.setdefault("period_source", _period_source_in(MEMBER, cell["table_index"], cell["column_path"]))
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


def test_a_prior_period_coordinate_blocks_after_repair():
    with pytest.raises(ProposalParseError, match="PROPOSAL_REJECTED"):
        _propose(_Scripted(_answer(column_path=["2025년"])))


def test_an_unrequested_metric_blocks_after_repair():
    with pytest.raises(ProposalParseError, match="PROPOSAL_REJECTED"):
        _propose(_Scripted(_answer(metric="utilization")))


def test_a_not_found_answer_is_a_gap_and_not_a_blocked_collection():
    import json

    transport = _Scripted(json.dumps({"cells": [], "not_found": ["realized_price"]}))
    assert _propose(transport) == ()


def test_an_unparseable_answer_blocks_after_repair():
    with pytest.raises(ProposalParseError, match="PROPOSAL_REJECTED"):
        _propose(_Scripted("not json at all"))


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
    with pytest.raises(ProposalParseError, match="PROPOSAL_REJECTED"):
        _propose(_Scripted(both))


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
                "unit_source": {"quote": "(단위: 천원/톤)"},
            }
        ),
        TASKS["realized_price"],
        effective_date="2026-06-30",
    )
    assert reading.value == Decimal("823")


@pytest.mark.parametrize(
    "text, declared",
    [
        ("(단위: 천원/톤)", ("천원/톤",)),
        ("(단위 : 원/Ton)", ("원/Ton",)),
        # A comma separates alternatives: one table, two units, one per column.
        ("(단위: 천톤, %)", ("천톤", "%")),
        ("(단위: 원/kg, 원/KG)", ("원/kg", "원/KG")),
        # Everything else composes. 원/톤 ⨯ 월 is one unit and it is not 원/톤,
        # whichever operator the issuer reached for — no blacklist decides this.
        ("(단위: 원/톤 ⨯ 월)", ("원/톤⨯월",)),
        ("(단위: 원/톤 ∙ 월)", ("원/톤∙월",)),
        ("(단위: USD/원/톤)", ("USD/원/톤",)),
        ("가동률 (%)", ("가동률(%", "%")),
        ("원/톤", ("원/톤",)),
    ],
)
def test_a_declaration_names_complete_units(text, declared):
    """Which units a piece of filing text declares is a unit question.

    The four tests this replaces asked the same thing through fixtures that
    presupposed where the caption was and what a note after it meant — the
    issuer-habit parsing docs/LLM_READING_HANDOFF_DESIGN.md §1.1 moves to the
    model. The proposal now names the text; all that is left here is to read
    it, and the one rule that matters is that a fragment of a compound unit is
    never a declaration: 원/톤 taken out of 천원/톤 is a thousandfold error on a
    valuation input, chosen by spelling.
    """
    assert _declared_units(text) == declared


def test_a_shorter_unit_is_not_declared_by_a_longer_one():
    assert "원/톤" not in _declared_units("(단위: 천원/톤)")
    assert "원/톤" not in _declared_units("(단위: 원/톤 ⨯ 월)")
