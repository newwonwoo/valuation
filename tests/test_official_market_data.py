from __future__ import annotations

from decimal import Decimal
import json

import pytest

from valuation_engine.official_market_data import (
    DataCollectionError,
    IndexPoint,
    PricePoint,
    collect_damodaran_country_risk,
    collect_ecos_series,
    collect_opendart_basic_eps,
    compute_ols_beta,
    fetch_krx_day,
    load_authorized_street_export,
)


def test_fetch_krx_day_filters_codes_and_exact_benchmark():
    calls = []

    def fetch_json(url, headers, label):
        calls.append((url, dict(headers), label))
        if "/sto/" in url:
            return {
                "OutBlock_1": [
                    {
                        "BAS_DD": "20260824",
                        "ISU_CD": "005930",
                        "ISU_NM": "삼성전자",
                        "TDD_CLSPRC": "123,000",
                    },
                    {
                        "BAS_DD": "20260824",
                        "ISU_CD": "000660",
                        "ISU_NM": "SK하이닉스",
                        "TDD_CLSPRC": "350000",
                    },
                ]
            }
        return {
            "OutBlock_1": [
                {"BAS_DD": "20260824", "IDX_NM": "코스피", "CLSPRC_IDX": "4,111.25"}
            ]
        }

    prices, benchmark = fetch_krx_day(
        fetch_json,
        auth_key="SECRET",
        market="KOSPI",
        bas_dd="20260824",
        codes=("005930",),
        benchmark_name="코스피",
    )

    assert tuple(prices) == ("005930",)
    assert prices["005930"].close == 123000.0
    assert benchmark is not None and benchmark.close == 4111.25
    assert len(calls) == 2
    assert all(call[1]["AUTH_KEY"] == "SECRET" for call in calls)


def test_compute_ols_beta_recovers_known_slope():
    market_returns = [0.01, -0.005, 0.02, -0.01, 0.015]
    market_prices = [100.0]
    stock_prices = [50.0]
    for market_return in market_returns:
        market_prices.append(market_prices[-1] * (1 + market_return))
        stock_prices.append(stock_prices[-1] * (1 + 2 * market_return))

    days = [f"2026-01-{day:02d}" for day in range(1, 7)]
    stock = [
        PricePoint(day, "PEER1", "Peer One", price)
        for day, price in zip(days, stock_prices)
    ]
    market = [
        IndexPoint(day, "Benchmark", price)
        for day, price in zip(days, market_prices)
    ]

    result = compute_ols_beta(stock, market, min_observations=5)

    assert result.beta == pytest.approx(2.0)
    assert result.observations == 5


def test_ecos_series_never_persists_api_key_in_source_ref():
    captured = {}

    def fetch_json(url, headers, label):
        captured["url"] = url
        return {
            "StatisticSearch": {
                "row": [
                    {
                        "TIME": "20260824",
                        "DATA_VALUE": "3.125",
                        "UNIT_NAME": "%",
                        "ITEM_NAME1": "국고채(10년)",
                        "STAT_CODE": "817Y002",
                    }
                ]
            }
        }

    rows = collect_ecos_series(
        fetch_json,
        api_key="TOPSECRET",
        stat_code="817Y002",
        cycle="D",
        start_time="20260824",
        end_time="20260824",
        item_code="010210000",
    )

    assert "TOPSECRET" in captured["url"]
    assert "TOPSECRET" not in rows[0].source_ref
    assert rows[0].value == pytest.approx(3.125)


def test_ecos_result_error_fails_closed():
    def fetch_json(url, headers, label):
        return {"RESULT": {"CODE": "ERROR-100", "MESSAGE": "bad request"}}

    with pytest.raises(DataCollectionError, match="ERROR-100"):
        collect_ecos_series(
            fetch_json,
            api_key="key",
            stat_code="817Y002",
            cycle="D",
            start_time="20260824",
            end_time="20260824",
            item_code="010210000",
        )


def test_damodaran_country_risk_separates_mature_and_country_premia():
    html = """
    <html><body>
    Last updated: January 5, 2026
    <table>
      <tr><th>Country</th><th>Adj. Default Spread</th><th>Equity Risk Premium</th>
          <th>Country Risk Premium</th><th>Corporate Tax Rate</th><th>Moody's rating</th></tr>
      <tr><td>Korea</td><td>0.51%</td><td>5.65%</td><td>0.57%</td><td>24.00%</td><td>Aa2</td></tr>
    </table>
    </body></html>
    """

    result = collect_damodaran_country_risk(
        lambda url, label: html,
        country="Korea",
    )

    assert result.total_equity_risk_premium == pytest.approx(0.0565)
    assert result.country_risk_premium == pytest.approx(0.0057)
    assert result.mature_market_erp == pytest.approx(0.0508)
    assert result.as_of == "2026-01-05"


def _dart_payload(*, report_code: str, current: str, cumulative: str | None):
    return {
        "status": "000",
        "list": [
            {
                "corp_code": "00126380",
                "bsns_year": "2026",
                "reprt_code": report_code,
                "fs_div": "CFS",
                "sj_div": "IS",
                "account_id": "ifrs-full_BasicEarningsLossPerShare",
                "thstrm_amount": current,
                "thstrm_add_amount": cumulative,
                "rcept_no": "20260814000123",
            }
        ],
    }


def test_opendart_q2_eps_requires_cumulative_amount():
    with pytest.raises(DataCollectionError, match="cumulative"):
        collect_opendart_basic_eps(
            lambda url, headers, label: _dart_payload(
                report_code="11012", current="1200", cumulative=None
            ),
            api_key="SECRET",
            corp_code="00126380",
            business_year="2026",
            report_code="11012",
        )


def test_opendart_q2_eps_uses_cumulative_amount():
    result = collect_opendart_basic_eps(
        lambda url, headers, label: _dart_payload(
            report_code="11012", current="1200", cumulative="2300"
        ),
        api_key="SECRET",
        corp_code="00126380",
        business_year="2026",
        report_code="11012",
    )

    assert result.eps == Decimal("2300")
    assert result.amount_field == "thstrm_add_amount"
    assert "SECRET" not in result.source_ref


def test_authorized_street_export_rejects_unapproved_transport(tmp_path):
    path = tmp_path / "street.json"
    path.write_text(
        json.dumps(
            {
                "authorization_basis": "web_scrape",
                "reports": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataCollectionError, match="authorization_basis"):
        load_authorized_street_export(path)


def test_authorized_street_export_builds_runtime_reports(tmp_path):
    path = tmp_path / "street.json"
    path.write_text(
        json.dumps(
            {
                "authorization_basis": "licensed_export",
                "source_ref": "licensed://vendor/export-20260825",
                "reports": [
                    {
                        "broker": "Broker A",
                        "analyst": "Analyst A",
                        "published_date": "2026-08-24",
                        "target_price": 50000,
                        "target_price_currency": "KRW",
                        "valuation_method": "DCF",
                        "base_year": "2027E",
                        "source_ref": "licensed://vendor/report-1",
                        "estimates": [
                            {
                                "metric": "EPS",
                                "period": "2027E",
                                "value": 3100,
                                "unit": "KRW/share",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reports = load_authorized_street_export(path)

    assert len(reports) == 1
    assert reports[0].target_price == 50000
    assert reports[0].estimates[0].metric == "EPS"
