"""Sections are chosen by the role they play, and a missing role is named.

The two 고려아연 filings committed in runs/ are the fixture that matters: the
same company's half-year and annual reports number their sections differently,
so a plan that survives both is a plan that survives renumbering. The five
sections that were hand-picked when the run was prepared must still be selected,
and the roles that hand-picking missed must be found too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuation_engine.filing_collection_plan import (
    FilingCollectionError,
    SectionRole,
    build_raw_manifest,
    load_section_roles,
    normalize_heading,
    parse_toc,
    parse_viewer_toc,
    plan_sections,
    render_toc,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
#: The contents trees of 고려아연's 2026 half-year report and of the 2026-08-13
#: restatement of its FY2025 annual — the same company, two filings that number
#: their sections differently.
HALF_YEAR = FIXTURES / "koreazinc_h1_2026_filing_toc.txt"
ANNUAL = FIXTURES / "koreazinc_annual_2025_filing_toc.txt"


def _plan(toc_path: Path):
    entries = parse_toc(toc_path.read_text(encoding="utf-8"))
    return entries, plan_sections(entries, load_section_roles())


@pytest.mark.parametrize("toc_path", (HALF_YEAR, ANNUAL), ids=("half-year", "annual"))
def test_every_declared_role_is_served_by_a_real_filing(toc_path):
    entries, plan = _plan(toc_path)
    assert len(entries) > 100
    assert plan.unmatched == ()
    assert plan.missing_required == ()


def test_the_same_note_keeps_its_role_when_the_filing_renumbers_it():
    """고려아연's operating segment note is item 31 of the half-year report and
    item 39 of the annual — different element ids, one role."""
    _entries, half = _plan(HALF_YEAR)
    _entries, annual = _plan(ANNUAL)
    half_ids = dict(half.selected)["segment_note"]
    annual_ids = dict(annual.selected)["segment_note"]
    assert [entry.ele_id for entry in half_ids] == ["58"]
    assert [entry.ele_id for entry in annual_ids] == ["64"]
    assert half_ids[0].heading == annual_ids[0].heading == "연결대상회사의 부문별 정보 (연결)"


@pytest.mark.parametrize(
    "toc_path, expected",
    (
        (HALF_YEAR, {"7", "11", "12", "58", "101"}),
        (ANNUAL, {"9", "13", "14", "64", "111"}),
    ),
    ids=("half-year", "annual"),
)
def test_the_plan_covers_every_section_a_prepared_run_collected_by_hand(
    toc_path, expected
):
    """Those element ids are the sections an operator picked out of these two
    filings by reading the tree; the plan has to find all of them."""
    _entries, plan = _plan(toc_path)
    planned = {entry.ele_id for entry in plan.entries}
    assert expected <= planned
    # And it finds more than the hand hunt did — that is the point of the plan.
    assert len(planned) > len(expected)


def test_a_planned_member_is_named_the_way_a_run_directory_stores_it():
    _entries, plan = _plan(HALF_YEAR)
    names = {entry.member_name("20260814003958") for entry in plan.entries}
    assert "20260814003958_58.xml" in names
    assert all(name.startswith("20260814003958_") for name in names)


def test_numbering_is_stripped_before_a_heading_is_matched():
    assert normalize_heading("31. 연결대상회사의 부문별 정보 (연결)") == (
        "연결대상회사의 부문별 정보 (연결)"
    )
    assert normalize_heading("2-1. 연결 재무상태표") == "연결 재무상태표"
    assert normalize_heading("III. 재무에 관한 사항") == "재무에 관한 사항"
    assert normalize_heading("  4.  주식의   총수 등 ") == "주식의 총수 등"


def test_an_unmatched_required_role_is_reported_not_skipped():
    entries = parse_toc("1\t100\t0\t10\tdart4.xsd\t1. 회사의 개요\n")
    plan = plan_sections(
        entries,
        (
            SectionRole("share_count", ("주식의 총수",), True),
            SectionRole("dividends", ("배당에 관한 사항",), False),
        ),
    )
    assert plan.missing_required == ("share_count",)
    assert set(plan.unmatched) == {"share_count", "dividends"}
    assert plan.entries == ()


def test_a_role_that_several_headings_serve_keeps_all_of_them():
    """차입금 detail is split across the consolidated and separate notes; a plan
    that kept only the first would collect half the bridge."""
    _entries, plan = _plan(HALF_YEAR)
    borrowings = dict(plan.selected)["borrowings_note"]
    assert len(borrowings) > 1


def test_the_viewer_tree_round_trips_through_the_stored_table_of_contents():
    """A filing's notes live at the third tree depth, so a reader fixed to the
    first two depths loses every note — 고려아연's half-year report puts 78 of
    its 135 sections there. The depth is captured and back-referenced so fields
    pair only within one node."""
    page = """
      node1['text'] = "II. 사업의 내용";
      node1['dcmNo'] = "11539212";
      node1['eleId'] = "9";
      node1['offset'] = "155272";
      node1['length'] = "490365";
      node1['dtd'] = "dart4.xsd";
      node2['text'] = "2. 주요 제품 및  서비스";
      node2['dcmNo'] = "11539212";
      node2['eleId'] = "11";
      node2['offset'] = "156488";
      node2['length'] = "60237";
      node2['dtd'] = "dart4.xsd";
      node3['text'] = "31. 연결대상회사의 부문별 정보 (연결)";
      node3['dcmNo'] = "11539212";
      node3['eleId'] = "58";
      node3['offset'] = "2147237";
      node3['length'] = "66678";
    """
    entries = parse_viewer_toc(page)
    assert [entry.ele_id for entry in entries] == ["9", "11", "58"]
    assert entries[1].title == "2. 주요 제품 및 서비스"
    # The note carries no dtd of its own, so the documented default stands.
    assert entries[2].dtd == "dart4.xsd"
    assert entries[2].heading == "연결대상회사의 부문별 정보 (연결)"
    assert parse_toc(render_toc(entries)) == entries
    assert entries[1].viewer_url("20260814003958") == (
        "https://dart.fss.or.kr/report/viewer.do?rcpNo=20260814003958"
        "&dcmNo=11539212&eleId=11&offset=156488&length=60237&dtd=dart4.xsd"
    )


def test_a_section_a_deeper_node_repeats_is_kept_once():
    page = """
      node2['text'] = "4. 주식의 총수 등";
      node2['dcmNo'] = "1"; node2['eleId'] = "7";
      node2['offset'] = "10"; node2['length'] = "20";
      node3['text'] = "4. 주식의 총수 등";
      node3['dcmNo'] = "1"; node3['eleId'] = "7";
      node3['offset'] = "10"; node3['length'] = "20";
    """
    assert [entry.ele_id for entry in parse_viewer_toc(page)] == ["7"]


def test_a_page_without_a_contents_tree_is_refused():
    with pytest.raises(FilingCollectionError, match="no contents tree"):
        parse_viewer_toc("<html><body>not a filing</body></html>")


def test_a_malformed_table_of_contents_line_is_refused():
    with pytest.raises(FilingCollectionError, match="expected ele_id"):
        parse_toc("7\t11539212\t57004\n")
    with pytest.raises(FilingCollectionError, match="non-numeric"):
        parse_toc("7\t11539212\tx\t10\tdart4.xsd\t4. 주식의 총수 등\n")


def test_the_manifest_hashes_what_was_collected_and_flags_a_truncated_read(tmp_path):
    """The hash makes a re-collection provable, and the truncation flag lets a
    later read name TRUNCATED instead of reporting the section as absent."""
    filing = tmp_path / "filing_20260814003958"
    filing.mkdir()
    short = filing / "20260814003958_7.xml"
    short.write_text("<table>주식의 총수</table>", encoding="utf-8")
    long = filing / "20260814003958_58.xml"
    long.write_text("<table>" + "부문 " * 6000 + "</table>", encoding="utf-8")
    (filing / "toc.txt").write_text(
        HALF_YEAR.read_text(encoding="utf-8"), encoding="utf-8"
    )

    manifest = build_raw_manifest(tmp_path)
    by_path = {row["path"]: row for row in manifest["files"]}

    note = by_path["filing_20260814003958/20260814003958_58.xml"]
    assert len(note["sha256"]) == 64
    assert note["bytes"] > 0
    assert note["characters"] > manifest["member_text_limit"]
    assert note["truncated_for_reader"] is True

    table = by_path["filing_20260814003958/20260814003958_7.xml"]
    assert table["truncated_for_reader"] is False
    # The table of contents is recorded too, but it is not an XML member, so it
    # carries no reader window.
    assert "truncated_for_reader" not in by_path["filing_20260814003958/toc.txt"]
    assert "manifest.json" not in by_path


def test_the_manifest_refuses_a_directory_that_was_never_collected(tmp_path):
    with pytest.raises(FilingCollectionError, match="no raw directory"):
        build_raw_manifest(tmp_path / "absent")
