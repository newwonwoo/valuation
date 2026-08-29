#!/usr/bin/env python3
"""Operator tool: check a KOSIS-style series before marking it verified.

`config/kr_industry_series_registry.yaml` ships with zero verified rows: an
industry series collects into Evidence only after a human has confirmed it
against the source catalog (table identity, unit, cadence). This tool is that
step. It fetches the candidate series, parses the observations the collector
would parse, and prints a ready-to-paste registry row with ``verified: false``,
so the operator reviews real numbers before flipping the flag.

Usage:

    export KOSIS_API_KEY=...
    PYTHONPATH=src python scripts/verify_industry_series.py \
        --url 'https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList&apiKey=REPLACE&orgId=...&tblId=...&format=json&jsonVD=Y' \
        --metric benchmark_price --unit dimensionless \
        --definition-id DEF_PPI_STEEL --series-id KR_KOSIS_PPI_STEEL_V1

The URL is fetched verbatim (with its credential); the printed template carries
the redacted ``{api_key}`` form, never the live key. Nothing is written to the
registry — flipping ``verified: true`` stays a deliberate human edit.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.industry_series_collector import (  # noqa: E402
    INDUSTRY_OBSERVABLE_METRICS,
    IndustrySeriesError,
    _period_end,
)
from valuation_engine.live_indexers import (  # noqa: E402
    HttpTransport,
    parse_json_response,
    parse_kosis_series_values,
)


def _redact(url: str, api_key: str | None) -> str:
    if api_key:
        return url.replace(api_key, "{api_key}")
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full statisticsParameterData URL")
    parser.add_argument("--metric", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--source-id", default="KR_KOSIS_API")
    parser.add_argument("--definition-id", required=True)
    parser.add_argument("--geography", default="KR")
    parser.add_argument("--layer", default="authorized_market_data")
    parser.add_argument("--api-key-env", default="KOSIS_API_KEY")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.metric not in INDUSTRY_OBSERVABLE_METRICS:
        raise SystemExit(
            f"metric {args.metric!r} is not an industry-observable metric; the "
            "definition gate refuses company-realized quantities. Allowed: "
            + ", ".join(sorted(INDUSTRY_OBSERVABLE_METRICS))
        )

    api_key = os.environ.get(args.api_key_env, "")
    transport = HttpTransport(timeout_seconds=args.timeout)
    rows = parse_json_response(transport.get_text(args.url).text)
    observations = parse_kosis_series_values(rows)
    if not observations:
        raise SystemExit(
            "no parseable (period, value) observations — check tblId/itmId and "
            "that the table returns PRD_DE/DT fields"
        )

    print(f"# parsed {len(observations)} observations for {args.series_id}")
    for period, value in observations[-8:]:
        try:
            effective = _period_end(period)
        except IndustrySeriesError:
            effective = "?"
        print(f"#   {period} -> {value}  (effective {effective})")
    print("#")
    print("# Review the numbers against the KOSIS catalog, then paste this row")
    print("# into config/kr_industry_series_registry.yaml and set verified: true.")
    print("  - series_id: " + args.series_id)
    print("    source_id: " + args.source_id)
    print("    metric: " + args.metric)
    print("    layer: " + args.layer)
    print("    unit: " + args.unit)
    print("    geography: " + args.geography)
    print("    definition_id: " + args.definition_id)
    print("    definition: >-")
    print("      TODO operator: one sentence naming the exact KOSIS table, item")
    print("      and index base, and stating this is an industry observable, not")
    print("      a company-realized figure.")
    print("    url_template: " + _redact(args.url, api_key))
    print("    api_key_env: " + args.api_key_env)
    print("    verified: false  # flip to true only after catalog review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
