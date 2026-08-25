from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from valuation_engine.official_market_data import (
    DAMODARAN_COUNTRY_RISK_URL,
    DataCollectionError,
    collect_damodaran_country_risk,
    collect_ecos_series,
    collect_opendart_basic_eps,
    compute_ols_beta,
    fetch_krx_day,
    load_authorized_street_export,
)


class HttpTransport:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_bytes: int = 16_000_000,
        retries: int = 1,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.retries = retries

    def _get(self, url: str, headers: Mapping[str, str], label: str) -> bytes:
        request_headers = {
            "User-Agent": "RocketSLA-ValuationCollector/0.1 (+authorized-sources-only)",
            **dict(headers),
        }
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers=request_headers)
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise DataCollectionError(
                            f"{label} response exceeds max_bytes={self.max_bytes}"
                        )
                    return raw
            except (HTTPError, URLError, TimeoutError, OSError, DataCollectionError) as exc:
                if attempt >= self.retries:
                    # Never include URL/header values because API keys can live in either.
                    raise DataCollectionError(
                        f"{label} fetch failed ({type(exc).__name__})"
                    ) from None
                time.sleep(0.25 * (attempt + 1))
        raise AssertionError("unreachable")

    def json(self, url: str, headers: Mapping[str, str], label: str):
        raw = self._get(url, headers, label)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataCollectionError(f"{label} did not return UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise DataCollectionError(f"{label} JSON root must be an object")
        return payload

    def text(self, url: str, label: str) -> str:
        raw = self._get(url, {}, label)
        for encoding in ("utf-8", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DataCollectionError(f"{label} response encoding is unsupported")


def _serialize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "__dataclass_fields__"):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def _envelope(source_id: str, authorization_basis: str, source_ref: str, payload):
    core = {
        "source_id": source_id,
        "authorization_basis": authorization_basis,
        "source_ref": source_ref,
        "checked_at": date.today().isoformat(),
        "payload": _serialize(payload),
    }
    encoded = json.dumps(
        core, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    core["snapshot_hash"] = sha256(encoded).hexdigest()
    return core


def _write(payload, output: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _weekdays(start: date, end: date):
    if start > end:
        raise ValueError("start date must be on or before end date")
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            yield cursor
        cursor += timedelta(days=1)


def _run_krx_beta(args, transport: HttpTransport):
    auth_key = os.getenv("KRX_AUTH_KEY", "")
    if not auth_key:
        raise DataCollectionError("KRX_AUTH_KEY is not configured")
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    stock_series = {code: [] for code in args.code}
    benchmark_series = []
    request_days = 0

    for day in _weekdays(start, end):
        request_days += 1
        bas_dd = day.strftime("%Y%m%d")
        prices, benchmark = fetch_krx_day(
            transport.json,
            auth_key=auth_key,
            market=args.market,
            bas_dd=bas_dd,
            codes=args.code,
            benchmark_name=args.benchmark,
        )
        for code, point in prices.items():
            stock_series[code].append(point)
        if benchmark is not None:
            benchmark_series.append(benchmark)
        if args.sleep > 0:
            time.sleep(args.sleep)

    estimates = []
    for code, points in stock_series.items():
        estimates.append(
            compute_ols_beta(
                points,
                benchmark_series,
                min_observations=args.min_observations,
            )
        )
    return _envelope(
        "KR_KRX_OPENAPI",
        "official_api_approval_required",
        "https://openapi.krx.co.kr/",
        {
            "market": args.market,
            "benchmark": args.benchmark,
            "requested_business_days": request_days,
            "beta_estimates": estimates,
            "stock_series": stock_series,
            "benchmark_series": benchmark_series,
        },
    )


def _run_ecos(args, transport: HttpTransport):
    api_key = os.getenv("ECOS_API_KEY", "")
    observations = collect_ecos_series(
        transport.json,
        api_key=api_key,
        stat_code=args.stat_code,
        cycle=args.cycle,
        start_time=args.start,
        end_time=args.end,
        item_code=args.item_code,
        max_rows=args.max_rows,
    )
    return _envelope(
        "KR_BOK_ECOS",
        "official_api",
        observations[0].source_ref,
        {
            "stat_code": args.stat_code,
            "item_code": args.item_code,
            "observations": observations,
            "latest": max(observations, key=lambda item: item.time),
        },
    )


def _run_damodaran(args, transport: HttpTransport):
    risk = collect_damodaran_country_risk(
        transport.text,
        country=args.country,
        url=args.url,
    )
    return _envelope(
        "EXT_DAMODARAN_COUNTRY_RISK",
        "open_noncommercial_with_attribution",
        args.url,
        risk,
    )


def _run_dart_eps(args, transport: HttpTransport):
    api_key = os.getenv("DART_API_KEY", "")
    eps = collect_opendart_basic_eps(
        transport.json,
        api_key=api_key,
        corp_code=args.corp_code,
        business_year=args.business_year,
        report_code=args.report_code,
        fs_div=args.fs_div,
    )
    return _envelope(
        "KR_OPENDART",
        "official_api",
        eps.source_ref,
        {
            "metric": "basic_eps_ytd",
            "unit": "KRW_per_share",
            "observation": eps,
            "note": (
                "Filing EPS only. This is not target-company Street consensus EPS "
                "and must not be used as a post-freeze reference before Intrinsic Freeze."
            ),
        },
    )


def _run_street_import(args):
    reports = load_authorized_street_export(args.input)
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    return _envelope(
        "AUTHORIZED_STREET_EXPORT",
        str(raw["authorization_basis"]),
        str(raw.get("source_ref") or "caller-authorized-export"),
        {
            "report_count": len(reports),
            "reports": reports,
            "note": "Post-freeze reference only; not an intrinsic assumption source.",
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect valuation inputs only from official/open-authorized sources. "
            "No login/paywall/robots bypass and no target Street leakage before Intrinsic Freeze."
        )
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--max-bytes", type=int, default=16_000_000)
    sub = parser.add_subparsers(dest="command", required=True)

    krx = sub.add_parser("krx-beta", help="Estimate regression beta from KRX Open API history")
    krx.add_argument("--market", choices=["KOSPI", "KOSDAQ"], required=True)
    krx.add_argument("--code", action="append", required=True, help="Repeat for multiple peer codes")
    krx.add_argument("--benchmark", required=True, help="Exact KRX index name")
    krx.add_argument("--start", required=True, help="YYYY-MM-DD")
    krx.add_argument("--end", required=True, help="YYYY-MM-DD")
    krx.add_argument("--min-observations", type=int, default=120)
    krx.add_argument("--sleep", type=float, default=0.10, help="Delay after each trading-day pair")
    krx.add_argument("--output")

    ecos = sub.add_parser("ecos", help="Collect a BOK ECOS StatisticSearch series")
    ecos.add_argument("--stat-code", required=True)
    ecos.add_argument("--item-code", required=True)
    ecos.add_argument("--cycle", required=True)
    ecos.add_argument("--start", required=True)
    ecos.add_argument("--end", required=True)
    ecos.add_argument("--max-rows", type=int, default=10000)
    ecos.add_argument("--output")

    risk = sub.add_parser(
        "damodaran-risk",
        help="Collect country ERP/CRP from Damodaran's open non-commercial dataset",
    )
    risk.add_argument("--country", default="Korea")
    risk.add_argument("--url", default=DAMODARAN_COUNTRY_RISK_URL)
    risk.add_argument("--output")

    dart = sub.add_parser("dart-eps", help="Collect filing basic EPS from official OpenDART")
    dart.add_argument("--corp-code", required=True)
    dart.add_argument("--business-year", required=True)
    dart.add_argument(
        "--report-code",
        choices=["11013", "11012", "11014", "11011"],
        required=True,
    )
    dart.add_argument("--fs-div", choices=["CFS", "OFS"], default="CFS")
    dart.add_argument("--output")

    street = sub.add_parser(
        "street-import",
        help="Validate/import a licensed or explicitly permitted Street JSON export",
    )
    street.add_argument("--input", required=True)
    street.add_argument("--output")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    transport = HttpTransport(
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        retries=args.retries,
    )
    if args.command == "krx-beta":
        payload = _run_krx_beta(args, transport)
    elif args.command == "ecos":
        payload = _run_ecos(args, transport)
    elif args.command == "damodaran-risk":
        payload = _run_damodaran(args, transport)
    elif args.command == "dart-eps":
        payload = _run_dart_eps(args, transport)
    else:
        payload = _run_street_import(args)
    _write(payload, getattr(args, "output", None))


if __name__ == "__main__":
    try:
        main()
    except (DataCollectionError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {"status": "collection_failed", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
