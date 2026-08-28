from __future__ import annotations

import hashlib
import inspect
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd


OUT = Path("artifacts/skhynix_calibration_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)

DART_FINANCIAL_URL = (
    "https://raw.githubusercontent.com/oyeong011/financial-database/"
    "main/data/dart/financial_data.csv"
)
DART_COMPANIES_URL = (
    "https://raw.githubusercontent.com/oyeong011/financial-database/"
    "main/data/dart/companies.csv"
)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "newwonwoo-valuation-calibration-diagnostic/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_summary(frame: pd.DataFrame) -> dict[str, object]:
    summary: dict[str, object] = {
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "null_counts": {
            str(column): int(value) for column, value in frame.isna().sum().items()
        },
    }
    for column in frame.columns:
        series = frame[column]
        if series.dtype == "object" or str(series.dtype).startswith("string"):
            values = series.dropna().astype(str)
            unique_count = int(values.nunique())
            summary.setdefault("categorical", {})[str(column)] = {
                "unique_count": unique_count,
                "sample_values": values.drop_duplicates().head(30).tolist(),
            }
        elif pd.api.types.is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce")
            summary.setdefault("numeric", {})[str(column)] = {
                "min": None if numeric.dropna().empty else float(numeric.min()),
                "max": None if numeric.dropna().empty else float(numeric.max()),
            }
    return summary


def find_rows(frame: pd.DataFrame, needles: tuple[str, ...]) -> pd.DataFrame:
    mask = pd.Series(False, index=frame.index)
    for column in frame.columns:
        values = frame[column].astype(str)
        column_mask = pd.Series(False, index=frame.index)
        for needle in needles:
            column_mask |= values.str.contains(needle, case=False, na=False, regex=False)
        mask |= column_mask
    return frame.loc[mask]


def pykrx_diagnostic() -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        from pykrx import stock
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"import_error": f"{type(exc).__name__}: {exc}"}

    result["signatures"] = {
        "get_index_ticker_list": str(inspect.signature(stock.get_index_ticker_list)),
        "get_index_ticker_name": str(inspect.signature(stock.get_index_ticker_name)),
        "get_index_portfolio_deposit_file": str(
            inspect.signature(stock.get_index_portfolio_deposit_file)
        ),
    }

    index_rows: list[dict[str, object]] = []
    for market in ("KRX", "KOSPI", "KOSDAQ", "테마"):
        try:
            try:
                tickers = stock.get_index_ticker_list(market=market)
            except TypeError:
                tickers = stock.get_index_ticker_list("20260828", market=market)
            for ticker in tickers:
                try:
                    name = stock.get_index_ticker_name(ticker)
                except Exception as exc:  # pragma: no cover - diagnostic only
                    name = f"ERROR:{type(exc).__name__}:{exc}"
                index_rows.append({"market": market, "ticker": ticker, "name": name})
        except Exception as exc:  # pragma: no cover - diagnostic only
            index_rows.append(
                {
                    "market": market,
                    "ticker": None,
                    "name": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    result["indices"] = index_rows
    candidates = [
        row
        for row in index_rows
        if any(
            keyword in str(row.get("name", ""))
            for keyword in ("반도체", "정보기술", "IT", "전자")
        )
    ]
    result["candidate_indices"] = candidates

    portfolio_results: list[dict[str, object]] = []
    dates = (
        "20150331",
        "20150630",
        "20150930",
        "20151230",
        "20160331",
        "20230331",
        "20240329",
        "20260828",
    )
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "")
        if not ticker:
            continue
        for date in dates:
            try:
                try:
                    members = stock.get_index_portfolio_deposit_file(ticker, date)
                except TypeError:
                    members = stock.get_index_portfolio_deposit_file(ticker, date=date)
                portfolio_results.append(
                    {
                        "index_ticker": ticker,
                        "index_name": candidate.get("name"),
                        "date": date,
                        "member_count": int(len(members)),
                        "members": list(members),
                    }
                )
            except Exception as exc:  # pragma: no cover - diagnostic only
                portfolio_results.append(
                    {
                        "index_ticker": ticker,
                        "index_name": candidate.get("name"),
                        "date": date,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    result["historical_portfolios"] = portfolio_results
    return result


def main() -> int:
    financial_path = OUT / "financial_data.csv"
    companies_path = OUT / "companies.csv"
    download(DART_FINANCIAL_URL, financial_path)
    download(DART_COMPANIES_URL, companies_path)

    financial = pd.read_csv(financial_path, low_memory=False)
    companies = pd.read_csv(companies_path, low_memory=False)

    financial_summary = frame_summary(financial)
    companies_summary = frame_summary(companies)
    skhynix_rows = find_rows(financial, ("000660", "SK하이닉스", "SK hynix"))
    skhynix_companies = find_rows(companies, ("000660", "SK하이닉스", "SK hynix"))

    financial.head(100).to_csv(OUT / "financial_head.csv", index=False)
    companies.head(100).to_csv(OUT / "companies_head.csv", index=False)
    skhynix_rows.to_csv(OUT / "skhynix_financial_rows.csv", index=False)
    skhynix_companies.to_csv(OUT / "skhynix_company_rows.csv", index=False)

    diagnostic = {
        "source": {
            "financial_url": DART_FINANCIAL_URL,
            "companies_url": DART_COMPANIES_URL,
            "financial_size": financial_path.stat().st_size,
            "companies_size": companies_path.stat().st_size,
            "financial_sha256": sha256(financial_path),
            "companies_sha256": sha256(companies_path),
        },
        "financial": financial_summary,
        "companies": companies_summary,
        "skhynix_financial_row_count": int(len(skhynix_rows)),
        "skhynix_company_row_count": int(len(skhynix_companies)),
        "pykrx": pykrx_diagnostic(),
    }
    (OUT / "diagnostic.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
