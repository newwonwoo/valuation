from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import Request, urlopen

from valuation_engine.skhynix_beta_snapshot import calculate_beta


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "config" / "skhynix_beta_snapshot.json"
START_DATE = "2021-08-28"
END_DATE = "2026-08-28"
NASDAQ_URL = (
    "https://api.nasdaq.com/api/quote/{symbol}/historical"
    "?assetclass={asset_class}&fromdate={start}&limit=5000&todate={end}"
)
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
USER_AGENT = "PRISM valuation research contact@example.com"

PEERS = {
    "INTC": {
        "cik": "0000050863",
        "accession": "0000050863-26-000157",
        "filing": "https://www.sec.gov/Archives/edgar/data/50863/000005086326000157/intc-20260627.htm",
        "debt_tags": ("DebtCurrent", "LongTermDebtNoncurrent"),
        "equity_tag": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    },
    "AVGO": {
        "cik": "0001730168",
        "accession": "0001730168-26-000054",
        "filing": "https://www.sec.gov/Archives/edgar/data/1730168/000173016826000054/avgo-20260503.htm",
        "debt_tags": ("DebtLongtermAndShorttermCombinedAmount",),
        "equity_tag": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    },
    "MRVL": {
        "cik": "0001835632",
        "accession": "0001835632-26-000025",
        "filing": "https://www.sec.gov/Archives/edgar/data/1835632/000183563226000025/mrvl-20260801.htm",
        "debt_tags": ("LongTermDebt",),
        "equity_tag": "StockholdersEquity",
    },
    "MU": {
        "cik": "0000723125",
        "accession": "0000723125-26-000015",
        "filing": "https://www.sec.gov/Archives/edgar/data/723125/000072312526000015/mu-20260528.htm",
        "debt_tags": ("LongTermDebtAndCapitalLeaseObligations",),
        "equity_tag": "StockholdersEquity",
    },
}


def _fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    with urlopen(request, timeout=90) as response:
        return response.read()


def _nasdaq_series(symbol: str, asset_class: str) -> tuple[str, bytes, list[list[str]]]:
    url = NASDAQ_URL.format(
        symbol=symbol,
        asset_class=asset_class,
        start=START_DATE,
        end=END_DATE,
    )
    raw = _fetch(url)
    payload = json.loads(raw)
    rows = payload["data"]["tradesTable"]["rows"]
    by_week: dict[tuple[int, int], tuple[datetime, float]] = {}
    for row in rows:
        observed = datetime.strptime(str(row["date"]), "%m/%d/%Y")
        close = float(str(row["close"]).replace("$", "").replace(",", ""))
        week = (observed.isocalendar().year, observed.isocalendar().week)
        if week not in by_week or observed > by_week[week][0]:
            by_week[week] = (observed, close)
    weekly = [
        [observed.date().isoformat(), format(close, ".12g")]
        for observed, close in sorted(by_week.values())
    ]
    if len(weekly) < 250:
        raise RuntimeError(f"{symbol} Nasdaq history is too short: {len(weekly)}")
    return url, raw, weekly


def _fact_row(
    payload: dict,
    *,
    tag: str,
    accession: str,
) -> dict:
    rows = payload["facts"]["us-gaap"][tag]["units"]["USD"]
    matches = [row for row in rows if row.get("accn") == accession]
    if not matches:
        raise RuntimeError(f"SEC fact {tag} is missing from {accession}")
    row = max(matches, key=lambda item: (item.get("end", ""), item.get("filed", "")))
    return {
        "tag": tag,
        "value": row["val"],
        "end": row["end"],
        "filed": row["filed"],
        "form": row["form"],
        "accession": row["accn"],
        "frame": row.get("frame"),
    }


def build_snapshot() -> dict:
    benchmark_url, benchmark_raw, benchmark_weekly = _nasdaq_series("COMP", "INDEX")
    benchmark_points = tuple((date, float(close)) for date, close in benchmark_weekly)
    peers: dict[str, dict] = {}
    for peer_id, spec in PEERS.items():
        price_url, price_raw, weekly = _nasdaq_series(peer_id, "STOCKS")
        calculated = calculate_beta(
            tuple((date, float(close)) for date, close in weekly),
            benchmark_points,
        )
        facts_url = SEC_COMPANY_FACTS_URL.format(cik=spec["cik"])
        facts_raw = _fetch(facts_url)
        facts = json.loads(facts_raw)
        debt_facts = [
            _fact_row(facts, tag=tag, accession=spec["accession"])
            for tag in spec["debt_tags"]
        ]
        equity_fact = _fact_row(
            facts,
            tag=spec["equity_tag"],
            accession=spec["accession"],
        )
        debt = sum(float(row["value"]) for row in debt_facts)
        equity = float(equity_fact["value"])
        peers[peer_id] = {
            "price_source_ref": price_url,
            "price_raw_response_sha256": sha256(price_raw).hexdigest(),
            "weekly_close": weekly,
            "ols": {
                key: calculated[key]
                for key in (
                    "beta",
                    "standard_error",
                    "alpha",
                    "r_squared",
                    "observations",
                    "start_date",
                    "end_date",
                    "series_hash",
                )
            },
            "capital": {
                "company_facts_source_ref": facts_url,
                "company_facts_raw_sha256": sha256(facts_raw).hexdigest(),
                "filing_source_ref": spec["filing"],
                "debt_facts": debt_facts,
                "equity_fact": equity_fact,
                "debt": debt,
                "equity": equity,
                "debt_to_equity": debt / equity,
            },
        }
    return {
        "contract": "skhynix_beta_snapshot/v1",
        "as_of": END_DATE,
        "method": "five-year weekly close-to-close OLS with intercept",
        "benchmark_id": "COMP",
        "benchmark_source_ref": benchmark_url,
        "benchmark_raw_response_sha256": sha256(benchmark_raw).hexdigest(),
        "benchmark_weekly_close": benchmark_weekly,
        "peers": peers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_snapshot()
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(sha256(args.output.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

