#!/usr/bin/env python3
"""Operator tool: build a calibration artifact from a cohort dataset.

The expected-value gate opens only for a CALIBRATED certificate, and a
certificate needs an artifact fitted on resolved cohort history. This tool is
the reproducible path from dataset to artifact:

    PYTHONPATH=src python scripts/build_calibration_artifact.py \
        --dataset runs/kr_steel/cohort.json \
        --drivers revenue_growth,operating_margin \
        --scenarios Down,Core,Bull --path-length 5 \
        --exclude-ticker 104700 \
        --conditioning-json runs/kisco/conditioning.json \
        --artifact-out config/kr_steel_calibration_artifact.json \
        --provenance-out config/kr_steel_calibration_provenance.json

The dataset JSON is {"rows": [{company_id, period_end, published_at, values,
source_ref}]}; the conditioning JSON is {values, source_ref, first_seen_at,
source_hash} for the TARGET's current readings. On success it prints the
BindingConstants to paste into a ContinuousCalibrationBinding — every hash the
assembly will re-verify. Rows belonging to the excluded ticker are refused,
never dropped.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.continuous_calibration_factory import (  # noqa: E402
    CalibrationFactoryError,
    ConditioningDeclaration,
    build_continuous_calibration_artifact,
    load_cohort_dataset,
    write_artifact_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--drivers", required=True, help="comma-separated driver ids")
    parser.add_argument("--scenarios", default="Down,Core,Bull")
    parser.add_argument("--path-length", type=int, required=True)
    parser.add_argument("--exclude-ticker", required=True)
    parser.add_argument("--conditioning-json", required=True)
    parser.add_argument("--artifact-out", required=True)
    parser.add_argument("--provenance-out", required=True)
    args = parser.parse_args()

    drivers = tuple(item.strip() for item in args.drivers.split(",") if item.strip())
    scenarios = tuple(item.strip() for item in args.scenarios.split(",") if item.strip())
    cond_payload = json.loads(Path(args.conditioning_json).read_text(encoding="utf-8"))
    conditioning = ConditioningDeclaration(
        values=tuple(
            sorted((str(k), float(v)) for k, v in (cond_payload.get("values") or {}).items())
        ),
        source_ref=str(cond_payload.get("source_ref") or ""),
        first_seen_at=str(cond_payload.get("first_seen_at") or ""),
        source_hash=str(cond_payload.get("source_hash") or ""),
    )
    try:
        result = build_continuous_calibration_artifact(
            observations=load_cohort_dataset(args.dataset),
            driver_ids=drivers,
            scenario_ids=scenarios,
            path_length=args.path_length,
            excluded_ticker=args.exclude_ticker,
            conditioning=conditioning,
        )
    except CalibrationFactoryError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    write_artifact_files(
        result,
        artifact_path=args.artifact_out,
        provenance_path=args.provenance_out,
    )
    constants = result.constants
    print(f"artifact written: {args.artifact_out}")
    print(f"provenance written: {args.provenance_out}")
    print("\nBindingConstants — paste into the ContinuousCalibrationBinding:")
    for field in (
        "expected_artifact_sha256",
        "expected_provenance_artifact_sha256",
        "expected_dataset_sha256",
        "expected_provenance_hash",
        "expected_source_row_count",
        "expected_source_company_count",
        "excluded_ticker",
    ):
        print(f"  {field} = {getattr(constants, field)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
