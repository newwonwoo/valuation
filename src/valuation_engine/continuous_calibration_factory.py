"""The artifact factory: resolved cohort history in, calibration artifact out.

The expected-value gate is the engine's sharpest honesty device: numeric
probability weighting opens only for a CALIBRATED certificate, and a
certificate exists only downstream of a calibration artifact fitted on
resolved history. The SK hynix artifact proved the consuming side end to end —
but it was built outside this repository, so for every other cohort the chain
dead-ended at "no artifact". This module is the missing quarter: a
deterministic, reproducible builder from cohort observations to the exact
artifact + provenance contract :mod:`continuous_probability_assembly` verifies
(hash-sealed, target-excluded, knowledge-time stamped, chronological OOS).

What the factory does NOT do is as important as what it does:

- it never invents an observation — every statistic is a deterministic
  function of the rows passed in, and the dataset hash in the artifact binds
  exactly those rows;
- rows belonging to the valuation target are REFUSED, not silently dropped:
  target exclusion is a declaration the assembly re-verifies, and a dataset
  that contains the target is a broken input, not a cleaning opportunity;
- the estimators are deliberately simple and named (per-driver AR(1)
  transitions by OLS, conjugate Normal-Inverse-Gamma pooling of transition
  outcomes, chronological TRAIN/VALIDATION/HOLDOUT/FINAL_OOS splits for the
  skill diagnostics, empirical residual correlation with shrink-to-identity
  until positive definite). A richer fit can replace any of them later; the
  artifact format, the hashes and the refusals are the durable part.

Outputs: the artifact dict, the provenance dict, and the ``BindingConstants``
(every ``expected_*`` hash and count) a ``ContinuousCalibrationBinding`` needs
to consume them — printed by ``scripts/build_calibration_artifact.py`` so
wiring a new cohort is a paste, not a hunt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Mapping, Sequence

import json

from .continuous_probability_assembly import (
    ARTIFACT_FORMAT_VERSION,
    REQUIRED_OOS_SPLIT_ORDER,
    REQUIRED_OOS_WINDOWS,
    parse_timestamp,
    stable_hash,
)


class CalibrationFactoryError(ValueError):
    """Raised when a cohort dataset cannot honestly support an artifact."""


_CANONICAL_FLOAT_SIGNIFICANT_DIGITS = 15


def _canonicalize_artifact_numbers(value: object) -> object:
    """Seal computed floats at a cross-runtime-stable JSON precision.

    CPython may improve ordinary floating-point reductions between supported
    versions.  The factory therefore combines ``math.fsum`` reductions with a
    final significant-digit boundary before hashing the computed artifact.
    Source rows are intentionally excluded from this normalization so their
    dataset hash continues to attest to the exact submitted observations.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CalibrationFactoryError(
                "calibration artifact contains a non-finite computed value"
            )
        if value == 0.0:
            return 0.0
        return float(format(value, f".{_CANONICAL_FLOAT_SIGNIFICANT_DIGITS}g"))
    if isinstance(value, list):
        return [_canonicalize_artifact_numbers(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_canonicalize_artifact_numbers(item) for item in value)
    if isinstance(value, dict):
        return {
            key: _canonicalize_artifact_numbers(item)
            for key, item in value.items()
        }
    return value


@dataclass(frozen=True)
class CohortObservation:
    """One company-period reading of the modeled drivers, with provenance."""

    company_id: str
    period_end: str  # ISO date of the fiscal period this reading describes
    published_at: str  # ISO timestamp the reading became public knowledge
    values: tuple[tuple[str, float], ...]
    source_ref: str

    def validate(self, driver_ids: tuple[str, ...]) -> None:
        if not self.company_id or not self.period_end or not self.source_ref:
            raise CalibrationFactoryError(
                "cohort observation requires company, period and source_ref"
            )
        if not self.source_ref.startswith("http"):
            raise CalibrationFactoryError(
                f"cohort observation source_ref must be an HTTP link: "
                f"{self.company_id}/{self.period_end}"
            )
        parse_timestamp(self.published_at, label="cohort observation published_at")
        values = dict(self.values)
        if set(values) != set(driver_ids):
            raise CalibrationFactoryError(
                f"cohort observation {self.company_id}/{self.period_end} must "
                "carry exactly the modeled drivers"
            )
        for driver_id, value in values.items():
            if not math.isfinite(float(value)):
                raise CalibrationFactoryError(
                    f"non-finite {driver_id} for {self.company_id}/{self.period_end}"
                )


@dataclass(frozen=True)
class ConditioningDeclaration:
    """The target's own current driver readings, with knowledge-time stamps."""

    values: tuple[tuple[str, float], ...]
    source_ref: str
    first_seen_at: str
    source_hash: str

    def validate(self, driver_ids: tuple[str, ...]) -> None:
        if not self.source_ref.startswith("http") or not self.source_hash:
            raise CalibrationFactoryError(
                "conditioning requires an HTTP source_ref and a source_hash"
            )
        parse_timestamp(self.first_seen_at, label="conditioning first_seen_at")
        if set(dict(self.values)) != set(driver_ids):
            raise CalibrationFactoryError(
                "conditioning must carry exactly the modeled drivers"
            )


@dataclass(frozen=True)
class BindingConstants:
    """Everything a ContinuousCalibrationBinding must pin to consume the output."""

    expected_artifact_sha256: str
    expected_provenance_artifact_sha256: str
    expected_dataset_sha256: str
    expected_provenance_hash: str
    expected_source_row_count: int
    expected_source_company_count: int
    excluded_ticker: str


@dataclass(frozen=True)
class FactoryResult:
    artifact: dict
    provenance: dict
    constants: BindingConstants


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = _mean(values)
    return math.sqrt(
        math.fsum((v - center) ** 2 for v in values) / (len(values) - 1)
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _company_transitions(
    rows: Sequence[CohortObservation], driver_id: str
) -> list[tuple[float, float]]:
    """Consecutive same-company (x_t, x_{t+1}) pairs in period order."""
    by_company: dict[str, list[CohortObservation]] = {}
    for row in rows:
        by_company.setdefault(row.company_id, []).append(row)
    pairs: list[tuple[float, float]] = []
    for company_rows in by_company.values():
        ordered = sorted(company_rows, key=lambda item: item.period_end)
        series = [dict(item.values)[driver_id] for item in ordered]
        pairs.extend(zip(series, series[1:]))
    return pairs


def _ar1(pairs: Sequence[tuple[float, float]]) -> tuple[float, float, float]:
    """OLS AR(1): returns (intercept, slope, residual_std)."""
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    x_mean, y_mean = _mean(xs), _mean(ys)
    denom = math.fsum((x - x_mean) ** 2 for x in xs)
    slope = (
        math.fsum((x - x_mean) * (y - y_mean) for x, y in pairs) / denom
        if denom > 0
        else 0.0
    )
    slope = _clamp(slope, -0.95, 0.95)
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in pairs]
    return intercept, slope, max(_std(residuals), 1e-9)


def _chronological_splits(
    rows: Sequence[CohortObservation],
) -> tuple[tuple[CohortObservation, ...], ...]:
    ordered = sorted(rows, key=lambda item: (item.period_end, item.company_id))
    n = len(ordered)
    bounds = [0, int(n * 0.4), int(n * 0.6), int(n * 0.8), n]
    splits = tuple(
        tuple(ordered[bounds[i] : bounds[i + 1]]) for i in range(4)
    )
    if any(len(split) < 2 for split in splits):
        raise CalibrationFactoryError(
            "cohort dataset is too small for chronological "
            "TRAIN/VALIDATION/HOLDOUT/FINAL_OOS splits"
        )
    return splits


def _shrink_to_positive_definite(
    matrix: list[list[float]],
) -> list[list[float]]:
    """Shrink an empirical correlation toward identity until Cholesky-safe."""
    n = len(matrix)

    def cholesky_ok(m: list[list[float]]) -> bool:
        lower = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1):
                total = math.fsum(
                    lower[i][k] * lower[j][k] for k in range(j)
                )
                if i == j:
                    diag = m[i][i] - total
                    if diag <= 1e-10:
                        return False
                    lower[i][j] = math.sqrt(diag)
                else:
                    lower[i][j] = (m[i][j] - total) / lower[j][j]
        return True

    shrink = 0.0
    while shrink <= 0.51:
        candidate = [
            [
                (1.0 if i == j else (1.0 - shrink) * matrix[i][j])
                for j in range(n)
            ]
            for i in range(n)
        ]
        if cholesky_ok(candidate):
            return candidate
        shrink += 0.05
    raise CalibrationFactoryError(
        "residual correlation could not be shrunk to positive definite"
    )


def build_continuous_calibration_artifact(
    *,
    observations: Sequence[CohortObservation],
    driver_ids: tuple[str, ...],
    scenario_ids: tuple[str, ...],
    path_length: int,
    excluded_ticker: str,
    conditioning: ConditioningDeclaration,
    scenario_offsets: Mapping[str, float] | None = None,
    student_t_df: int = 6,
) -> FactoryResult:
    """Fit the artifact + provenance pair from resolved cohort history."""
    if not driver_ids or len(driver_ids) != len(set(driver_ids)):
        raise CalibrationFactoryError("driver_ids must be unique and non-empty")
    if len(scenario_ids) < 2 or len(scenario_ids) != len(set(scenario_ids)):
        raise CalibrationFactoryError("scenario_ids must be unique, at least two")
    if path_length < 1:
        raise CalibrationFactoryError("path_length must be positive")
    if not excluded_ticker:
        raise CalibrationFactoryError("excluded_ticker is required")
    offsets = dict(
        scenario_offsets
        if scenario_offsets is not None
        else _default_offsets(scenario_ids)
    )
    if set(offsets) != set(scenario_ids):
        raise CalibrationFactoryError("scenario_offsets must cover every scenario")

    rows = tuple(observations)
    if not rows:
        raise CalibrationFactoryError("cohort dataset is empty")
    for row in rows:
        row.validate(driver_ids)
        if row.company_id == excluded_ticker:
            raise CalibrationFactoryError(
                f"cohort dataset contains the valuation target {excluded_ticker} "
                f"({row.period_end}); target rows must never train the target's "
                "own calibration — remove them at the source, not here"
            )
    conditioning.validate(driver_ids)

    companies = sorted({row.company_id for row in rows})
    if len(companies) < 5:
        raise CalibrationFactoryError(
            "cohort breadth is insufficient: at least 5 distinct companies are "
            f"required, got {len(companies)}"
        )

    dataset_rows = [
        {
            "company_id": row.company_id,
            "period_end": row.period_end,
            "published_at": row.published_at,
            "values": {k: v for k, v in sorted(row.values)},
            "source_ref": row.source_ref,
        }
        for row in sorted(rows, key=lambda r: (r.period_end, r.company_id))
    ]
    dataset_sha256 = stable_hash({"rows": dataset_rows, "drivers": list(driver_ids)})
    provenance_hash = stable_hash(
        {
            "source_refs": sorted({row.source_ref for row in rows}),
            "dataset_sha256": dataset_sha256,
            "excluded_ticker": excluded_ticker,
        }
    )
    training_latest = max(
        parse_timestamp(row.published_at, label="published_at") for row in rows
    )

    splits = _chronological_splits(rows)
    train_rows = splits[0]

    drivers_payload: dict[str, dict] = {}
    residual_series: dict[str, list[float]] = {}
    for driver_id in driver_ids:
        pairs = _company_transitions(rows, driver_id)
        if len(pairs) < 8:
            raise CalibrationFactoryError(
                f"driver {driver_id} has too few resolved transitions "
                f"({len(pairs)}); a calibration cannot stand on them"
            )
        intercept, slope, resid_std = _ar1(pairs)

        # Chronological OOS skill: fit on TRAIN only, score each later split.
        train_pairs = _company_transitions(train_rows, driver_id)
        if len(train_pairs) < 4:
            raise CalibrationFactoryError(
                f"driver {driver_id} lacks TRAIN-split transitions"
            )
        t_intercept, t_slope, t_resid = _ar1(train_pairs)
        base_var = max(_std([y for _, y in train_pairs]) ** 2, 1e-12)
        skill_windows: list[float] = []
        inflation_candidates: list[float] = []
        for split in splits[1:]:
            split_pairs = _company_transitions(split, driver_id)
            if not split_pairs:
                skill_windows.append(0.5)
                continue
            mse = _mean(
                [
                    (y - (t_intercept + t_slope * x)) ** 2
                    for x, y in split_pairs
                ]
            )
            skill_windows.append(_clamp(1.0 / (1.0 + mse / base_var), 1e-6, 1.0))
            inflation_candidates.append(math.sqrt(max(mse / base_var, 1.0)))
        assert len(skill_windows) == REQUIRED_OOS_WINDOWS
        likelihood_weight = _clamp(_mean(skill_windows), 1e-6, 1.0)
        uncertainty_inflation = max(inflation_candidates or [1.0])

        # Path: iterate the AR(1) from the cohort's latest cross-section.
        latest_by_company: dict[str, tuple[str, float]] = {}
        for row in rows:
            value = dict(row.values)[driver_id]
            prior = latest_by_company.get(row.company_id)
            if prior is None or row.period_end > prior[0]:
                latest_by_company[row.company_id] = (row.period_end, value)
        start = _mean([value for _, value in latest_by_company.values()])
        mean_path: list[float] = []
        level = start
        for _ in range(path_length):
            level = intercept + slope * level
            mean_path.append(level)
        stationary_std = resid_std / math.sqrt(max(1.0 - slope**2, 0.05))
        scale_path = [
            min(
                resid_std
                * math.sqrt(math.fsum(slope ** (2 * i) for i in range(k + 1))),
                stationary_std,
            )
            * uncertainty_inflation
            for k in range(path_length)
        ]
        sem = resid_std / math.sqrt(len(pairs))
        mean_uncertainty_path = [sem] * path_length
        all_values = [dict(row.values)[driver_id] for row in rows]
        spread = max(_std(all_values), resid_std)
        lower_bound = min(all_values) - 3.0 * spread
        upper_bound = max(all_values) + 3.0 * spread

        # Conjugate NIG pooling of transition outcomes (documented, simple).
        outcomes = [y for _, y in pairs]
        n = len(outcomes)
        nig_mean = _mean(outcomes)
        nig_strength = float(n)
        nig_shape = 1.0 + n / 2.0
        nig_scale = max(
            math.fsum((y - nig_mean) ** 2 for y in outcomes) / 2.0, 1e-9
        )

        recent = [dict(row.values)[driver_id] for row in splits[-1]]
        regime_similarity = _clamp(
            1.0 - abs(_mean(recent) - _mean(all_values)) / (spread * 3.0),
            0.0,
            1.0,
        )

        drivers_payload[driver_id] = {
            "path": {
                "mean": mean_path,
                "scale": scale_path,
                "mean_uncertainty": mean_uncertainty_path,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
            },
            "posterior": {
                "mean": nig_mean,
                "mean_strength": nig_strength,
                "shape": nig_shape,
                "scale": nig_scale,
            },
            "transition": {
                "intercept": intercept,
                "slope": slope,
                "residual_std": resid_std,
                "x_bounds": [min(x for x, _ in pairs), max(x for x, _ in pairs)],
                "y_bounds": [min(outcomes), max(outcomes)],
            },
            "diagnostic": {
                "skill_windows": skill_windows,
                "likelihood_weight": likelihood_weight,
                "uncertainty_inflation": uncertainty_inflation,
                "resolved_cases": len(pairs),
                "company_count": len(
                    {row.company_id for row in rows}
                ),
                "quarter_count": len({row.period_end for row in rows}),
                "regime_similarity": regime_similarity,
            },
        }
        residual_series[driver_id] = [
            y - (intercept + slope * x) for x, y in pairs
        ]

    # Cross-driver residual correlation on the common transition count.
    common = min(len(series) for series in residual_series.values())
    matrix: list[list[float]] = []
    for a in driver_ids:
        row_values: list[float] = []
        for b in driver_ids:
            if a == b:
                row_values.append(1.0)
                continue
            xs = residual_series[a][:common]
            ys = residual_series[b][:common]
            sx, sy = _std(xs), _std(ys)
            if sx <= 0 or sy <= 0:
                row_values.append(0.0)
                continue
            cov = _mean(
                [
                    (x - _mean(xs)) * (y - _mean(ys))
                    for x, y in zip(xs, ys)
                ]
            )
            row_values.append(_clamp(cov / (sx * sy), -0.99, 0.99))
        matrix.append(row_values)
    matrix = _shrink_to_positive_definite(matrix)

    scenarios_payload = {
        scenario_id: {
            "driver_paths": {
                driver_id: [
                    drivers_payload[driver_id]["path"]["mean"][k]
                    + offsets[scenario_id]
                    * drivers_payload[driver_id]["path"]["scale"][k]
                    for k in range(path_length)
                ]
                for driver_id in driver_ids
            },
            "driver_weights": {driver_id: 1.0 for driver_id in driver_ids},
        }
        for scenario_id in scenario_ids
    }

    artifact = _canonicalize_artifact_numbers({
        "version": ARTIFACT_FORMAT_VERSION,
        "source_dataset_sha256": dataset_sha256,
        "provenance_hash": provenance_hash,
        "source_row_count": len(rows),
        "source_company_count": len(companies),
        "target_ticker_excluded": excluded_ticker,
        "oos_split_order": list(REQUIRED_OOS_SPLIT_ORDER),
        "current_conditioning": {
            **{k: v for k, v in conditioning.values},
            "source_hash": conditioning.source_hash,
            "first_seen_at": conditioning.first_seen_at,
        },
        "drivers": drivers_payload,
        "dependence": {
            "version": "empirical_residual_corr_v1",
            "correlation_matrix": matrix,
            "student_t_df": student_t_df,
            "complete_case_count": common,
        },
        "scenarios": scenarios_payload,
    })
    assert isinstance(artifact, dict)
    artifact["artifact_sha256"] = stable_hash(
        {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    )

    provenance: dict = {
        "version": ARTIFACT_FORMAT_VERSION,
        "source_dataset_sha256": dataset_sha256,
        "source_provenance_hash": provenance_hash,
        "target_ticker_excluded": excluded_ticker,
        "training_latest_publication_at": training_latest.isoformat().replace(
            "+00:00", "Z"
        ),
        "current_conditioning_source_ref": conditioning.source_ref,
        "current_conditioning_source_hash": conditioning.source_hash,
        "current_conditioning_first_seen_at": conditioning.first_seen_at,
        "source_row_count": len(rows),
        "source_company_count": len(companies),
    }

    constants = BindingConstants(
        expected_artifact_sha256=artifact["artifact_sha256"],
        expected_provenance_artifact_sha256=stable_hash(provenance),
        expected_dataset_sha256=dataset_sha256,
        expected_provenance_hash=provenance_hash,
        expected_source_row_count=len(rows),
        expected_source_company_count=len(companies),
        excluded_ticker=excluded_ticker,
    )
    return FactoryResult(artifact=artifact, provenance=provenance, constants=constants)


def _default_offsets(scenario_ids: tuple[str, ...]) -> dict[str, float]:
    if len(scenario_ids) == 3:
        low, mid, high = scenario_ids
        return {low: -1.0, mid: 0.0, high: 1.0}
    raise CalibrationFactoryError(
        "scenario_offsets are required unless exactly three scenarios are "
        "declared (interpreted as low/central/high)"
    )


def write_artifact_files(
    result: FactoryResult,
    *,
    artifact_path: str | Path,
    provenance_path: str | Path,
) -> None:
    Path(artifact_path).write_text(
        json.dumps(result.artifact, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    Path(provenance_path).write_text(
        json.dumps(result.provenance, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def load_cohort_dataset(path: str | Path) -> tuple[CohortObservation, ...]:
    """Read a cohort dataset JSON: {"rows": [{company_id, period_end, published_at, values, source_ref}]}"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise CalibrationFactoryError("cohort dataset requires a rows list")
    return tuple(
        CohortObservation(
            company_id=str(row.get("company_id") or ""),
            period_end=str(row.get("period_end") or ""),
            published_at=str(row.get("published_at") or ""),
            values=tuple(sorted((str(k), float(v)) for k, v in (row.get("values") or {}).items())),
            source_ref=str(row.get("source_ref") or ""),
        )
        for row in rows
    )
