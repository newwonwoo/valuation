#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "skhynix_full_ledger_calibration"
RAW = OUT / "raw_html"
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (compatible; PRISM-full-ledger-calibration/1.0; "
    "+https://github.com/newwonwoo/valuation)"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

EXCHANGE_OVERRIDES = {
    "005930": "krx",
    "000660": "krx",
    "000990": "krx",
    "042700": "krx",
    "281820": "krx",
}

VALUE_ROWS = {
    "revenue": "Revenue",
    "gross_profit": "Gross Profit",
    "operating_income": "Operating Income",
    "net_income": "Net Income",
    "cash_and_investments": "Cash & Investments",
    "total_debt": "Total Debt",
    "net_cash": "Net Cash (Debt)",
    "operating_cash_flow": "Operating Cash Flow",
    "capital_expenditures": "Capital Expenditures",
    "free_cash_flow": "Free Cash Flow",
    "gross_margin": "Gross Margin",
    "operating_margin": "Operating Margin",
    "profit_margin": "Profit Margin",
    "fcf_margin": "FCF Margin",
}

GROWTH_ROWS = {
    "revenue_growth": "Revenue Growth",
    "operating_income_growth": "Operating Income Growth",
    "net_income_growth": "Net Income Growth",
    "operating_cash_flow_growth": "Operating Cash Flow Growth",
    "capex_growth": "CapEx Growth",
    "fcf_growth": "Free Cash Flow Growth",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def flat_column(column: object) -> str:
    if isinstance(column, tuple):
        parts = [
            str(item)
            for item in column
            if str(item) != "nan" and not str(item).startswith("Unnamed")
        ]
        return parts[-1] if parts else str(column[-1])
    return str(column)


def clean_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def number(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "—", "nan", "None"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def quarter_name(column: str) -> str | None:
    match = re.search(r"Q([1-4])\s*(20\d{2})", column)
    if not match:
        return None
    return f"{match.group(2)}Q{match.group(1)}"


def annual_year(column: str) -> int | None:
    match = re.search(r"FY\s*(20\d{2})", column)
    return int(match.group(1)) if match else None


def fetch_html(url: str) -> bytes:
    last_status: int | None = None
    for attempt in range(5):
        response = SESSION.get(url, timeout=45)
        last_status = response.status_code
        if response.status_code == 200 and "Free Cash Flow" in response.text:
            return response.content
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"fetch failed status={last_status}: {url}")


def table_rows(html: bytes) -> dict[str, dict[str, object]]:
    tables = pd.read_html(StringIO(html.decode("utf-8", errors="replace")))
    rows: dict[str, dict[str, object]] = {}
    for frame in tables:
        frame = frame.copy()
        frame.columns = [flat_column(column) for column in frame.columns]
        if frame.empty:
            continue
        first = frame.columns[0]
        for _, row in frame.iterrows():
            label = clean_label(row[first])
            if label and label not in rows:
                rows[label] = {
                    clean_label(column): row[column] for column in frame.columns[1:]
                }
    return rows


def resolve_row(
    rows: dict[str, dict[str, object]], label: str, *, growth: bool = False
) -> dict[str, object] | None:
    if label in rows:
        return rows[label]
    candidates: list[str] = []
    for candidate in rows:
        normalized = clean_label(candidate)
        if growth:
            if normalized.endswith(label):
                candidates.append(candidate)
        elif normalized.startswith(label + " ") and normalized != label + " Growth":
            candidates.append(candidate)
    if not candidates:
        return None
    return rows[min(candidates, key=len)]


def extract_quarterly(
    rows: dict[str, dict[str, object]], label: str, *, growth: bool = False
) -> dict[str, float | None]:
    selected = resolve_row(rows, label, growth=growth)
    if selected is None:
        return {}
    result: dict[str, float | None] = {}
    for column, value in selected.items():
        quarter = quarter_name(column)
        if quarter is not None:
            result[quarter] = number(value)
    return result


def extract_annual(
    rows: dict[str, dict[str, object]], label: str
) -> dict[int, float | None]:
    selected = resolve_row(rows, label)
    if selected is None:
        return {}
    result: dict[int, float | None] = {}
    for column, value in selected.items():
        year = annual_year(column)
        if year is not None:
            result[year] = number(value)
    return result


def load_companies() -> list[dict[str, object]]:
    seed_path = ROOT / "config" / "semiconductor_calibration_seed.yaml"
    seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    companies: list[dict[str, object]] = []
    for company in seed["companies"]:
        ticker = str(company["ticker"]).zfill(6)
        companies.append(
            {
                "company": company["name"],
                "ticker": ticker,
                "corp_code": str(company["corp_code"]).zfill(8),
                "sub_industries": list(company["sub_industries"]),
                "exchange": EXCHANGE_OVERRIDES.get(ticker, "kosdaq"),
            }
        )
    if len(companies) != 30:
        raise RuntimeError(f"seed company count drifted: {len(companies)}")
    return companies


def fetch_company(company: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    ticker = str(company["ticker"])
    exchange = str(company["exchange"])
    base_url = f"https://stockanalysis.com/quote/{exchange}/{ticker}/financials/"
    quarterly_url = base_url + "?p=quarterly"

    quarterly_html = fetch_html(quarterly_url)
    annual_html = fetch_html(base_url)
    (RAW / f"{ticker}_quarterly.html").write_bytes(quarterly_html)
    (RAW / f"{ticker}_annual.html").write_bytes(annual_html)

    quarterly_rows = table_rows(quarterly_html)
    annual_rows = table_rows(annual_html)
    metrics: dict[str, dict[str, float | None]] = {}
    for key, label in VALUE_ROWS.items():
        metrics[key] = extract_quarterly(quarterly_rows, label)
    for key, label in GROWTH_ROWS.items():
        metrics[key] = extract_quarterly(quarterly_rows, label, growth=True)

    annual_revenue = extract_annual(annual_rows, "Revenue")
    annual_operating_income = extract_annual(annual_rows, "Operating Income")
    annual_operating_cash_flow = extract_annual(annual_rows, "Operating Cash Flow")
    annual_capex = extract_annual(annual_rows, "Capital Expenditures")
    annual_fcf = extract_annual(annual_rows, "Free Cash Flow")

    # Reconstruct missing fourth-quarter flow values from annual less Q1-Q3.
    annual_metric_map = {
        "revenue": annual_revenue,
        "operating_income": annual_operating_income,
        "operating_cash_flow": annual_operating_cash_flow,
        "capital_expenditures": annual_capex,
        "free_cash_flow": annual_fcf,
    }
    for year in range(2021, 2026):
        q4 = f"{year}Q4"
        for metric, annual_series in annual_metric_map.items():
            if metrics[metric].get(q4) is not None:
                continue
            annual_value = annual_series.get(year)
            q123 = [metrics[metric].get(f"{year}Q{quarter}") for quarter in (1, 2, 3)]
            if annual_value is not None and all(value is not None for value in q123):
                reconstructed = annual_value - sum(value for value in q123 if value is not None)
                metrics[metric][q4] = reconstructed

        revenue = metrics["revenue"].get(q4)
        operating_income = metrics["operating_income"].get(q4)
        fcf = metrics["free_cash_flow"].get(q4)
        if revenue not in (None, 0):
            if operating_income is not None and metrics["operating_margin"].get(q4) is None:
                metrics["operating_margin"][q4] = 100.0 * operating_income / revenue
            if fcf is not None and metrics["fcf_margin"].get(q4) is None:
                metrics["fcf_margin"][q4] = 100.0 * fcf / revenue

    quarters = sorted(
        {quarter for series in metrics.values() for quarter in series},
        key=lambda quarter: (int(quarter[:4]), int(quarter[-1])),
    )
    fetched_at = datetime.now(timezone.utc).isoformat()
    panel_rows: list[dict[str, object]] = []
    for quarter in quarters:
        row: dict[str, object] = {
            **company,
            "sub_industries": "|".join(company["sub_industries"]),
            "quarter": quarter,
            "source_url": quarterly_url,
            "source_fetched_at": fetched_at,
            "quarterly_html_sha256": sha256_bytes(quarterly_html),
            "annual_html_sha256": sha256_bytes(annual_html),
            "unit": "KRW_million",
        }
        for metric, series in metrics.items():
            value = series.get(quarter)
            if metric == "capital_expenditures" and value is not None:
                value = abs(value)
            row[metric] = value
        panel_rows.append(row)

    audit = {
        **company,
        "quarterly_url": quarterly_url,
        "fetched_at": fetched_at,
        "quarterly_html_sha256": sha256_bytes(quarterly_html),
        "annual_html_sha256": sha256_bytes(annual_html),
        "quarter_count": len(quarters),
        "min_quarter": quarters[0] if quarters else None,
        "max_quarter": quarters[-1] if quarters else None,
        "non_null_counts": {
            metric: sum(value is not None for value in series.values())
            for metric, series in metrics.items()
        },
    }
    return panel_rows, audit


def main() -> int:
    companies = load_companies()
    panel: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for index, company in enumerate(companies, 1):
        try:
            rows, audit = fetch_company(company)
            panel.extend(rows)
            audits.append(audit)
            print(
                f"[{index:02d}/30] {company['ticker']} {company['company']} "
                f"OK rows={len(rows)}",
                flush=True,
            )
        except Exception as exc:
            failures.append(
                {
                    **company,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(
                f"[{index:02d}/30] {company['ticker']} {company['company']} FAIL {exc}",
                flush=True,
            )
        time.sleep(0.35)

    frame = pd.DataFrame(panel)
    if frame.empty:
        raise RuntimeError("no quarterly facts were collected")
    frame = frame.sort_values(["ticker", "quarter"]).reset_index(drop=True)
    frame.to_csv(OUT / "semiconductor_quarterly_fact_panel.csv", index=False, encoding="utf-8-sig")
    frame.to_json(
        OUT / "semiconductor_quarterly_fact_panel.jsonl",
        orient="records",
        lines=True,
        force_ascii=False,
    )
    pd.DataFrame(audits).to_json(
        OUT / "source_audit.json",
        orient="records",
        indent=2,
        force_ascii=False,
    )
    pd.DataFrame(failures).to_json(
        OUT / "source_failures.json",
        orient="records",
        indent=2,
        force_ascii=False,
    )

    metric_columns = list(VALUE_ROWS) + list(GROWTH_ROWS)
    summary = {
        "schema_version": "semiconductor_quarterly_fact_panel_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company_count_expected": len(companies),
        "company_count_succeeded": len(audits),
        "company_count_failed": len(failures),
        "row_count": int(len(frame)),
        "unique_company_quarters": int(frame[["ticker", "quarter"]].drop_duplicates().shape[0]),
        "quarter_min": str(frame["quarter"].min()),
        "quarter_max": str(frame["quarter"].max()),
        "metric_fact_count": int(frame[metric_columns].notna().sum().sum()),
        "metric_non_null_counts": {
            metric: int(frame[metric].notna().sum()) for metric in metric_columns
        },
        "failures": failures,
    }
    (OUT / "panel_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
