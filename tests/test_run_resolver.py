"""The resolver reproduces the committed runs' lookups, and refuses to guess.

Every committed run directory was prepared by hand, so it doubles as the answer
key: if the resolver reads the same three public metadata payloads and does not
land on the same company, fiscal calendar, adopted report and cohort, the
resolver is wrong. The four runs cover the cases that actually bite — a name
search that also hits unlisted namesakes, a March fiscal year end, a company
that files only separate statements, and a five-year restatement that moves
which annual report a run must read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from valuation_engine.run_resolver import (
    ResolvedRun,
    RunResolverError,
    adopt_annual_report,
    read_periodic_filings,
    resolve_run,
)

ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION_MAP = ROOT / "config" / "kr_industry_classification_map.yaml"
ARCHETYPE_REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"

#: (run directory, query, ticker, files consolidated statements, method chosen)
#: 고려아연 is not here: that run binds its half-year report rather than the
#: annual, and its committed filing index is a two-row extract, so it is not an
#: answer key for a resolver that adopts the newest annual report. The
#: restatement case it does exercise is covered below from the full public
#: index it was collected from.
COMMITTED_RUNS = (
    ("kisco-104700", "한국철강", "104700", False, "commodity_price_taker/normalized_multiple"),
    ("shinhanalpha-293940", "신한알파리츠", "293940", True, "asset_yield_nav/nav"),
    ("daehansteel-084010", "대한제강", "084010", True, "commodity_price_taker/midcycle_price_volume_dcf"),
)


def _resolve(run_name: str, **overrides) -> ResolvedRun:
    raw = ROOT / "runs" / run_name / "raw"
    kwargs = dict(
        corp_search=json.loads((raw / "corp_search.json").read_text(encoding="utf-8")),
        company=json.loads((raw / "company.json").read_text(encoding="utf-8")),
        filing_index=json.loads((raw / "list.json").read_text(encoding="utf-8")),
        as_of="2026-08-29",
        classification_map_path=CLASSIFICATION_MAP,
        archetype_registry_path=ARCHETYPE_REGISTRY,
    )
    kwargs.update(overrides)
    return resolve_run(**kwargs)


@pytest.mark.parametrize(
    "run_name, query, ticker, consolidated, method", COMMITTED_RUNS
)
def test_the_resolver_reproduces_each_committed_filing_selection(
    run_name, query, ticker, consolidated, method
):
    resolved = _resolve(
        run_name,
        company_query=query,
        stock_code=ticker,
        method=method,
        consolidated=consolidated,
    )
    assert [gap.reason for gap in resolved.gaps] == []

    committed = yaml.safe_load(
        (ROOT / "runs" / run_name / "run.yaml").read_text(encoding="utf-8")
    )
    rendered = yaml.safe_load(resolved.to_run_yaml())
    assert rendered["filing"]["business_year"] == committed["filing"]["business_year"]
    assert rendered["filing"]["report_code"] == committed["filing"]["report_code"]
    assert rendered["filing"]["fs_div"] == committed["filing"]["fs_div"]
    assert (
        rendered["filing"]["fiscal_period_end"]
        == committed["filing"]["fiscal_period_end"]
    )
    assert rendered["as_of"] == committed["as_of"]
    assert rendered["scenario_ids"] == committed["scenario_ids"]


@pytest.mark.parametrize("run_name, _q, ticker, _c, _m", COMMITTED_RUNS)
def test_the_sections_source_is_the_filing_whose_raw_members_were_collected(
    run_name, _q, ticker, _c, _m
):
    """The engine reads original sections from the newest periodic filing, which
    is a half-year report while the statements bind the annual. The collected
    raw/filing_<rcept>/ directories are the record of which one that was."""
    resolved = _resolve(run_name, stock_code=ticker)
    collected = {
        path.name.removeprefix("filing_")
        for path in (ROOT / "runs" / run_name / "raw").glob("filing_*")
    }
    assert resolved.latest_periodic is not None
    assert resolved.latest_periodic.rcept_no in collected


KOREAZINC_INDEX = json.loads(
    (ROOT / "tests" / "fixtures" / "koreazinc_filing_index_2026.json").read_text(
        encoding="utf-8"
    )
)


def _resolve_koreazinc(**overrides) -> ResolvedRun:
    """고려아연 off its full public filing index, collected 2026-08-29.

    The committed run keeps a two-row extract of that index, which cannot show
    a restatement displacing three earlier filings, so the case is exercised
    from the whole index instead.
    """
    raw = ROOT / "runs" / "koreazinc-010130" / "raw"
    kwargs = dict(
        corp_search=json.loads((raw / "corp_search.json").read_text(encoding="utf-8")),
        company=json.loads((raw / "company.json").read_text(encoding="utf-8")),
        filing_index=KOREAZINC_INDEX,
        as_of="2026-08-29",
        stock_code="010130",
        classification_map_path=CLASSIFICATION_MAP,
        archetype_registry_path=ARCHETYPE_REGISTRY,
    )
    kwargs.update(overrides)
    return resolve_run(**kwargs)


def test_a_restatement_moves_which_annual_report_the_run_reads():
    """고려아연 refiled five years of reports on 2026-08-13 under a sanction. The
    run must read the correction, and must be able to say what it replaced."""
    resolved = _resolve_koreazinc()
    annual = resolved.adopted_annual
    assert annual is not None
    assert annual.rcept_no == "20260813001726"
    assert annual.is_correction is True
    assert annual.period_end.isoformat() == "2025-12-31"
    # The original March filing and both earlier corrections are named, not lost.
    assert resolved.superseded_rcept_nos == (
        "20260316000929",
        "20260325000008",
        "20260601001725",
    )


def test_a_non_december_close_dates_the_business_year_by_when_it_ends():
    resolved = _resolve("shinhanalpha-293940", stock_code="293940")
    assert resolved.fiscal_month == 3
    annual = resolved.adopted_annual
    assert annual is not None
    assert annual.period_end.isoformat() == "2026-03-31"
    assert annual.period_end.year == 2026


def test_unlisted_namesakes_do_not_make_the_company_ambiguous():
    """한국철강 also matches 한국철강산업 and 한국철강자원, neither listed."""
    resolved = _resolve("kisco-104700", stock_code="104700")
    assert [gap.reason for gap in resolved.gaps if "COMPANY" in gap.reason] == []
    assert resolved.corp_code == "00687711"


def test_two_listed_matches_are_a_gap_not_a_pick():
    resolved = _resolve(
        "kisco-104700",
        corp_search={
            "companies": [
                {"corp_code": "00000001", "corp_name": "가", "stock_code": "000001"},
                {"corp_code": "00000002", "corp_name": "나", "stock_code": "000002"},
            ]
        },
    )
    reasons = {gap.reason for gap in resolved.gaps}
    assert "AMBIGUOUS_COMPANY" in reasons
    with pytest.raises(RunResolverError, match="unresolved"):
        resolved.to_run_yaml()


def test_a_filing_published_after_the_as_of_date_is_not_visible():
    """고려아연's H1 report landed 2026-08-14; a run dated before it must not
    see it, and must fall back to the newest filing that existed."""
    resolved = _resolve_koreazinc(as_of="2026-08-01")
    assert resolved.latest_periodic is not None
    assert resolved.latest_periodic.rcept_no != "20260814003958"
    assert resolved.latest_periodic.received_on.isoformat() <= "2026-08-01"


def test_an_unmapped_ksic_is_named_not_routed():
    resolved = _resolve(
        "kisco-104700",
        stock_code="104700",
        company={
            "corp_code": "00687711",
            "corp_name": "한국철강(주)",
            "stock_code": "104700",
            "induty_code": "99999",
            "acc_mt": "12",
        },
    )
    gap = next(item for item in resolved.gaps if item.reason == "KSIC_UNMAPPED")
    assert "99999" in gap.detail
    assert resolved.archetypes == ()


def test_a_method_off_the_route_is_refused():
    resolved = _resolve(
        "shinhanalpha-293940",
        stock_code="293940",
        method="commodity_price_taker/midcycle_price_volume_dcf",
        consolidated=True,
    )
    reasons = {gap.reason for gap in resolved.gaps}
    assert "METHOD_OFF_ROUTE" in reasons


def test_an_unprobed_statement_scope_is_a_gap_because_the_profile_cannot_say_it():
    resolved = _resolve("kisco-104700", stock_code="104700", method="commodity_price_taker/normalized_multiple")
    assert "FS_SCOPE_UNPROBED" in {gap.reason for gap in resolved.gaps}


def test_a_registered_production_cohort_is_carried_into_the_declaration():
    resolved = _resolve(
        "daehansteel-084010",
        stock_code="084010",
        consolidated=True,
        method="commodity_price_taker/midcycle_price_volume_dcf",
    )
    assert resolved.calibration_cohort is not None
    assert resolved.calibration_cohort.registry_id == "kr-steel-long-continuous-v1"
    assert "kr.steel.long|5y_path|continuous_v1" in resolved.to_run_yaml()


def test_every_decision_carries_the_evidence_that_made_it():
    resolved = _resolve_koreazinc(
        consolidated=True,
        method="commodity_price_taker/midcycle_price_volume_dcf",
        company_query="고려아연",
    )
    fields = {item.field for item in resolved.decisions}
    assert {"corp_code", "fiscal_month", "archetypes", "filing", "fs_div"} <= fields
    assert all(item.basis for item in resolved.decisions)
    payload = resolved.as_dict()
    assert payload["adopted_annual"]["rcept_no"] == "20260813001726"
    assert payload["gaps"] == []


def test_periodic_reading_ignores_ad_hoc_disclosures():
    rows = [
        {"report_nm": "소송등의제기ㆍ신청(경영권분쟁소송)", "rcept_no": "20260826800491", "rcept_dt": "20260826"},
        {"report_nm": "사업보고서 (2025.12)", "rcept_no": "20260319001021", "rcept_dt": "20260319"},
        {"report_nm": "[기재정정]반기보고서 (2025.06)", "rcept_no": "20250820000108", "rcept_dt": "20250820"},
    ]
    filings = read_periodic_filings(rows, as_of=__import__("datetime").date(2026, 8, 29))
    assert [item.rcept_no for item in filings] == [
        "20260319001021",
        "20250820000108",
    ]
    assert filings[1].is_correction is True
    assert filings[0].report_code == "11011"


def test_no_annual_report_is_a_gap_rather_than_a_quarterly_substitute():
    rows = [
        {"report_nm": "분기보고서 (2026.03)", "rcept_no": "20260515002302", "rcept_dt": "20260515"},
    ]
    filings = read_periodic_filings(rows, as_of=__import__("datetime").date(2026, 8, 29))
    adopted, superseded = adopt_annual_report(filings)
    assert adopted is None and superseded == ()


def test_a_declared_sum_of_the_parts_is_not_described_as_single_segment():
    """Decomposition comes before method selection. A company-level KSIC route
    can only describe a single-segment issuer, so for a run that already
    declares reportable segments the resolver stops instead of emitting one
    method and a core segment id — which, written over the prepared
    declaration, would have lost the per-segment methods."""
    resolved = _resolve_koreazinc(
        consolidated=True,
        method="commodity_price_taker/midcycle_price_volume_dcf",
        declared_segment_ids=("smelting", "trading", "other"),
    )
    gap = next(
        item for item in resolved.gaps if item.reason == "MULTI_SEGMENT_DECLARED"
    )
    for segment in ("smelting", "trading", "other"):
        assert segment in gap.detail
    with pytest.raises(RunResolverError, match="unresolved"):
        resolved.to_run_yaml()


def test_an_undeclared_run_records_that_the_note_decides_the_structure():
    resolved = _resolve(
        "kisco-104700",
        stock_code="104700",
        consolidated=False,
        method="commodity_price_taker/normalized_multiple",
    )
    decision = next(item for item in resolved.decisions if item.field == "segments")
    assert decision.value == "undetermined"
    assert "IFRS 8" in decision.basis
    assert resolved.gaps == ()


def test_a_single_declared_segment_is_not_a_sum_of_the_parts():
    resolved = _resolve(
        "kisco-104700",
        stock_code="104700",
        consolidated=False,
        method="commodity_price_taker/normalized_multiple",
        declared_segment_ids=("core",),
    )
    assert resolved.gaps == ()
    assert "method: commodity_price_taker/normalized_multiple" in resolved.to_run_yaml()
