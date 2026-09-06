#!/usr/bin/env python3
"""Operator tool: fit a target's scenario probabilities on its own history.

The expected-value gate opens only for a CALIBRATED certificate. This is the
route that gets one without borrowing another company's distribution:

    PYTHONPATH=src python scripts/build_self_calibration_artifact.py \
        --observations runs/<run>/declarations/self_history.json \
        --scenario-paths runs/<run>/declarations/scenario_driver_paths.json \
        --drivers revenue_growth,operating_margin \
        --scenarios Down,Base,Bull --path-length 5 \
        --target-ticker 068270 \
        --conditioning-json runs/<run>/declarations/conditioning.json \
        --artifact-out runs/<run>/declarations/self_artifact.json \
        --provenance-out runs/<run>/declarations/self_provenance.json

Three inputs, and which is measured and which is declared is the whole point.

* ``--observations`` is MEASURED: {"observations": [{period_end, published_at,
  values, source_ref}]} read off the target's own filed statements. It is the
  only thing the dispersion is fitted on, and it must be the target alone —
  peer rows have no place here.
* ``--scenario-paths`` is DECLARED: {"paths": {scenario_id: {driver_id:
  [...periods]}}, "rationale": "..."} — the driver path each scenario assumes,
  written by the operator, and never fitted from the same series. Fit the
  anchors from the dispersion they are scored against and the split becomes a
  constant that carries no information.
* ``--series-basis`` is DECLARED: the reporting basis every observation shares.
  A dispersion fitted across a merger, spin-off or restatement measures the
  break rather than the company, so the statement is required rather than
  assumed, and the operator must cut the series at the break themselves.

On success it prints the BindingConstants to paste into the run.yaml
``calibration:`` block, which must also carry ``self_calibrated: true`` and
name ``target_realized_dispersion_monte_carlo`` as its probability source.
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
    write_artifact_files,
)
from valuation_engine.self_calibration_factory import (  # noqa: E402
    SELF_PROBABILITY_SOURCE,
    DeclaredScenarioDriverPaths,
    build_self_calibration_artifact,
    load_target_observations,
)


def _declared_paths(path: str, drivers: tuple[str, ...]) -> DeclaredScenarioDriverPaths:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CalibrationFactoryError("scenario paths file must be a mapping")
    rows = payload.get("paths")
    if not isinstance(rows, dict):
        raise CalibrationFactoryError(
            "scenario paths file requires a paths mapping of scenario_id to "
            "the driver path that scenario assumes"
        )
    # An empty mapping is passed through rather than rejected here, so that a
    # target with too little of its own history is refused for that reason
    # first. Being told to go write scenario paths for a company that cannot
    # be calibrated at all would send the operator down the wrong road.
    return DeclaredScenarioDriverPaths(
        paths=tuple(
            (
                str(scenario_id),
                tuple(
                    (driver_id, tuple(float(x) for x in (values.get(driver_id) or ())))
                    for driver_id in drivers
                ),
            )
            for scenario_id, values in rows.items()
            if isinstance(values, dict)
        ),
        rationale=str(payload.get("rationale") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--scenario-paths", required=True)
    parser.add_argument("--drivers", required=True, help="comma-separated driver ids")
    parser.add_argument("--scenarios", default="Down,Base,Bull")
    parser.add_argument("--path-length", type=int, required=True)
    parser.add_argument("--target-ticker", required=True)
    parser.add_argument("--conditioning-json", required=True)
    parser.add_argument(
        "--series-basis",
        default="",
        help=(
            "which reporting basis every observation shares, and that no "
            "merger, spin-off or restatement divides them. Read from the "
            "observations file's series_basis key when omitted."
        ),
    )
    parser.add_argument("--artifact-out", required=True)
    parser.add_argument("--provenance-out", required=True)
    args = parser.parse_args()

    drivers = tuple(item.strip() for item in args.drivers.split(",") if item.strip())
    scenarios = tuple(item.strip() for item in args.scenarios.split(",") if item.strip())
    observations_payload = json.loads(
        Path(args.observations).read_text(encoding="utf-8")
    )
    cond_payload = json.loads(Path(args.conditioning_json).read_text(encoding="utf-8"))
    conditioning = ConditioningDeclaration(
        values=tuple(
            sorted(
                (str(k), float(v))
                for k, v in (cond_payload.get("values") or {}).items()
            )
        ),
        source_ref=str(cond_payload.get("source_ref") or ""),
        first_seen_at=str(cond_payload.get("first_seen_at") or ""),
        source_hash=str(cond_payload.get("source_hash") or ""),
    )
    try:
        result = build_self_calibration_artifact(
            observations=load_target_observations(observations_payload, drivers),
            conditioning=conditioning,
            scenarios=_declared_paths(args.scenario_paths, drivers),
            driver_ids=drivers,
            scenario_ids=scenarios,
            path_length=args.path_length,
            target_ticker=args.target_ticker,
            series_basis=(
                args.series_basis
                or str(observations_payload.get("series_basis") or "")
            ),
        )
    except CalibrationFactoryError as exc:
        # A refusal here is the answer, not an obstacle. A company without
        # enough of its own history has no measurable dispersion yet, and the
        # run reports UNCALIBRATED with this reason rather than borrowing one.
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
    print("\nrun.yaml calibration block must carry:")
    print("  self_calibrated: true")
    print(f"  external_probability_source: {SELF_PROBABILITY_SOURCE}")
    print("\nBindingConstants — paste into calibration.constants:")
    for field in (
        "expected_artifact_sha256",
        "expected_provenance_artifact_sha256",
        "expected_dataset_sha256",
        "expected_provenance_hash",
        "expected_source_row_count",
        "expected_source_company_count",
        "excluded_ticker",
    ):
        print(f"  {field}: {getattr(constants, field)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
