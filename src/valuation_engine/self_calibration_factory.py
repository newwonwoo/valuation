"""Scenario probability from the target's own realized dispersion.

The cohort factory answers "where does this company sit inside its industry's
distribution". That question needs other companies, and it is the wrong
question: how likely 셀트리온's Bull case is depends on 셀트리온's economics,
not on how 유한양행 evolved. When the target's levels sit outside the cohort's,
nearest-scenario assignment saturates and reports near-certainty where it has
no discrimination at all — which is what borrowing a distribution looks like
when it fails loudly, and it fails quietly the rest of the time.

This factory answers a different question, from the issuer alone:

    Given how much this company's own drivers have actually moved year to
    year, where does each declared scenario's assumed driver path sit?

Two inputs, and keeping them apart is the whole design:

* **Measured** — the target's own realized driver history. An AR(1) fitted on
  its own transitions gives the persistence and the residual spread that the
  forward simulation is run with. Nothing here is a judgment.
* **Declared** — the driver path each scenario assumes. That is the operator's
  judgment, and it is an *input*, never fitted from the same series. Fitting
  the anchors from the dispersion they are scored against would make the
  answer a constant: three anchors at ±1 sigma always split the mass the same
  way, whatever the company. It would look like a probability and carry no
  information.

Because the two come from different places the output is meaningful: a Bull
case assuming growth this company has never sustained lands far out in its own
distribution and is scored low, and one assuming its ordinary year is scored
high. The target's own rows train the target's own probability, which the
cohort route forbids on purpose — there the target's presence in the panel
would let it set the reference path it is then measured against. Here there is
no reference path to corrupt: the anchors are declared, and the target trains
only the spread.

What this factory refuses rather than approximates:

* Too few of its own transitions to speak of a distribution. A company two
  years past a merger has two observations, not a spread, and the run gets a
  named refusal instead of a confident fit on n=2.
* A structural break the operator has not declared. A merger, spin-off or
  reporting-basis change makes the series two different companies; the
  declaration must say where it breaks and the fit uses only the later leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from typing import Mapping, Sequence

from .continuous_calibration_factory import (
    ARTIFACT_FORMAT_VERSION,
    REQUIRED_OOS_SPLIT_ORDER,
    BindingConstants,
    CalibrationFactoryError,
    ConditioningDeclaration,
    FactoryResult,
    _ar1,
    _canonicalize_artifact_numbers,
    _clamp,
    _mean,
    _shrink_to_positive_definite,
    _std,
    parse_timestamp,
    stable_hash,
)

#: Fewest own-company transitions a dispersion may be fitted on. An AR(1)
#: slope and a residual spread from four points is already thin; below it the
#: honest answer is that this company has no measurable distribution yet, and
#: the run says so instead of reporting a probability derived from noise.
MIN_OWN_TRANSITIONS = 5

#: Walk-forward evaluation steps held out of the fit. With one company the
#: out-of-sample test is its own later years: fit on the first k transitions,
#: predict the next, roll forward. Every prediction is made from information
#: strictly earlier than the outcome it is scored against.
REQUIRED_WALK_FORWARD_STEPS = 3

SELF_PROBABILITY_SOURCE = "target_realized_dispersion_monte_carlo"
SCENARIO_ANCHOR_POLICY = "operator_declared_scenario_driver_paths_v1"

#: There is no cohort dataset to hash. The binding still carries a dataset
#: field, so it carries a value that says plainly there is nothing there rather
#: than a hash of an empty thing that could be mistaken for one.
SELF_DATASET_SENTINEL = "no_cohort_dataset_self_calibrated"


@dataclass(frozen=True)
class TargetObservation:
    """One resolved fiscal period of the target's own filed drivers."""

    period_end: str
    published_at: str
    values: tuple[tuple[str, float], ...]
    source_ref: str

    def validate(self, driver_ids: Sequence[str]) -> None:
        if not self.period_end or not self.source_ref:
            raise CalibrationFactoryError(
                "target observation requires period_end and source_ref"
            )
        if not self.source_ref.startswith("http"):
            raise CalibrationFactoryError(
                f"target observation {self.period_end} needs an HTTP source_ref"
            )
        parse_timestamp(self.published_at, label="published_at")
        keys = tuple(driver_id for driver_id, _ in self.values)
        if set(keys) != set(driver_ids) or len(keys) != len(set(keys)):
            raise CalibrationFactoryError(
                f"target observation {self.period_end} must carry exactly the "
                "modeled drivers, once each"
            )
        for driver_id, value in self.values:
            if not math.isfinite(float(value)):
                raise CalibrationFactoryError(
                    f"target observation {self.period_end}/{driver_id} is not finite"
                )


@dataclass(frozen=True)
class DeclaredScenarioDriverPaths:
    """The driver path each scenario assumes — the operator's judgment.

    This is the half of the calculation that is declared rather than measured.
    It must be written down as numbers because the probability is exactly the
    question "how far out in this company's own distribution does that
    assumption sit"; a scenario whose assumptions stay in prose cannot be
    scored at all.
    """

    paths: tuple[tuple[str, tuple[tuple[str, tuple[float, ...]], ...]], ...]
    rationale: str

    def as_map(self) -> dict[str, dict[str, tuple[float, ...]]]:
        return {
            scenario_id: {driver_id: path for driver_id, path in rows}
            for scenario_id, rows in self.paths
        }

    def validate(
        self,
        *,
        scenario_ids: Sequence[str],
        driver_ids: Sequence[str],
        path_length: int,
    ) -> None:
        mapped = self.as_map()
        if set(mapped) != set(scenario_ids):
            raise CalibrationFactoryError(
                "declared scenario driver paths must cover exactly the "
                "declared scenarios"
            )
        if len(self.rationale.strip()) < 20:
            raise CalibrationFactoryError(
                "declared scenario driver paths require a rationale saying "
                "which filed figures the assumed paths come from"
            )
        for scenario_id, rows in mapped.items():
            if set(rows) != set(driver_ids):
                raise CalibrationFactoryError(
                    f"scenario {scenario_id} must declare every modeled driver"
                )
            for driver_id, path in rows.items():
                if len(path) != path_length:
                    raise CalibrationFactoryError(
                        f"scenario {scenario_id}/{driver_id} declares "
                        f"{len(path)} periods, not {path_length}"
                    )
                if any(not math.isfinite(float(x)) for x in path):
                    raise CalibrationFactoryError(
                        f"scenario {scenario_id}/{driver_id} path is not finite"
                    )
        # Distinct scenarios must assume distinct economics. Two scenarios with
        # the same driver path are one scenario counted twice, and the split
        # between them would be an artifact of tie-breaking.
        seen: list[tuple[str, tuple]] = []
        for scenario_id, rows in mapped.items():
            key = tuple(
                (driver_id, tuple(round(x, 12) for x in rows[driver_id]))
                for driver_id in driver_ids
            )
            for other_id, other_key in seen:
                if key == other_key:
                    raise CalibrationFactoryError(
                        f"scenarios {other_id} and {scenario_id} declare the "
                        "same driver path; they are one scenario, not two"
                    )
            seen.append((scenario_id, key))


def _ordered_series(
    observations: Sequence[TargetObservation], driver_id: str
) -> list[tuple[str, float]]:
    ordered = sorted(observations, key=lambda item: item.period_end)
    return [(item.period_end, dict(item.values)[driver_id]) for item in ordered]


def _own_transitions(
    observations: Sequence[TargetObservation], driver_id: str
) -> list[tuple[float, float]]:
    series = [value for _period, value in _ordered_series(observations, driver_id)]
    return list(zip(series, series[1:]))


def _walk_forward_skill(
    pairs: Sequence[tuple[float, float]],
) -> tuple[list[float], float, int]:
    """Skill of the AR(1) on the target's own later years.

    The fit for each step sees only transitions strictly before the one it
    predicts, so no outcome trains the model that forecasts it. Returns the
    per-step skill weights, the uncertainty inflation the errors imply, and
    the number of scored steps.
    """

    steps = REQUIRED_WALK_FORWARD_STEPS
    if len(pairs) <= steps:
        raise CalibrationFactoryError(
            f"self-calibration needs more than {steps} own transitions to hold "
            f"any out of the fit; got {len(pairs)}"
        )
    base_var = max(_std([y for _, y in pairs]) ** 2, 1e-12)
    skill: list[float] = []
    inflation: list[float] = []
    for offset in range(steps, 0, -1):
        train = pairs[: len(pairs) - offset]
        origin, outcome = pairs[len(pairs) - offset]
        intercept, slope, _ = _ar1(train)
        prediction = intercept + slope * origin
        squared = (outcome - prediction) ** 2
        skill.append(_clamp(1.0 / (1.0 + squared / base_var), 1e-6, 1.0))
        inflation.append(math.sqrt(max(squared / base_var, 1.0)))
    return skill, max(inflation), steps


def build_self_calibration_artifact(
    *,
    observations: Sequence[TargetObservation],
    conditioning: ConditioningDeclaration,
    scenarios: DeclaredScenarioDriverPaths,
    driver_ids: Sequence[str],
    scenario_ids: Sequence[str],
    path_length: int,
    target_ticker: str,
    series_basis: str,
) -> FactoryResult:
    """Fit the target's own dispersion and seal it against declared scenarios.

    ``series_basis`` names the reporting basis every observation shares — the
    operator's statement that no merger, spin-off or restatement splits the
    series in two. It is carried into the artifact and the provenance because
    a dispersion fitted across a structural break measures the break, not the
    company.
    """

    driver_ids = tuple(driver_ids)
    scenario_ids = tuple(scenario_ids)
    if len(driver_ids) < 1 or len(set(driver_ids)) != len(driver_ids):
        raise CalibrationFactoryError("driver_ids must be unique and non-empty")
    if len(scenario_ids) < 2 or len(set(scenario_ids)) != len(scenario_ids):
        raise CalibrationFactoryError("scenario_ids must be unique, at least two")
    if path_length < 1:
        raise CalibrationFactoryError("path_length must be positive")
    if not target_ticker.strip():
        raise CalibrationFactoryError("self-calibration requires the target ticker")
    if len(series_basis.strip()) < 20:
        raise CalibrationFactoryError(
            "self-calibration requires a series_basis statement: which reporting "
            "basis every observation shares, and that no structural break "
            "divides them"
        )
    for observation in observations:
        observation.validate(driver_ids)
    periods = [item.period_end for item in observations]
    if len(periods) != len(set(periods)):
        raise CalibrationFactoryError("target observations repeat a fiscal period")
    # Checked before anything downstream is asked for. A company with three
    # filed periods has two transitions, and the walk-forward hold-out, the
    # AR(1) and the residual spread would each operate on an empty or
    # single-element slice. It also comes before the scenario paths are
    # validated on purpose: an operator should not be asked to write out
    # assumed driver paths for a company that has no distribution to score
    # them against.
    if len(observations) < MIN_OWN_TRANSITIONS + 1:
        raise CalibrationFactoryError(
            f"self-calibration has {max(len(observations) - 1, 0)} own "
            f"transitions across {len(observations)} filed periods; "
            f"{MIN_OWN_TRANSITIONS} own transitions are required before this "
            "company's realized dispersion is a distribution rather than a "
            "handful of points"
        )
    conditioning.validate(driver_ids)

    scenarios.validate(
        scenario_ids=scenario_ids,
        driver_ids=driver_ids,
        path_length=path_length,
    )
    declared_paths = scenarios.as_map()

    ordered = sorted(observations, key=lambda item: item.period_end)
    # The walk-forward steps carry the same four chronological labels the
    # cohort route uses, because they mean the same thing: one frozen training
    # leg and three later periods scored against it, each predicted from
    # information published strictly before it.
    training_rows = ordered[: len(ordered) - REQUIRED_WALK_FORWARD_STEPS]
    held_out = ordered[len(ordered) - REQUIRED_WALK_FORWARD_STEPS :]
    training_latest = max(
        parse_timestamp(item.published_at, label="published_at")
        for item in training_rows
    )
    oos_window_receipts: list[dict[str, object]] = [
        {
            "split": REQUIRED_OOS_SPLIT_ORDER[0],
            "period_ends": [item.period_end for item in training_rows],
            "row_count": len(training_rows),
            "evaluation_earliest_publication_at": min(
                parse_timestamp(item.published_at, label="published_at")
                for item in training_rows
            ).isoformat().replace("+00:00", "Z"),
            "evaluation_latest_publication_at": training_latest.isoformat().replace(
                "+00:00", "Z"
            ),
        }
    ]
    for label, item in zip(REQUIRED_OOS_SPLIT_ORDER[1:], held_out, strict=True):
        published = parse_timestamp(item.published_at, label="published_at")
        if training_latest >= published:
            raise CalibrationFactoryError(
                f"{label} period {item.period_end} was published no later than "
                "the frozen training leg; the walk-forward would train on its "
                "own outcome"
            )
        oos_window_receipts.append(
            {
                "split": label,
                "period_ends": [item.period_end],
                "row_count": 1,
                "evaluation_earliest_publication_at": published.isoformat().replace(
                    "+00:00", "Z"
                ),
                "evaluation_latest_publication_at": published.isoformat().replace(
                    "+00:00", "Z"
                ),
                "training_latest_publication_at": training_latest.isoformat().replace(
                    "+00:00", "Z"
                ),
            }
        )

    drivers_payload: dict[str, dict] = {}
    residual_series: dict[str, list[float]] = {}
    for driver_id in driver_ids:
        pairs = _own_transitions(observations, driver_id)
        if len(pairs) < MIN_OWN_TRANSITIONS:
            raise CalibrationFactoryError(
                f"driver {driver_id} has {len(pairs)} own transitions; "
                f"{MIN_OWN_TRANSITIONS} are required before this company's "
                "realized dispersion is a distribution rather than two points"
            )
        intercept, slope, resid_std = _ar1(pairs)
        skill_windows, uncertainty_inflation, scored = _walk_forward_skill(pairs)
        likelihood_weight = _clamp(_mean(skill_windows), 1e-6, 1.0)

        start = float(dict(conditioning.values)[driver_id])
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
        # The standard error of a mean fitted on this few points is not a
        # rounding detail; it is most of what the run should be uncertain
        # about, so it is carried rather than smoothed away.
        sem = resid_std / math.sqrt(len(pairs))
        residual_series[driver_id] = [
            y - (intercept + slope * x) for x, y in pairs
        ]
        # Support limits for the forward draws. The cohort route reads them off
        # the panel's observed range; with one company they come off that
        # company's own realized range, widened by three spreads so the
        # simulation is free to go somewhere the company has not been while
        # still being bounded by something it filed.
        own_values = [value for _period, value in _ordered_series(observations, driver_id)]
        spread = max(_std(own_values), resid_std)
        lower_bound = min(own_values) - 3.0 * spread
        upper_bound = max(own_values) + 3.0 * spread
        drivers_payload[driver_id] = {
            "path": {
                "mean": mean_path,
                "scale": scale_path,
                "mean_uncertainty": [sem for _ in range(path_length)],
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
            },
            "posterior": {
                "mean": _mean([y for _, y in pairs]),
                "mean_strength": float(len(pairs)),
                "shape": 1.0 + len(pairs) / 2.0,
                "scale": max(
                    math.fsum(
                        (y - _mean([y2 for _, y2 in pairs])) ** 2 for _, y in pairs
                    )
                    / 2.0,
                    1e-9,
                ),
            },
            "diagnostic": {
                "skill_windows": skill_windows,
                "likelihood_weight": likelihood_weight,
                "uncertainty_inflation": uncertainty_inflation,
                "resolved_cases": len(pairs),
                "company_count": 1,
                "quarter_count": len(observations),
                "regime_similarity": 1.0,
                "walk_forward_steps": scored,
            },
            "fit": {
                "ar1_intercept": intercept,
                "ar1_slope": slope,
                "residual_std": resid_std,
                "own_transitions": len(pairs),
            },
        }

    matrix: list[list[float]] = []
    for left in driver_ids:
        row_values: list[float] = []
        for right in driver_ids:
            xs, ys = residual_series[left], residual_series[right]
            sx, sy = _std(xs), _std(ys)
            if sx <= 0 or sy <= 0:
                row_values.append(1.0 if left == right else 0.0)
                continue
            cov = _mean(
                [(x - _mean(xs)) * (y - _mean(ys)) for x, y in zip(xs, ys)]
            )
            row_values.append(_clamp(cov / (sx * sy), -0.99, 0.99))
        matrix.append(row_values)
    matrix = _shrink_to_positive_definite(matrix)

    # The lineage record is what artifact and provenance both point at, so a
    # swapped provenance file fails the same check a swapped artifact does.
    # The cohort route hashes source refs, the dataset and the withheld
    # ticker; here the inputs are the target's own filed periods and the basis
    # they share, so those are what the lineage is made of.
    lineage = {
        "source_refs": sorted({item.source_ref for item in observations}),
        "observation_periods": sorted(periods),
        "target_ticker": target_ticker,
        "series_basis": series_basis,
    }
    provenance_hash = stable_hash(lineage)

    provenance = {
        "version": ARTIFACT_FORMAT_VERSION,
        "probability_source": SELF_PROBABILITY_SOURCE,
        "self_calibrated": True,
        "target_ticker": target_ticker,
        "series_basis": series_basis,
        "source_provenance_hash": provenance_hash,
        "scenario_anchor_policy": SCENARIO_ANCHOR_POLICY,
        "scenario_path_rationale": scenarios.rationale,
        "training_latest_publication_at": training_latest.isoformat().replace(
            "+00:00", "Z"
        ),
        "oos_windows": oos_window_receipts,
        "observation_periods": sorted(periods),
        "observation_source_refs": sorted(
            {item.source_ref for item in observations}
        ),
        "current_conditioning_source_ref": conditioning.source_ref,
        "current_conditioning_source_hash": conditioning.source_hash,
        "current_conditioning_first_seen_at": conditioning.first_seen_at,
        "source_row_count": len(observations),
        "source_company_count": 1,
    }

    artifact = _canonicalize_artifact_numbers({
        "version": ARTIFACT_FORMAT_VERSION,
        "probability_source": SELF_PROBABILITY_SOURCE,
        "self_calibrated": True,
        # The cohort artifact records which ticker was withheld. A
        # self-calibrated artifact records the opposite fact, under a
        # different key, so neither can ever be read as the other.
        "target_ticker": target_ticker,
        "series_basis": series_basis,
        "scenario_anchor_policy": SCENARIO_ANCHOR_POLICY,
        "provenance_hash": provenance_hash,
        "source_row_count": len(observations),
        "source_company_count": 1,
        "oos_split_order": list(REQUIRED_OOS_SPLIT_ORDER),
        "oos_window_policy": "target_own_walk_forward_fixed_origin_v1",
        "oos_windows": oos_window_receipts,
        "drivers": drivers_payload,
        "scenarios": {
            scenario_id: {
                "driver_paths": {
                    driver_id: list(declared_paths[scenario_id][driver_id])
                    for driver_id in driver_ids
                },
                "driver_weights": {driver_id: 1.0 for driver_id in driver_ids},
            }
            for scenario_id in scenario_ids
        },
        "dependence": {
            "version": "target_own_residual_correlation_v1",
            "student_t_df": 6,
            "correlation_matrix": matrix,
        },
        "current_conditioning": {
            **{k: v for k, v in conditioning.values},
            "source_hash": conditioning.source_hash,
            "first_seen_at": conditioning.first_seen_at,
        },
    })
    artifact["artifact_sha256"] = stable_hash(
        {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    )
    return FactoryResult(
        artifact=artifact,
        provenance=provenance,
        constants=BindingConstants(
            expected_artifact_sha256=artifact["artifact_sha256"],
            expected_provenance_artifact_sha256=stable_hash(provenance),
            expected_dataset_sha256=SELF_DATASET_SENTINEL,
            expected_provenance_hash=provenance_hash,
            expected_source_row_count=len(observations),
            expected_source_company_count=1,
            excluded_ticker=target_ticker,
        ),
    )


def load_target_observations(
    payload: Mapping[str, object], driver_ids: Sequence[str]
) -> tuple[TargetObservation, ...]:
    """Read {"observations": [...]} written from the target's own filings."""
    rows = payload.get("observations")
    if not isinstance(rows, list) or not rows:
        raise CalibrationFactoryError(
            "self-calibration input requires an observations list"
        )
    result: list[TargetObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CalibrationFactoryError("each observation must be a mapping")
        values = row.get("values")
        if not isinstance(values, dict):
            raise CalibrationFactoryError("observation values must be a mapping")
        result.append(
            TargetObservation(
                period_end=str(row.get("period_end") or ""),
                published_at=str(row.get("published_at") or ""),
                values=tuple(
                    (driver_id, float(values[driver_id]))
                    for driver_id in driver_ids
                    if driver_id in values
                ),
                source_ref=str(row.get("source_ref") or ""),
            )
        )
    return tuple(result)

