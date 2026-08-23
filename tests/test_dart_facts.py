import pytest

from valuation_engine.dart_facts import (
    DartAmountBasis,
    DartFactMetricSpec,
    build_opendart_full_financials_url,
    live_opendart_fact_collector,
    opendart_fact_collector,
    parse_opendart_financial_facts,
)
from valuation_engine.evidence_collection import collect_primary_evidence


FISCAL_PERIOD_END = "2026-09-30"


def row(account_id: str, amount: str, *, sj_div: str = "IS", fs_div: str | None = "CFS", **extra) -> dict:
    result = {
        "rcept_no": "20261114000123",
        "corp_code": "00126380",
        "reprt_code": "11014",
        "bsns_year": "2026",
        "sj_div": sj_div,
        "account_id": account_id,
        "account_nm": account_id,
        "thstrm_amount": amount,
        "currency": "KRW",
    }
    if sj_div in {"IS", "CIS"}:
        result["thstrm_add_amount"] = amount
    if fs_div is not None:
        result["fs_div"] = fs_div
    result.update(extra)
    return result


def test_core_dart_facts_preserve_large_integer_precision():
    records = parse_opendart_financial_facts(
        (
            row("ifrs-full_Revenue", "12,345,678,901,234,567"),
            row("dart_OperatingIncomeLoss", "1,234,567,890,123"),
        ),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=FISCAL_PERIOD_END,
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
    )
    by_metric = {item.metric: item for item in records}
    assert by_metric["revenue"].value == 12345678901234567
    assert isinstance(by_metric["revenue"].value, int)
    assert by_metric["revenue"].effective_date == "2026-09-30"
    assert by_metric["revenue"].critical


def test_interim_flow_uses_cumulative_amount_when_available():
    records = parse_opendart_financial_facts(
        (row("ifrs-full_Revenue", "300", fs_div=None, thstrm_add_amount="900"),),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=FISCAL_PERIOD_END,
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        fs_div="CFS",
    )
    assert records[0].value == 900
    assert "amount_field=thstrm_add_amount" in records[0].notes
    assert f"amount_basis={DartAmountBasis.YEAR_TO_DATE.value}" in records[0].notes


@pytest.mark.parametrize("report_code", ("11012", "11014"))
def test_later_interim_ytd_without_cumulative_amount_fails_closed(report_code):
    with pytest.raises(ValueError, match="requires thstrm_add_amount"):
        parse_opendart_financial_facts(
            (row("ifrs-full_Revenue", "300", reprt_code=report_code, thstrm_add_amount=""),),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=FISCAL_PERIOD_END,
            corp_code="00126380",
            business_year="2026",
            report_code=report_code,
        )


@pytest.mark.parametrize(
    ("report_code", "period_end"),
    (("11012", "2026-06-30"), ("11014", "2026-09-30")),
)
def test_later_interim_ytd_checks_all_account_alternatives(report_code, period_end):
    records = parse_opendart_financial_facts(
        (
            row(
                "ifrs-full_Revenue",
                "300",
                reprt_code=report_code,
                thstrm_add_amount="",
            ),
            row(
                "ifrs_Revenue",
                "350",
                reprt_code=report_code,
                thstrm_add_amount="900",
            ),
        ),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=period_end,
        corp_code="00126380",
        business_year="2026",
        report_code=report_code,
    )
    assert records[0].value == 900
    assert "account_id=ifrs_Revenue" in records[0].notes


@pytest.mark.parametrize(
    ("report_code", "period_end"),
    (("11013", "2026-03-31"), ("11011", "2026-12-31")),
)
def test_q1_and_annual_ytd_may_fall_back_to_current_amount(report_code, period_end):
    records = parse_opendart_financial_facts(
        (
            row(
                "ifrs-full_Revenue",
                "300",
                reprt_code=report_code,
                thstrm_add_amount="",
                rcept_no="20270320000123",
            ),
        ),
        target_id="TEST",
        published_date="2027-03-20",
        source_ref="opendart://TEST",
        fiscal_period_end=period_end,
        corp_code="00126380",
        business_year="2026",
        report_code=report_code,
    )
    assert records[0].value == 300
    assert "amount_field=thstrm_amount" in records[0].notes


def test_effective_date_uses_non_calendar_fiscal_period_metadata():
    records = parse_opendart_financial_facts(
        (
            row(
                "ifrs-full_Revenue",
                "300",
                reprt_code="11011",
                bsns_year="2025",
                thstrm_add_amount="",
                rcept_no="20260515000123",
            ),
        ),
        target_id="TEST",
        published_date="2026-05-15",
        source_ref="opendart://TEST",
        fiscal_period_end="2026-03-31",
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
    )
    assert records[0].effective_date == "2026-03-31"


def test_missing_fiscal_period_metadata_fails_closed():
    with pytest.raises(ValueError, match="fiscal_period_end is required"):
        parse_opendart_financial_facts(
            (row("ifrs-full_Assets", "1000", sj_div="BS"),),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=None,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )


def test_fiscal_period_end_after_publication_fails_closed():
    with pytest.raises(ValueError, match="cannot be after published_date"):
        parse_opendart_financial_facts(
            (row("ifrs-full_Assets", "1000", sj_div="BS"),),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end="2026-12-31",
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )


@pytest.mark.parametrize(
    ("period_end", "published_date", "receipt_no"),
    (
        ("2025-09-30", "2026-11-14", "20261114000123"),
        ("2028-09-30", "2028-11-14", "20281114000123"),
    ),
)
def test_fiscal_period_end_outside_requested_business_year_window_fails_closed(
    period_end,
    published_date,
    receipt_no,
):
    with pytest.raises(ValueError, match="not aligned with requested business_year"):
        parse_opendart_financial_facts(
            (
                row(
                    "ifrs-full_Assets",
                    "1000",
                    sj_div="BS",
                    rcept_no=receipt_no,
                ),
            ),
            target_id="TEST",
            published_date=published_date,
            source_ref="opendart://TEST",
            fiscal_period_end=period_end,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )


def test_fiscal_period_end_after_observed_receipt_fails_closed():
    with pytest.raises(ValueError, match="cannot be after observed receipt date"):
        parse_opendart_financial_facts(
            (
                row(
                    "ifrs-full_Assets",
                    "1000",
                    sj_div="BS",
                    rcept_no="20260823000123",
                ),
            ),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=FISCAL_PERIOD_END,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )


def test_observed_receipt_after_publication_fails_closed():
    with pytest.raises(ValueError, match="observed receipt date cannot be after published_date"):
        parse_opendart_financial_facts(
            (
                row(
                    "ifrs-full_Assets",
                    "1000",
                    sj_div="BS",
                    rcept_no="20261115000123",
                ),
            ),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=FISCAL_PERIOD_END,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )


def test_request_level_fs_div_is_authoritative_when_response_rows_omit_it():
    records = parse_opendart_financial_facts(
        (row("ifrs-full_Assets", "1000", sj_div="BS", fs_div=None),),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=FISCAL_PERIOD_END,
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        fs_div="CFS",
    )
    assert records[0].metric == "total_assets"


def test_company_specific_metric_requires_explicit_account_spec():
    custom = DartFactMetricSpec(
        "contract_liabilities",
        ("custom_ContractLiabilities",),
        ("BS",),
        critical=True,
        amount_basis=DartAmountBasis.POINT_IN_TIME,
    )
    records = parse_opendart_financial_facts(
        (row("custom_ContractLiabilities", "5000000000", sj_div="BS"),),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=FISCAL_PERIOD_END,
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        specs=(custom,),
    )
    assert len(records) == 1
    assert records[0].metric == "contract_liabilities"


def test_account_name_without_explicit_account_id_is_not_fuzzy_matched():
    rows = ({
        "rcept_no": "20270320000001",
        "reprt_code": "11011",
        "bsns_year": "2026",
        "sj_div": "BS",
        "account_id": "company_specific_other",
        "account_nm": "계약부채",
        "thstrm_amount": "5000000000",
        "currency": "KRW",
    },)
    records = parse_opendart_financial_facts(
        rows,
        target_id="TEST",
        published_date="2027-03-20",
        source_ref="opendart://TEST",
        fiscal_period_end="2026-12-31",
        corp_code="00126380",
        business_year="2026",
        report_code="11011",
    )
    assert all(item.metric != "contract_liabilities" for item in records)


def test_ambiguous_same_metric_values_fail_closed():
    rows = (
        row("ifrs-full_Revenue", "100"),
        row("ifrs_Revenue", "200"),
    )
    try:
        parse_opendart_financial_facts(
            rows,
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=FISCAL_PERIOD_END,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )
    except ValueError as exc:
        assert "ambiguous DART fact" in str(exc)
    else:
        raise AssertionError("ambiguous DART rows must fail closed")


def test_dart_currency_mismatch_fails_closed():
    try:
        parse_opendart_financial_facts(
            (row("ifrs-full_Revenue", "100", currency="USD"),),
            target_id="TEST",
            published_date="2026-11-14",
            source_ref="opendart://TEST",
            fiscal_period_end=FISCAL_PERIOD_END,
            corp_code="00126380",
            business_year="2026",
            report_code="11014",
        )
    except ValueError as exc:
        assert "currency mismatch" in str(exc)
    else:
        raise AssertionError("currency mismatch must fail closed")


def test_dart_collector_plugs_into_primary_evidence_coverage():
    rows = (
        row("ifrs-full_Revenue", "100000000000"),
        row("dart_OperatingIncomeLoss", "20000000000"),
    )
    collector = opendart_fact_collector(
        source_id="KR_OPENDART_FACTS",
        checked_at="2026-11-14",
        rows=rows,
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        fiscal_period_end=FISCAL_PERIOD_END,
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
    )
    result = collect_primary_evidence(
        target_id="TEST",
        required_metrics=("revenue", "operating_income"),
        collectors=(collector,),
    )
    assert result.coverage_complete
    assert result.covered_metrics == ("revenue", "operating_income")
    assert result.source_snapshot_hash


def test_official_full_financials_url_contract():
    url = build_opendart_full_financials_url(
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        fs_div="CFS",
        api_key="x" * 40,
    )
    assert url.startswith("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?")
    assert "corp_code=00126380" in url
    assert "bsns_year=2026" in url
    assert "reprt_code=11014" in url
    assert "fs_div=CFS" in url


def test_live_collector_uses_injected_transport_and_official_response_shape():
    captured = []

    def fetch_text(url: str) -> str:
        captured.append(url)
        return '''{
          "status":"000","message":"정상","list":[
            {"rcept_no":"20261114000123","reprt_code":"11014","bsns_year":"2026","corp_code":"00126380","sj_div":"IS","account_id":"ifrs-full_Revenue","account_nm":"매출액","thstrm_amount":"300","thstrm_add_amount":"900","currency":"KRW"},
            {"rcept_no":"20261114000123","reprt_code":"11014","bsns_year":"2026","corp_code":"00126380","sj_div":"CIS","account_id":"dart_OperatingIncomeLoss","account_nm":"영업이익","thstrm_amount":"100","thstrm_add_amount":"250","currency":"KRW"}
          ]}'''

    collector = live_opendart_fact_collector(
        fetch_text,
        source_id="KR_OPENDART_FACTS",
        checked_at="2026-11-14",
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        fiscal_period_end=FISCAL_PERIOD_END,
        api_key="x" * 40,
    )
    result = collect_primary_evidence(
        target_id="TEST",
        required_metrics=("revenue", "operating_income"),
        collectors=(collector,),
    )
    assert result.coverage_complete
    by_metric = {item.metric: item for item in result.ledger.active()}
    assert by_metric["revenue"].value == 900
    assert by_metric["operating_income"].value == 250
    assert by_metric["revenue"].observed_date == "2026-11-14"
    assert captured and "fnlttSinglAcntAll.json" in captured[0]


@pytest.mark.parametrize(
    ("response_corp", "response_year", "response_report"),
    (
        ("00999999", "2026", "11014"),
        ("00126380", "2025", "11014"),
        ("00126380", "2026", "11012"),
    ),
)
def test_live_collector_rejects_response_for_a_different_filing(
    response_corp,
    response_year,
    response_report,
):
    def fetch_text(_url: str) -> str:
        return (
            '{"status":"000","message":"정상","list":['
            '{"rcept_no":"20261114000123",'
            f'"reprt_code":"{response_report}","bsns_year":"{response_year}",'
            f'"corp_code":"{response_corp}","sj_div":"IS",'
            '"account_id":"ifrs-full_Revenue","account_nm":"매출액",'
            '"thstrm_amount":"300","thstrm_add_amount":"900","currency":"KRW"}'
            "]}"
        )

    collector = live_opendart_fact_collector(
        fetch_text,
        source_id="KR_OPENDART_FACTS",
        checked_at="2026-11-14",
        corp_code="00126380",
        business_year="2026",
        report_code="11014",
        fiscal_period_end=FISCAL_PERIOD_END,
        api_key="x" * 40,
    )
    with pytest.raises(ValueError, match="does not match requested filing"):
        collect_primary_evidence(
            target_id="TEST",
            required_metrics=("revenue",),
            collectors=(collector,),
        )
