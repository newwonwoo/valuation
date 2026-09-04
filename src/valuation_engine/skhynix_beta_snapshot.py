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
        debt = float(capital["debt"])
        equity = float(capital["equity"])
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
