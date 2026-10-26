from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from statistics import fmean
from typing import Callable, Mapping, Sequence
from urllib.parse import urlencode
import json
from math import isfinite


class DataCollectionError(RuntimeError):
    pass


FetchJson = Callable[[str, Mapping[str, str], str], Mapping[str, object]]
FetchText = Callable[[str, str], str]

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
KRX_SOURCE_REF = "https://openapi.krx.co.kr/"
ECOS_SOURCE_REF = "https://ecos.bok.or.kr/api/"
DAMODARAN_COUNTRY_RISK_URL = (
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html"
)
DART_SOURCE_REF = "https://opendart.fss.or.kr/"

_KRX_STOCK_ENDPOINT = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}
_KRX_INDEX_ENDPOINT = {"KOSPI": "idx/kospi_dd_trd", "KOSDAQ": "idx/kosdaq_dd_trd"}
_DART_REPORT_CODES = {"11013", "11012", "11014", "11011"}
_DART_YTD_FALLBACK = {"11013", "11011"}
_DART_BASIC_EPS_IDS = {
    "ifrs-full_BasicEarningsLossPerShare",
    "ifrs_BasicEarningsLossPerShare",
}


@dataclass(frozen=True)
class PricePoint:
    as_of: str
    code: str
    name: str
    close: float


@dataclass(frozen=True)
class IndexPoint:
    as_of: str
    name: str
    close: float


@dataclass(frozen=True)
class BetaEstimate:
    code: str
    benchmark: str
    beta: float
    observations: int
    start_date: str
    end_date: str
    method: str = "OLS simple daily returns"


@dataclass(frozen=True)
class SeriesObservation:
    time: str
    value: float
    unit: str
    name: str
    source_ref: str


@dataclass(frozen=True)
class CountryRisk:
    country: str
    as_of: str
    mature_market_erp: float
    country_risk_premium: float
    total_equity_risk_premium: float
    adjusted_default_spread: float
    corporate_tax_rate: float
    rating: str
    source_ref: str = DAMODARAN_COUNTRY_RISK_URL
    attribution: str = "Aswath Damodaran, NYU Stern"


@dataclass(frozen=True)
class DartEPS:
    corp_code: str
    business_year: str
    report_code: str
    fs_div: str
    eps: Decimal
    amount_field: str
    receipt_no: str
    source_ref: str


def _number(value: object, label: str) -> float:
    text = str(value or "").strip().replace(",", "")
    try:
        result = float(text)
    except ValueError as exc:
        raise DataCollectionError(f"{label} is not numeric: {value!r}") from exc
    if not isfinite(result):
        raise DataCollectionError(f"{label} must be finite")
    return result


def _decimal(value: object, label: str) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise DataCollectionError(f"{label} is not numeric: {value!r}") from exc
    if not result.is_finite():
        raise DataCollectionError(f"{label} must be finite")
    return result


def _iso_krx_date(value: object) -> str:
    raw = str(value or "").strip()
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise DataCollectionError(f"invalid KRX BAS_DD: {value!r}") from exc


def _outblock(payload: Mapping[str, object], label: str) -> tuple[Mapping[str, object], ...]:
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise DataCollectionError(f"{label} response is missing OutBlock_1")
    return tuple(row for row in rows if isinstance(row, Mapping))


def fetch_krx_day(
    fetch_json: FetchJson,
    *,
    auth_key: str,
    market: str,
    bas_dd: str,
    codes: Sequence[str],
    benchmark_name: str,
) -> tuple[dict[str, PricePoint], IndexPoint | None]:
    market = market.upper()
    if market not in _KRX_STOCK_ENDPOINT:
        raise ValueError("market must be KOSPI or KOSDAQ")
    if not auth_key:
        raise ValueError("KRX_AUTH_KEY is required")
    if len(bas_dd) != 8 or not bas_dd.isdigit():
        raise ValueError("bas_dd must be YYYYMMDD")
    wanted = set(codes)
    if not wanted or not benchmark_name:
        raise ValueError("codes and benchmark_name are required")

    headers = {"AUTH_KEY": auth_key}
    stock_url = f"{KRX_BASE}/{_KRX_STOCK_ENDPOINT[market]}?" + urlencode({"basDd": bas_dd})
    index_url = f"{KRX_BASE}/{_KRX_INDEX_ENDPOINT[market]}?" + urlencode({"basDd": bas_dd})
    stock = fetch_json(stock_url, headers, "KRX stock daily trading")
    index = fetch_json(index_url, headers, "KRX index daily trading")

    prices: dict[str, PricePoint] = {}
    for row in _outblock(stock, "KRX stock"):
        code = str(row.get("ISU_CD") or "").strip()
        if code not in wanted:
            continue
        if code in prices:
            raise DataCollectionError(f"duplicate KRX row for {code}/{bas_dd}")
        prices[code] = PricePoint(
            _iso_krx_date(row.get("BAS_DD")),
            code,
            str(row.get("ISU_NM") or "").strip(),
            _number(row.get("TDD_CLSPRC"), "KRX close"),
        )

    matches = [
        row
        for row in _outblock(index, "KRX index")
        if str(row.get("IDX_NM") or "").strip() == benchmark_name
    ]
    if len(matches) > 1:
        raise DataCollectionError(f"ambiguous KRX benchmark {benchmark_name}/{bas_dd}")
    benchmark = None
    if matches:
        row = matches[0]
        benchmark = IndexPoint(
            _iso_krx_date(row.get("BAS_DD")),
            benchmark_name,
            _number(row.get("CLSPRC_IDX"), "KRX index close"),
        )
    return prices, benchmark


def compute_ols_beta(
    stock_points: Sequence[PricePoint],
    benchmark_points: Sequence[IndexPoint],
    *,
    min_observations: int = 60,
) -> BetaEstimate:
    stocks = {point.as_of: point for point in stock_points}
    market = {point.as_of: point for point in benchmark_points}
    common = sorted(set(stocks).intersection(market))
    if len(common) < min_observations + 1:
        raise DataCollectionError(
            f"insufficient aligned history: need {min_observations + 1} prices, got {len(common)}"
        )
    codes = {stocks[d].code for d in common}
    benchmarks = {market[d].name for d in common}
    if len(codes) != 1 or len(benchmarks) != 1:
        raise DataCollectionError("beta input must contain one security and one benchmark")

    stock_returns = [
        stocks[cur].close / stocks[prev].close - 1.0
        for prev, cur in zip(common, common[1:])
    ]
    market_returns = [
        market[cur].close / market[prev].close - 1.0
        for prev, cur in zip(common, common[1:])
    ]
    mean_s = fmean(stock_returns)
    mean_m = fmean(market_returns)
    variance_m = sum((value - mean_m) ** 2 for value in market_returns)
    if variance_m <= 0:
        raise DataCollectionError("benchmark variance must be positive")
    covariance = sum(
        (m - mean_m) * (s - mean_s) for m, s in zip(market_returns, stock_returns)
    )
    return BetaEstimate(
        code=next(iter(codes)),
        benchmark=next(iter(benchmarks)),
        beta=covariance / variance_m,
        observations=len(stock_returns),
        start_date=common[0],
        end_date=common[-1],
    )


def collect_ecos_series(
    fetch_json: FetchJson,
    *,
    api_key: str,
    stat_code: str,
    cycle: str,
    start_time: str,
    end_time: str,
    item_code: str,
    max_rows: int = 10000,
) -> tuple[SeriesObservation, ...]:
    if not api_key:
        raise ValueError("ECOS_API_KEY is required")
    parts = [
        "https://ecos.bok.or.kr/api/StatisticSearch",
        api_key,
        "json",
        "kr",
        "1",
        str(max_rows),
        stat_code,
        cycle,
        start_time,
        end_time,
        item_code,
    ]
    payload = fetch_json("/".join(parts), {}, "BOK ECOS StatisticSearch")
    if isinstance(payload.get("RESULT"), Mapping):
        result = payload["RESULT"]
        raise DataCollectionError(
            f"ECOS returned {result.get('CODE')}: {result.get('MESSAGE')}"
        )
    container = payload.get("StatisticSearch")
    rows = container.get("row") if isinstance(container, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise DataCollectionError("ECOS response contains no rows")
    safe_ref = (
        "https://ecos.bok.or.kr/api/StatisticSearch/{API_KEY}/json/kr/"
        f"1/{max_rows}/{stat_code}/{cycle}/{start_time}/{end_time}/{item_code}"
    )
    observations = tuple(
        SeriesObservation(
            time=str(row.get("TIME") or "").strip(),
            value=_number(row.get("DATA_VALUE"), "ECOS DATA_VALUE"),
            unit=str(row.get("UNIT_NAME") or "").strip(),
            name=str(row.get("ITEM_NAME1") or "").strip(),
            source_ref=safe_ref,
        )
        for row in rows
        if isinstance(row, Mapping)
    )
    if not observations:
        raise DataCollectionError("ECOS response contains no usable rows")
    return observations


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self.row = []
        elif tag.lower() in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag.lower() == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def _pct(value: str, label: str) -> float:
    try:
        return float(value.strip().replace("%", "").replace(",", "")) / 100.0
    except ValueError as exc:
        raise DataCollectionError(f"{label} is not a percentage: {value!r}") from exc


def collect_damodaran_country_risk(
    fetch_text: FetchText,
    *,
    country: str = "Korea",
    url: str = DAMODARAN_COUNTRY_RISK_URL,
) -> CountryRisk:
    import re

    html = fetch_text(url, "Damodaran country risk")
    updated = re.search(
        r"Last\s+updated\s*:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", html, re.I
    )
    if not updated:
        raise DataCollectionError("Damodaran page is missing Last updated date")
    as_of = datetime.strptime(updated.group(1), "%B %d, %Y").date().isoformat()

    parser = _TableParser()
    parser.feed(html)
    rows = [
        row
        for row in parser.rows
        if row and row[0].strip().casefold() == country.strip().casefold()
    ]
    if len(rows) != 1 or len(rows[0]) < 6:
        raise DataCollectionError(f"Damodaran country row is missing/ambiguous for {country}")
    row = rows[0]
    total = _pct(row[2], "equity risk premium")
    country_risk = _pct(row[3], "country risk premium")
    mature = total - country_risk
    if mature < 0:
        raise DataCollectionError("mature-market ERP cannot be negative")
    return CountryRisk(
        country=row[0],
        as_of=as_of,
        mature_market_erp=mature,
        country_risk_premium=country_risk,
        total_equity_risk_premium=total,
        adjusted_default_spread=_pct(row[1], "adjusted default spread"),
        corporate_tax_rate=_pct(row[4], "corporate tax rate"),
        rating=row[5],
    )


def collect_opendart_basic_eps(
    fetch_json: FetchJson,
    *,
    api_key: str,
    corp_code: str,
    business_year: str,
    report_code: str,
    fs_div: str = "CFS",
) -> DartEPS:
    if not api_key:
        raise ValueError("DART_API_KEY is required")
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise ValueError("corp_code must be 8 digits")
    if report_code not in _DART_REPORT_CODES or fs_div not in {"CFS", "OFS"}:
        raise ValueError("unsupported report_code or fs_div")
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": business_year,
        "reprt_code": report_code,
        "fs_div": fs_div,
    }
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?" + urlencode(params)
    payload = fetch_json(url, {}, "OpenDART full financial statements")
    status = str(payload.get("status") or "").strip()
    if status and status != "000":
        raise DataCollectionError(
            f"OpenDART returned status={status} message={payload.get('message') or ''}"
        )
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise DataCollectionError("OpenDART response is missing list")

    candidates: list[tuple[Mapping[str, object], Decimal, str]] = []
    missing_ytd = False
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("corp_code") or corp_code).strip() != corp_code:
            continue
        if str(row.get("bsns_year") or "").strip() != business_year:
            continue
        if str(row.get("reprt_code") or "").strip() != report_code:
            continue
        if str(row.get("sj_div") or "").strip() not in {"IS", "CIS"}:
            continue
        if str(row.get("account_id") or "").strip() not in _DART_BASIC_EPS_IDS:
            continue
        row_fs = str(row.get("fs_div") or "").strip()
        if row_fs and row_fs != fs_div:
            continue
        if report_code in _DART_YTD_FALLBACK:
            field = (
                "thstrm_add_amount"
                if row.get("thstrm_add_amount") not in (None, "", "-")
                else "thstrm_amount"
            )
        else:
            field = "thstrm_add_amount"
            if row.get(field) in (None, "", "-"):
                missing_ytd = True
                continue
        candidates.append((row, _decimal(row.get(field), "OpenDART EPS"), field))

    if not candidates:
        if missing_ytd:
            raise DataCollectionError("Q2/Q3 EPS requires cumulative thstrm_add_amount")
        raise DataCollectionError("basic EPS XBRL account was not found")
    values = {value for _, value, _ in candidates}
    if len(values) != 1:
        raise DataCollectionError("basic EPS is ambiguous across matching XBRL rows")
    row, eps, field = candidates[0]
    receipt = str(row.get("rcept_no") or "").strip()
    source_ref = (
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        if receipt
        else DART_SOURCE_REF
    )
    return DartEPS(corp_code, business_year, report_code, fs_div, eps, field, receipt, source_ref)


def load_authorized_street_export(path: str | Path):
    """Load a user-authorized broker export; never scrape a broker/portal here."""
    from .street import StreetEstimate, StreetResearchReport

    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataCollectionError("Street export must be UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise DataCollectionError("Street export root must be an object")
    if payload.get("authorization_basis") not in {"licensed_export", "explicit_permission"}:
        raise DataCollectionError(
            "Street export requires authorization_basis=licensed_export or explicit_permission"
        )
    rows = payload.get("reports")
    if not isinstance(rows, list):
        raise DataCollectionError("Street export requires a reports list")
    # An empty authorized export supplies no eligible numeric target-price
    # references. It does not establish that sell-side coverage is absent:
    # Not Rated research or reports outside the supplied export may exist.
    # The Street stages record the unavailable comparison without inventing a target.
    if not rows:
        return ()
    reports = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise DataCollectionError("Street report must be an object")
        estimates = tuple(
            StreetEstimate(
                metric=str(item.get("metric") or ""),
                period=str(item.get("period") or ""),
                value=float(item.get("value")),
                unit=str(item.get("unit") or ""),
            )
            for item in row.get("estimates", [])
            if isinstance(item, Mapping)
        )
        reports.append(
            StreetResearchReport(
                broker=str(row.get("broker") or ""),
                analyst=str(row.get("analyst") or ""),
                published_date=str(row.get("published_date") or ""),
                target_price=float(row.get("target_price")),
                target_price_currency=str(row.get("target_price_currency") or ""),
                valuation_method=str(row.get("valuation_method") or ""),
                base_year=str(row.get("base_year") or ""),
                estimates=estimates,
                source_ref=str(row.get("source_ref") or ""),
                access_quality=str(row.get("access_quality") or "").strip(),
                argument_summary=str(row.get("argument_summary") or "").strip(),
                valuation_basis_note=str(row.get("valuation_basis_note") or "").strip(),
            )
        )
    return tuple(reports)


def street_loader_from_authorized_export(path: str | Path):
    return lambda: load_authorized_street_export(path)


def load_broker_own_metric_manifest(path: str | Path, *, freeze_token_hash: str,
                                   valuation_hash: str, assumption_set_hash: str):
    """Read source-bound own-model metrics; never accept a newly typed value.

    The manifest is a replay binding to a previously frozen model, not a claim
    that an arbitrary attachment was itself compiler-verified. The exact source
    artifact hash and pointed value must agree, as must the frozen model hashes.
    """
    from hashlib import sha256
    from .broker_comparison import parse_frozen_operating_metrics
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "broker-own-metrics/v1":
        raise DataCollectionError("unsupported broker own metric manifest")
    if payload.get("expected_valuation_hash") != valuation_hash:
        raise DataCollectionError("own metric manifest is not bound to this frozen valuation")
    if payload.get("expected_assumption_set_hash") != assumption_set_hash:
        raise DataCollectionError("own metric manifest is not bound to these frozen assumptions")
    rows = []
    for original in payload.get("metrics", []):
        row = dict(original)
        artifact = (manifest_path.parent / row["artifact_ref"]).resolve()
        raw = artifact.read_bytes()
        if sha256(raw).hexdigest() != row["artifact_sha256"]:
            raise DataCollectionError("own metric artifact content hash mismatch")
        value = json.loads(raw)
        pointer = row["json_pointer"]
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise DataCollectionError("own metric requires exact JSON pointer")
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]
        metric = row["metric"]
        if metric.get("value") is not None:
            if Decimal(str(value)) != Decimal(str(metric["value"])):
                raise DataCollectionError("own metric value differs from its source pointer")
        elif not isinstance(value, dict) or any(
            Decimal(str(value.get(bound))) != Decimal(str(metric.get(bound)))
            for bound in ("low", "high")
        ):
            raise DataCollectionError("own metric bounds differ from source pointer")
        row.update(artifact_ref=str(artifact), freeze_token_hash=freeze_token_hash,
                   valuation_hash=valuation_hash)
        rows.append(row)
    return parse_frozen_operating_metrics(rows)


def load_broker_review_context(path: str | Path):
    """Post-freeze research attachments, bytes/hash bound rather than paraphrase-only.

    These files inform host review; this loader grants no primary-source truth,
    hypothesis adoption, numeric-input or same-run mutation authority.
    """
    from hashlib import sha256
    from .host_judgment import load_judgment_json
    manifest = Path(path)
    payload = load_judgment_json(manifest.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "purpose", "artifacts"} or payload["schema_version"] != "broker-review-context/v1":
        raise DataCollectionError("unsupported broker review context manifest")
    if not isinstance(payload["artifacts"], list):
        raise DataCollectionError("broker review artifacts must be a list")
    rows = []
    identities = set()
    for row in payload["artifacts"]:
        if set(row) != {"artifact_id", "path", "sha256", "description"} or row["artifact_id"] in identities:
            raise DataCollectionError("broker context requires unique complete artifact identities")
        identities.add(row["artifact_id"])
        artifact = (manifest.parent / row["path"]).resolve()
        raw = artifact.read_bytes()
        if sha256(raw).hexdigest() != row["sha256"]:
            raise DataCollectionError("broker review research artifact hash mismatch")
        rows.append({**row, "path": str(artifact), "content_text": raw.decode("utf-8")})
    return {**payload, "artifacts": rows}
