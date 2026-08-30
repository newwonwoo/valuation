"""Regression betas for a declared risk pack, from saved public price series.

The risk pack's peer rows demand a levered regression beta with benchmark,
observation count and window — judgments an operator must be able to ground in
public data without a terminal. This script computes them deterministically
from raw Naver fchart daily-candle XML files saved under a run's
``raw/peers/`` directory (``chart_<code>.xml`` per peer plus
``chart_KOSPI.xml``), so the numbers in ``risk_pack.yaml`` are reproducible
from committed bytes: OLS slope of daily log returns on the benchmark's, over
the overlapping window.

Usage:
    python scripts/compute_peer_betas.py runs/<run>/raw/peers [--end YYYYMMDD]
"""

from __future__ import annotations

import argparse
from math import log, sqrt
from pathlib import Path
import re
import sys

_ITEM = re.compile(r'<item data="([0-9]{8})\|([0-9.]+)\|[0-9.]+\|[0-9.]+\|([0-9.]+)\|')


def load_closes(path: Path) -> dict[str, float]:
    text = path.read_bytes().decode("euc-kr", errors="replace")
    closes: dict[str, float] = {}
    for match in _ITEM.finditer(text):
        date, _open, close = match.groups()
        closes[date] = float(close)
    return closes


def regression_beta(
    peer: dict[str, float], benchmark: dict[str, float], end: str | None
) -> tuple[float, float, int, str, str]:
    dates = sorted(set(peer) & set(benchmark))
    if end:
        dates = [d for d in dates if d <= end]
    if len(dates) < 60:
        raise ValueError(f"only {len(dates)} overlapping sessions; need >= 60")
    peer_returns = [
        log(peer[b] / peer[a]) for a, b in zip(dates, dates[1:])
    ]
    bench_returns = [
        log(benchmark[b] / benchmark[a]) for a, b in zip(dates, dates[1:])
    ]
    n = len(peer_returns)
    mean_p = sum(peer_returns) / n
    mean_b = sum(bench_returns) / n
    var_b = sum((r - mean_b) ** 2 for r in bench_returns)
    cov = sum(
        (p - mean_p) * (b - mean_b)
        for p, b in zip(peer_returns, bench_returns)
    )
    beta = cov / var_b
    residual_ss = sum(
        (p - mean_p - beta * (b - mean_b)) ** 2
        for p, b in zip(peer_returns, bench_returns)
    )
    standard_error = sqrt(residual_ss / (n - 2) / var_b)
    iso = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"  # noqa: E731
    return beta, standard_error, n, iso(dates[0]), iso(dates[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("peers_dir")
    parser.add_argument("--end", help="last session to include, YYYYMMDD")
    args = parser.parse_args()
    peers_dir = Path(args.peers_dir)
    benchmark = load_closes(peers_dir / "chart_KOSPI.xml")
    for chart in sorted(peers_dir.glob("chart_*.xml")):
        code = chart.stem.split("_", 1)[1]
        if code == "KOSPI":
            continue
        beta, se, n, start, end = regression_beta(
            load_closes(chart), benchmark, args.end
        )
        print(
            f"{code}  beta={beta:.4f}  se={se:.4f}  observations={n}"
            f"  start_date={start}  end_date={end}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
