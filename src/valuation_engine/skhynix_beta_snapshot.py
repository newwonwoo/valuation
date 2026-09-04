from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import fsum, isclose, sqrt
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BETA_SNAPSHOT_PATH = _REPO_ROOT / "config" / "skhynix_beta_snapshot.json"
PEER_IDS = ("INTC", "AVGO", "MRVL", "MU")
BENCHMARK_ID = "COMP"
_PEER_CAPITAL_FACT_BINDINGS = {
    "INTC": {
        "company_facts_source_ref": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000050863.json",
        "company_facts_raw_sha256": "70056711b8b04d3a27e4a42b6d86e26f85ddaa3d59e480039d66ccfb353eb0b8",
        "capital_facts_sha256": "23b53b019310070bd4caed5697dc221c197a19fc145ea2af50ace75312328a4f",
    },
    "AVGO": {
        "company_facts_source_ref": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001730168.json",
        "company_facts_raw_sha256": "b6399ff9bc3e4047dfe955ad7866ca51208a9f617457e7b917cbfc2effcbc916",
        "capital_facts_sha256": "750aef77aa9736be41004075d9e99ac885274245e2fe7537e135cfca4ab6fab6",
    },
    "MRVL": {
        "company_facts_source_ref": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001835632.json",
        "company_facts_raw_sha256": "2190d7525c2749e96636b0f95376c5f7d73d9cb47a7452b3a94f70e156ec1169",
        "capital_facts_sha256": "42ab5df203294ff9932f10b945ce3188913c350e45b700b2f079601b06156485",
    },
    "MU": {
        "company_facts_source_ref": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000723125.json",
        "company_facts_raw_sha256": "a8b088c2111daef36536e53c81fef4c0f01220c64933e126a665b00a9257f882",
        "capital_facts_sha256": "bc2b5dc24bc003443ea32ce72f1e29b19ff74e972ad4cb3ce08fe7bd9d82ba1b",
    },
}


@dataclass(frozen=True)
class FrozenBetaEstimate:
    peer_id: str
    beta: float
    standard_error: float
    observations: int
    start_date: str
    end_date: str
    series_hash: str
    debt: float
    book_debt_to_equity: float
    ending_price: float
    price_source_ref: str
    capital_source_ref: str


@dataclass(frozen=True)
class SKHynixBetaSnapshot:
    as_of: str
    benchmark_id: str
    benchmark_source_ref: str
    estimates: tuple[FrozenBetaEstimate, ...]
    raw_hash: str

    def estimate(self, peer_id: str) -> FrozenBetaEstimate:
        try:
            return next(item for item in self.estimates if item.peer_id == peer_id)
        except StopIteration as exc:
            raise KeyError(peer_id) from exc


def _canonical_points(rows: object, *, label: str) -> tuple[tuple[str, float], ...]:
    if not isinstance(rows, list) or len(rows) < 200:
        raise ValueError(f"{label} weekly price history is missing or too short")
    points: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"{label} price point must be [date, close]")
        date, close = str(row[0]), float(row[1])
        if not date or close <= 0:
            raise ValueError(f"{label} price point is invalid")
        points.append((date, close))
    if tuple(date for date, _ in points) != tuple(sorted(date for date, _ in points)):
        raise ValueError(f"{label} weekly price history must be date-sorted")
    if len({date for date, _ in points}) != len(points):
        raise ValueError(f"{label} weekly price history contains duplicate dates")
    return tuple(points)


def calculate_beta(
    stock_points: tuple[tuple[str, float], ...],
    benchmark_points: tuple[tuple[str, float], ...],
) -> dict[str, object]:
    stock = dict(stock_points)
    benchmark = dict(benchmark_points)
    dates = tuple(sorted(set(stock).intersection(benchmark)))
    if len(dates) < 201:
        raise ValueError("aligned weekly price history is too short")
    stock_returns = tuple(
        stock[current] / stock[previous] - 1.0
        for previous, current in zip(dates, dates[1:])
    )
    benchmark_returns = tuple(
        benchmark[current] / benchmark[previous] - 1.0
        for previous, current in zip(dates, dates[1:])
    )
    observations = len(stock_returns)
    mean_stock = fsum(stock_returns) / observations
    mean_benchmark = fsum(benchmark_returns) / observations
    centered_benchmark = tuple(
        value - mean_benchmark for value in benchmark_returns
    )
    centered_stock = tuple(value - mean_stock for value in stock_returns)
    sxx = fsum(value * value for value in centered_benchmark)
    if sxx <= 0:
        raise ValueError("benchmark weekly-return variance is zero")
    beta = fsum(
        x_value * y_value
        for x_value, y_value in zip(centered_benchmark, centered_stock)
    ) / sxx
    alpha = mean_stock - beta * mean_benchmark
    residuals = tuple(
        y_value - alpha - beta * x_value
        for x_value, y_value in zip(benchmark_returns, stock_returns)
    )
    standard_error = sqrt(
        (fsum(value * value for value in residuals) / (observations - 2)) / sxx
    )
    total_variance = fsum(value * value for value in centered_stock)
    r_squared = (
        1.0 - fsum(value * value for value in residuals) / total_variance
        if total_variance > 0
        else 0.0
    )
    canonical = [
        [date, format(stock[date], ".12g"), format(benchmark[date], ".12g")]
        for date in dates
    ]
    return {
        "beta": beta,
        "standard_error": standard_error,
        "alpha": alpha,
        "r_squared": r_squared,
        "observations": observations,
        "start_date": dates[1],
        "end_date": dates[-1],
        "series_hash": sha256(
            json.dumps(canonical, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def load_skhynix_beta_snapshot(
    path: str | Path | None = None,
) -> SKHynixBetaSnapshot:
    resolved = Path(path or DEFAULT_BETA_SNAPSHOT_PATH)
    raw = resolved.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("contract") != "skhynix_beta_snapshot/v1":
        raise ValueError("SK hynix Beta snapshot contract mismatch")
    if payload.get("benchmark_id") != BENCHMARK_ID:
        raise ValueError("SK hynix Beta benchmark mismatch")
    benchmark = _canonical_points(
        payload.get("benchmark_weekly_close"),
        label=BENCHMARK_ID,
    )
    peer_rows = payload.get("peers")
    if not isinstance(peer_rows, dict) or tuple(peer_rows) != PEER_IDS:
        raise ValueError("SK hynix Beta snapshot peers must be INTC/AVGO/MRVL/MU")

    estimates: list[FrozenBetaEstimate] = []
    for peer_id in PEER_IDS:
        row = peer_rows[peer_id]
        if not isinstance(row, dict):
            raise ValueError(f"{peer_id} Beta row must be a mapping")
        stock_points = _canonical_points(row.get("weekly_close"), label=peer_id)
        calculated = calculate_beta(stock_points, benchmark)
        frozen = row.get("ols")
        capital = row.get("capital")
        if not isinstance(frozen, dict) or not isinstance(capital, dict):
            raise ValueError(f"{peer_id} Beta row lacks OLS or capital binding")
        for key in ("beta", "standard_error", "alpha", "r_squared"):
            if not isclose(
                float(frozen[key]),
                float(calculated[key]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{peer_id} frozen {key} does not replay")
        for key in ("observations", "start_date", "end_date", "series_hash"):
            if frozen[key] != calculated[key]:
                raise ValueError(f"{peer_id} frozen {key} does not replay")
        fact_binding = _PEER_CAPITAL_FACT_BINDINGS[peer_id]
        if any(
            capital.get(key) != expected
            for key, expected in fact_binding.items()
            if key != "capital_facts_sha256"
        ):
            raise ValueError(f"{peer_id} SEC company-facts source binding mismatch")
        debt_facts = capital.get("debt_facts")
        equity_fact = capital.get("equity_fact")
        if (
            not isinstance(debt_facts, list)
            or not debt_facts
            or not all(isinstance(item, dict) for item in debt_facts)
            or not isinstance(equity_fact, dict)
        ):
            raise ValueError(f"{peer_id} SEC capital fact records are missing")
        fact_payload = {
            "debt_facts": debt_facts,
            "equity_fact": equity_fact,
        }
        fact_hash = sha256(
            json.dumps(
                fact_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if fact_hash != fact_binding["capital_facts_sha256"]:
            raise ValueError(f"{peer_id} SEC capital fact records are not registered")
        debt = fsum(float(item["value"]) for item in debt_facts)
        equity = float(equity_fact["value"])
        if not isclose(
            float(capital["debt"]), debt, rel_tol=0.0, abs_tol=1e-9
        ) or not isclose(
            float(capital["equity"]), equity, rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError(f"{peer_id} SEC capital totals do not replay")
        ratio = float(capital["debt_to_equity"])
        if debt < 0 or equity <= 0 or not isclose(
            debt / equity,
            ratio,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"{peer_id} debt/equity binding does not replay")
        price_source_ref = str(row.get("price_source_ref", ""))
        capital_source_ref = str(capital.get("filing_source_ref", ""))
        if "api.nasdaq.com/api/quote/" not in price_source_ref:
            raise ValueError(f"{peer_id} price source is not the frozen Nasdaq query")
        if "sec.gov/Archives/edgar/data/" not in capital_source_ref:
            raise ValueError(f"{peer_id} capital source is not an original SEC filing")
        estimates.append(
            FrozenBetaEstimate(
                peer_id=peer_id,
                beta=float(frozen["beta"]),
                standard_error=float(frozen["standard_error"]),
                observations=int(frozen["observations"]),
                start_date=str(frozen["start_date"]),
                end_date=str(frozen["end_date"]),
                series_hash=str(frozen["series_hash"]),
                debt=debt,
                book_debt_to_equity=ratio,
                ending_price=stock_points[-1][1],
                price_source_ref=price_source_ref,
                capital_source_ref=capital_source_ref,
            )
        )

    benchmark_source_ref = str(payload.get("benchmark_source_ref", ""))
    if "api.nasdaq.com/api/quote/COMP/historical" not in benchmark_source_ref:
        raise ValueError("SK hynix benchmark source is not the frozen Nasdaq query")
    return SKHynixBetaSnapshot(
        as_of=str(payload["as_of"]),
        benchmark_id=BENCHMARK_ID,
        benchmark_source_ref=benchmark_source_ref,
        estimates=tuple(estimates),
        raw_hash=sha256(raw).hexdigest(),
    )
