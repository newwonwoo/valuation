from valuation_engine.dart_facts import (
    DartFactMetricSpec,
    opendart_fact_collector,
    parse_opendart_financial_facts,
)
from valuation_engine.evidence_collection import collect_primary_evidence


def row(account_id: str, amount: str, *, sj_div: str = "IS", fs_div: str = "CFS") -> dict:
    return {
        "rcept_no": "20260823000123",
        "reprt_code": "11014",
        "bsns_year": "2026",
        "fs_div": fs_div,
        "sj_div": sj_div,
        "account_id": account_id,
        "account_nm": account_id,
        "thstrm_amount": amount,
    }


def test_core_dart_facts_preserve_large_integer_precision():
    records = parse_opendart_financial_facts(
        (
            row("ifrs-full_Revenue", "12,345,678,901,234,567"),
            row("dart_OperatingIncomeLoss", "1,234,567,890,123"),
        ),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
    )
    by_metric = {item.metric: item for item in records}
    assert by_metric["revenue"].value == 12345678901234567
    assert isinstance(by_metric["revenue"].value, int)
    assert by_metric["revenue"].effective_date == "2026-09-30"
    assert by_metric["revenue"].critical


def test_company_specific_metric_requires_explicit_account_spec():
    custom = DartFactMetricSpec(
        "contract_liabilities",
        ("custom_ContractLiabilities",),
        ("BS",),
        critical=True,
    )
    records = parse_opendart_financial_facts(
        (row("custom_ContractLiabilities", "5000000000", sj_div="BS"),),
        target_id="TEST",
        published_date="2026-11-14",
        source_ref="opendart://TEST",
        specs=(custom,),
    )
    assert len(records) == 1
    assert records[0].metric == "contract_liabilities"


def test_account_name_without_explicit_account_id_is_not_fuzzy_matched():
    rows = ({
        "rcept_no": "1",
        "reprt_code": "11011",
        "bsns_year": "2026",
        "fs_div": "CFS",
        "sj_div": "BS",
        "account_id": "company_specific_other",
        "account_nm": "계약부채",
        "thstrm_amount": "5000000000",
    },)
    records = parse_opendart_financial_facts(
        rows,
        target_id="TEST",
        published_date="2027-03-20",
        source_ref="opendart://TEST",
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
        )
    except ValueError as exc:
        assert "ambiguous DART fact" in str(exc)
    else:
        raise AssertionError("ambiguous DART rows must fail closed")


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
    )
    result = collect_primary_evidence(
        target_id="TEST",
        required_metrics=("revenue", "operating_income"),
        collectors=(collector,),
    )
    assert result.coverage_complete
    assert result.covered_metrics == ("revenue", "operating_income")
    assert result.source_snapshot_hash
