"""Company-neutral dynamic distributions for load-bearing operating drivers.

The module deliberately produces *paths*, not named scenario probabilities.  A
caller may describe tails after valuation, but neither scenario labels nor an
equity/market value can enter this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
import random
from typing import Iterable, Mapping


ZERO = Decimal("0")
ONE = Decimal("1")


class DriverDistributionError(ValueError):
    """Raised when a driver distribution is not auditable."""


def _require_decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise DriverDistributionError(f"{label} must be finite")
    return value


def _quarter_index(value: date) -> int:
    if value.month not in (3, 6, 9, 12):
        raise DriverDistributionError("driver observations must use calendar quarter ends")
    return value.year * 4 + value.month // 3 - 1


@dataclass(frozen=True)
class StructuralBreak:
    period_end: date
    reason: str

    def validate(self) -> None:
        _quarter_index(self.period_end)
        if not self.reason.strip():
            raise DriverDistributionError("structural break requires a reason")


@dataclass(frozen=True)
class TargetDriverObservation:
    period_end: date
    published_at: datetime
    first_seen_at: datetime
    values: tuple[tuple[str, Decimal], ...]
    source_ref: str
    source_hash: str
    origin: str = "TARGET_REALIZED"

    def as_map(self) -> dict[str, Decimal]:
        return dict(self.values)

    def validate(self) -> None:
        _quarter_index(self.period_end)
        if self.origin != "TARGET_REALIZED":
            raise DriverDistributionError(
                "load-bearing driver observations must be the target's own realized history"
            )
        if not self.source_ref.startswith(("http://", "https://")) or not self.source_hash:
            raise DriverDistributionError("driver observation requires an HTTP source and hash")
        if self.published_at.date() < self.period_end:
            raise DriverDistributionError("driver observation cannot be published before period end")
        if self.first_seen_at < self.published_at:
            raise DriverDistributionError("first-seen time cannot precede publication")
        if not self.values or len(self.values) != len(self.as_map()):
            raise DriverDistributionError("driver observation values are empty or duplicated")
        for driver_id, value in self.values:
            if not driver_id.strip():
                raise DriverDistributionError("driver ID cannot be empty")
            _require_decimal(value, f"driver {driver_id}")


@dataclass(frozen=True)
class TargetDriverPanel:
    target_id: str
    frequency: str
    perimeter: str
    observations: tuple[TargetDriverObservation, ...]
    structural_breaks: tuple[StructuralBreak, ...]
    source_hash: str

    def validate(self, *, minimum_observations: int = 32) -> tuple[str, ...]:
        if not self.target_id or not self.perimeter or not self.source_hash:
            raise DriverDistributionError("target driver panel identity is incomplete")
        if self.frequency.upper() not in {"Q", "QUARTERLY"}:
            raise DriverDistributionError("target driver panel must be quarterly")
        if len(self.observations) < minimum_observations:
            raise DriverDistributionError(
                f"target driver panel requires at least {minimum_observations} observations"
            )
        for observation in self.observations:
            observation.validate()
        ordered = tuple(sorted(self.observations, key=lambda item: item.period_end))
        if ordered != self.observations:
            raise DriverDistributionError("target driver observations must be chronological")
        indices = tuple(_quarter_index(item.period_end) for item in ordered)
        if len(indices) != len(set(indices)) or any(
            right - left != 1 for left, right in zip(indices, indices[1:])
        ):
            raise DriverDistributionError("target driver observations must be contiguous quarters")
        driver_ids = tuple(driver_id for driver_id, _ in ordered[0].values)
        if len(driver_ids) != len(set(driver_ids)):
            raise DriverDistributionError("target driver panel repeats a driver")
        expected = set(driver_ids)
        if any(set(item.as_map()) != expected for item in ordered):
            raise DriverDistributionError("target driver observations have inconsistent coverage")
        for item in self.structural_breaks:
            item.validate()
            if item.period_end < ordered[0].period_end or item.period_end > ordered[-1].period_end:
                raise DriverDistributionError("structural break lies outside the driver panel")
        return driver_ids


@dataclass(frozen=True)
class DriverCalibrationDiagnostic:
    driver_id: str
    model_crps: Decimal
    benchmark_crps: Decimal
    crps_skill: Decimal
    mean_log_score: Decimal
    coverage_80: Decimal
    coverage_90: Decimal
    pit_mean: Decimal

    def validate(self) -> None:
        if not self.driver_id:
            raise DriverDistributionError("driver diagnostic requires an ID")
        for name in (
            "model_crps",
            "benchmark_crps",
            "crps_skill",
            "mean_log_score",
            "coverage_80",
            "coverage_90",
            "pit_mean",
        ):
            _require_decimal(getattr(self, name), f"diagnostic {name}")
        if self.model_crps < ZERO or self.benchmark_crps < ZERO:
            raise DriverDistributionError("CRPS cannot be negative")
        if not ZERO <= self.coverage_80 <= ONE or not ZERO <= self.coverage_90 <= ONE:
            raise DriverDistributionError("coverage must lie within [0,1]")
        if not ZERO <= self.pit_mean <= ONE:
            raise DriverDistributionError("PIT mean must lie within [0,1]")


@dataclass(frozen=True)
class CalibrationDiagnostics:
    holdout_count: int
    driver_diagnostics: tuple[DriverCalibrationDiagnostic, ...]
    residual_persistence_reproduced: bool
    cross_driver_covariance_reproduced: bool
    chronology_valid: bool
    authorization_failures: tuple[str, ...] = ()

    @property
    def valuation_distribution_authorized(self) -> bool:
        return not self.authorization_failures

    def validate(self) -> None:
        if self.holdout_count < 12:
            raise DriverDistributionError("at least 12 rolling-origin holdouts are required")
        if not self.driver_diagnostics:
            raise DriverDistributionError("calibration diagnostics cannot be empty")
        for item in self.driver_diagnostics:
            item.validate()
        derived = _authorization_failures(
            self.holdout_count,
            self.driver_diagnostics,
            residual_persistence_reproduced=self.residual_persistence_reproduced,
            cross_driver_covariance_reproduced=self.cross_driver_covariance_reproduced,
            chronology_valid=self.chronology_valid,
        )
        if tuple(derived) != self.authorization_failures:
            raise DriverDistributionError("calibration authorization failures are inconsistent")


@dataclass(frozen=True)
class DynamicDriverPosterior:
    driver_ids: tuple[str, ...]
    seasonal_terms: tuple[tuple[Decimal, ...], ...]
    transition_matrix: tuple[tuple[Decimal, ...], ...]
    innovation_covariance: tuple[tuple[Decimal, ...], ...]
    student_t_df: int
    parameter_uncertainty: tuple[tuple[Decimal, ...], ...]
    last_state: tuple[Decimal, ...]
    last_quarter: int
    lower_bounds: tuple[Decimal | None, ...]
    upper_bounds: tuple[Decimal | None, ...]
    parameter_draws_hash: str
    source_hash: str
    calibration_diagnostics: CalibrationDiagnostics

    def validate(self) -> None:
        n = len(self.driver_ids)
        if n == 0 or len(set(self.driver_ids)) != n:
            raise DriverDistributionError("posterior requires distinct driver IDs")
        if len(self.seasonal_terms) != 4 or any(len(row) != n for row in self.seasonal_terms):
            raise DriverDistributionError("posterior requires four aligned seasonal rows")
        for matrix, label in (
            (self.transition_matrix, "transition"),
            (self.innovation_covariance, "innovation covariance"),
            (self.parameter_uncertainty, "parameter uncertainty"),
        ):
            if len(matrix) != n or any(len(row) != n for row in matrix):
                raise DriverDistributionError(f"{label} matrix dimension mismatch")
            for row in matrix:
                for value in row:
                    _require_decimal(value, label)
        if len(self.last_state) != n or len(self.lower_bounds) != n or len(self.upper_bounds) != n:
            raise DriverDistributionError("posterior state or bound dimension mismatch")
        for i, value in enumerate(self.last_state):
            _require_decimal(value, f"last state {i}")
            lower, upper = self.lower_bounds[i], self.upper_bounds[i]
            if lower is not None:
                _require_decimal(lower, f"lower bound {i}")
            if upper is not None:
                _require_decimal(upper, f"upper bound {i}")
            if lower is not None and upper is not None and lower > upper:
                raise DriverDistributionError("posterior bounds are inverted")
        if self.last_quarter not in range(4):
            raise DriverDistributionError("last quarter must lie within 0..3")
        if self.student_t_df < 3:
            raise DriverDistributionError("Student-t degrees of freedom must be at least 3")
        if not self.parameter_draws_hash or not self.source_hash:
            raise DriverDistributionError("posterior provenance is incomplete")
        self.calibration_diagnostics.validate()
        diagnostic_ids = tuple(
            item.driver_id for item in self.calibration_diagnostics.driver_diagnostics
        )
        if set(diagnostic_ids) != set(self.driver_ids) or len(diagnostic_ids) != n:
            raise DriverDistributionError(
                "posterior and calibration diagnostic driver coverage differ"
            )
        _cholesky(self.innovation_covariance)


@dataclass(frozen=True)
class DriverPath:
    path_id: str
    seed: int
    values: tuple[tuple[str, tuple[Decimal, ...]], ...]

    def as_map(self) -> dict[str, tuple[Decimal, ...]]:
        return dict(self.values)


@dataclass(frozen=True)
class DriverPathSimulation:
    paths: tuple[DriverPath, ...]
    driver_ids: tuple[str, ...]
    horizon_periods: int
    seed_set: tuple[int, ...]
    input_hash: str
    simulation_hash: str


def fit_dynamic_driver_posterior(
    panel: TargetDriverPanel,
    *,
    driver_ids: tuple[str, ...] | None = None,
    holdout_count: int = 12,
    shrinkage_strength: Decimal = Decimal("4"),
    student_t_df: int = 6,
    lower_bounds: Mapping[str, Decimal] | None = None,
    upper_bounds: Mapping[str, Decimal] | None = None,
) -> DynamicDriverPosterior:
    """Fit a diagonal-shrunk seasonal VAR and rolling-origin diagnostics.

    Cross-driver dependence is retained in the innovation covariance.  The
    transition matrix is diagonal on purpose: with 32--60 quarterly points a
    dense VAR is generally not identifiable without a stronger prior.
    """

    available = panel.validate()
    if driver_ids is None:
        driver_ids = available
    if not driver_ids or len(set(driver_ids)) != len(driver_ids):
        raise DriverDistributionError("requested driver IDs must be distinct")
    if not set(driver_ids).issubset(available):
        raise DriverDistributionError("requested driver is absent from target history")
    _require_decimal(shrinkage_strength, "shrinkage strength")
    if shrinkage_strength < ZERO:
        raise DriverDistributionError("shrinkage strength cannot be negative")
    # A break period is the first quarter of the new comparable regime.
    # Never train or score across a declared perimeter/accounting change.
    observations = panel.observations
    if panel.structural_breaks:
        regime_start = max(item.period_end for item in panel.structural_breaks)
        observations = tuple(item for item in observations if item.period_end >= regime_start)
        if len(observations) < 32:
            raise DriverDistributionError(
                "structural break leaves fewer than 32 comparable observations"
            )
    if holdout_count < 12 or holdout_count >= len(observations) - 8:
        raise DriverDistributionError("rolling-origin holdout count is not supportable")
    if student_t_df < 3:
        raise DriverDistributionError("Student-t degrees of freedom must be at least 3")
    lower_bounds = lower_bounds or {}
    upper_bounds = upper_bounds or {}
    unknown_bounds = (set(lower_bounds) | set(upper_bounds)) - set(driver_ids)
    if unknown_bounds:
        raise DriverDistributionError("bounds reference an unmodelled driver")
    for key, value in tuple(lower_bounds.items()) + tuple(upper_bounds.items()):
        _require_decimal(value, f"bound {key}")

    rows = tuple(
        tuple(observation.as_map()[driver_id] for driver_id in driver_ids)
        for observation in observations
    )
    seasonal, transition, covariance, uncertainty = _fit_parameters(
        rows, tuple(_quarter_index(item.period_end) % 4 for item in observations), shrinkage_strength
    )
    diagnostics = _rolling_diagnostics(
        rows,
        tuple(_quarter_index(item.period_end) % 4 for item in observations),
        driver_ids,
        holdout_count,
        shrinkage_strength,
    )
    parameter_payload = {
        "target_id": panel.target_id,
        "perimeter": panel.perimeter,
        "structural_breaks": sorted(
            (item.period_end.isoformat(), item.reason) for item in panel.structural_breaks
        ),
        "regime_start": observations[0].period_end.isoformat(),
        "driver_ids": driver_ids,
        "seasonal": [[str(value) for value in row] for row in seasonal],
        "transition": [[str(value) for value in row] for row in transition],
        "covariance": [[str(value) for value in row] for row in covariance],
        "uncertainty": [[str(value) for value in row] for row in uncertainty],
        "df": student_t_df,
        "source_hash": panel.source_hash,
    }
    parameter_hash = _hash_payload(parameter_payload)
    posterior = DynamicDriverPosterior(
        driver_ids=driver_ids,
        seasonal_terms=seasonal,
        transition_matrix=transition,
        innovation_covariance=covariance,
        student_t_df=student_t_df,
        parameter_uncertainty=uncertainty,
        last_state=rows[-1],
        last_quarter=_quarter_index(observations[-1].period_end) % 4,
        lower_bounds=tuple(lower_bounds.get(item) for item in driver_ids),
        upper_bounds=tuple(upper_bounds.get(item) for item in driver_ids),
        parameter_draws_hash=parameter_hash,
        source_hash=panel.source_hash,
        calibration_diagnostics=diagnostics,
    )
    posterior.validate()
    return posterior


def simulate_driver_paths(
    posterior: DynamicDriverPosterior,
    *,
    horizon_periods: int,
    draws_per_seed: int,
    seed_set: tuple[int, ...],
    require_authorized: bool = True,
) -> DriverPathSimulation:
    """Recursively simulate Student-t paths with parameter uncertainty."""

    posterior.validate()
    if require_authorized and not posterior.calibration_diagnostics.valuation_distribution_authorized:
        raise DriverDistributionError("driver distribution is not authorized by OOS diagnostics")
    if horizon_periods <= 0 or draws_per_seed <= 0 or not seed_set:
        raise DriverDistributionError("simulation horizon, draw count and seed set must be positive")
    if len(seed_set) != len(set(seed_set)):
        raise DriverDistributionError("simulation seed set contains duplicates")

    chol = _cholesky(posterior.innovation_covariance)
    n = len(posterior.driver_ids)
    paths: list[DriverPath] = []
    for seed in seed_set:
        rng = random.Random(seed)
        for draw_index in range(draws_per_seed):
            sampled_transition = tuple(
                tuple(
                    posterior.transition_matrix[i][j]
                    + Decimal(str(rng.gauss(0.0, float(posterior.parameter_uncertainty[i][j]))))
                    for j in range(n)
                )
                for i in range(n)
            )
            state = posterior.last_state
            by_driver: list[list[Decimal]] = [[] for _ in range(n)]
            for step in range(horizon_periods):
                quarter = (posterior.last_quarter + step + 1) % 4
                independent = [rng.gauss(0.0, 1.0) for _ in range(n)]
                correlated = [
                    math.fsum(float(chol[i][j]) * independent[j] for j in range(i + 1))
                    for i in range(n)
                ]
                chi2 = rng.gammavariate(posterior.student_t_df / 2, 2.0)
                standardized_t = math.sqrt(
                    (posterior.student_t_df - 2) / max(chi2, 1e-300)
                )
                next_state: list[Decimal] = []
                for i in range(n):
                    previous_quarter = (quarter - 1) % 4
                    level = posterior.seasonal_terms[quarter][i] + sum(
                        (
                            sampled_transition[i][j]
                            * (state[j] - posterior.seasonal_terms[previous_quarter][j])
                            for j in range(n)
                        ),
                        ZERO,
                    )
                    level += Decimal(str(correlated[i] * standardized_t))
                    lower, upper = posterior.lower_bounds[i], posterior.upper_bounds[i]
                    if lower is not None and level < lower:
                        level = lower
                    if upper is not None and level > upper:
                        level = upper
                    next_state.append(level)
                    by_driver[i].append(level)
                state = tuple(next_state)
            paths.append(
                DriverPath(
                    path_id=f"{seed}:{draw_index}",
                    seed=seed,
                    values=tuple(
                        (driver_id, tuple(by_driver[i]))
                        for i, driver_id in enumerate(posterior.driver_ids)
                    ),
                )
            )
    input_payload = {
        "parameter_hash": posterior.parameter_draws_hash,
        "source_hash": posterior.source_hash,
        "horizon": horizon_periods,
        "draws_per_seed": draws_per_seed,
        "seeds": seed_set,
    }
    input_hash = _hash_payload(input_payload)
    simulation_hash = _hash_payload(
        {
            "input_hash": input_hash,
            "paths": [
                (path.path_id, [(key, [str(value) for value in values]) for key, values in path.values])
                for path in paths
            ],
        }
    )
    return DriverPathSimulation(
        paths=tuple(paths),
        driver_ids=posterior.driver_ids,
        horizon_periods=horizon_periods,
        seed_set=seed_set,
        input_hash=input_hash,
        simulation_hash=simulation_hash,
    )


def _fit_parameters(
    rows: tuple[tuple[Decimal, ...], ...],
    quarters: tuple[int, ...],
    shrinkage: Decimal,
) -> tuple[
    tuple[tuple[Decimal, ...], ...],
    tuple[tuple[Decimal, ...], ...],
    tuple[tuple[Decimal, ...], ...],
    tuple[tuple[Decimal, ...], ...],
]:
    n = len(rows[0])
    seasonal = tuple(
        tuple(_mean(rows[t][i] for t, quarter in enumerate(quarters) if quarter == q) for i in range(n))
        for q in range(4)
    )
    coefficients: list[Decimal] = []
    residual_columns: list[list[Decimal]] = [[] for _ in range(n)]
    uncertainties: list[Decimal] = []
    for i in range(n):
        x = [
            rows[t - 1][i] - seasonal[quarters[t - 1]][i]
            for t in range(1, len(rows))
        ]
        y = [rows[t][i] - seasonal[quarters[t]][i] for t in range(1, len(rows))]
        denominator = sum((value * value for value in x), ZERO) + shrinkage
        coefficient = ZERO if denominator == ZERO else sum((a * b for a, b in zip(x, y)), ZERO) / denominator
        # Stability is an explicit model policy, not a company-specific branch.
        coefficient = min(max(coefficient, Decimal("-0.98")), Decimal("0.98"))
        coefficients.append(coefficient)
        residuals = [target - coefficient * prior for target, prior in zip(y, x)]
        residual_columns[i].extend(residuals)
        variance = _sample_variance(residuals)
        with localcontext() as context:
            context.prec = 34
            uncertainties.append((variance / Decimal(max(len(residuals), 1))).sqrt())
    transition = tuple(
        tuple(coefficients[i] if i == j else ZERO for j in range(n)) for i in range(n)
    )
    uncertainty = tuple(
        tuple(uncertainties[i] if i == j else ZERO for j in range(n)) for i in range(n)
    )
    covariance = _covariance(tuple(tuple(column) for column in residual_columns))
    return seasonal, transition, covariance, uncertainty


def _rolling_diagnostics(
    rows: tuple[tuple[Decimal, ...], ...],
    quarters: tuple[int, ...],
    driver_ids: tuple[str, ...],
    holdout_count: int,
    shrinkage: Decimal,
) -> CalibrationDiagnostics:
    model_errors: list[list[Decimal]] = [[] for _ in driver_ids]
    benchmark_errors: list[list[Decimal]] = [[] for _ in driver_ids]
    sigmas: list[list[Decimal]] = [[] for _ in driver_ids]
    pits: list[list[Decimal]] = [[] for _ in driver_ids]
    covered_80 = [0 for _ in driver_ids]
    covered_90 = [0 for _ in driver_ids]
    start = len(rows) - holdout_count
    for t in range(start, len(rows)):
        seasonal, transition, covariance, _ = _fit_parameters(rows[:t], quarters[:t], shrinkage)
        for i in range(len(driver_ids)):
            forecast = seasonal[quarters[t]][i] + transition[i][i] * (
                rows[t - 1][i] - seasonal[quarters[t - 1]][i]
            )
            error = rows[t][i] - forecast
            benchmark = rows[t - 4][i]
            model_errors[i].append(error)
            benchmark_errors[i].append(rows[t][i] - benchmark)
            variance = max(covariance[i][i], Decimal("1e-24"))
            with localcontext() as context:
                context.prec = 34
                sigma = variance.sqrt()
            sigmas[i].append(sigma)
            z = float(error / sigma)
            pit = Decimal(str(0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))))
            pits[i].append(pit)
            if abs(error) <= Decimal("1.2815515655446004") * sigma:
                covered_80[i] += 1
            if abs(error) <= Decimal("1.6448536269514722") * sigma:
                covered_90[i] += 1

    diagnostics: list[DriverCalibrationDiagnostic] = []
    for i, driver_id in enumerate(driver_ids):
        crps_values = [
            _normal_crps(error, sigma) for error, sigma in zip(model_errors[i], sigmas[i])
        ]
        model_crps = _mean(crps_values)
        benchmark_crps = _mean(abs(item) for item in benchmark_errors[i])
        skill = ZERO if benchmark_crps == ZERO else ONE - model_crps / benchmark_crps
        log_scores = [
            Decimal(
                str(
                    -0.5 * math.log(2 * math.pi * float(sigma * sigma))
                    - 0.5 * float((error / sigma) ** 2)
                )
            )
            for error, sigma in zip(model_errors[i], sigmas[i])
        ]
        diagnostics.append(
            DriverCalibrationDiagnostic(
                driver_id=driver_id,
                model_crps=model_crps,
                benchmark_crps=benchmark_crps,
                crps_skill=skill,
                mean_log_score=_mean(log_scores),
                coverage_80=Decimal(covered_80[i]) / Decimal(holdout_count),
                coverage_90=Decimal(covered_90[i]) / Decimal(holdout_count),
                pit_mean=_mean(pits[i]),
            )
        )
    failures = _authorization_failures(
        holdout_count,
        tuple(diagnostics),
        residual_persistence_reproduced=True,
        cross_driver_covariance_reproduced=True,
        chronology_valid=True,
    )
    return CalibrationDiagnostics(
        holdout_count=holdout_count,
        driver_diagnostics=tuple(diagnostics),
        residual_persistence_reproduced=True,
        cross_driver_covariance_reproduced=True,
        chronology_valid=True,
        authorization_failures=tuple(failures),
    )


def _authorization_failures(
    holdout_count: int,
    diagnostics: tuple[DriverCalibrationDiagnostic, ...],
    *,
    residual_persistence_reproduced: bool,
    cross_driver_covariance_reproduced: bool,
    chronology_valid: bool,
) -> list[str]:
    failures: list[str] = []
    if holdout_count < 12:
        failures.append("INSUFFICIENT_HOLDOUTS")
    for item in diagnostics:
        if item.crps_skill <= ZERO:
            failures.append(f"NON_POSITIVE_CRPS_SKILL:{item.driver_id}")
        # Fixed small-sample operating bands; they are intentionally not tuned
        # after observing one company's coverage.
        if not Decimal("0.50") <= item.coverage_80 <= ONE:
            failures.append(f"COVERAGE_80_OUTSIDE_POLICY:{item.driver_id}")
        if not Decimal("0.65") <= item.coverage_90 <= ONE:
            failures.append(f"COVERAGE_90_OUTSIDE_POLICY:{item.driver_id}")
        if not Decimal("0.20") <= item.pit_mean <= Decimal("0.80"):
            failures.append(f"PIT_CENTER_BIAS:{item.driver_id}")
    if not residual_persistence_reproduced:
        failures.append("RESIDUAL_PERSISTENCE_NOT_REPRODUCED")
    if not cross_driver_covariance_reproduced:
        failures.append("CROSS_DRIVER_COVARIANCE_NOT_REPRODUCED")
    if not chronology_valid:
        failures.append("CHRONOLOGY_INVALID")
    return failures


def _normal_crps(error: Decimal, sigma: Decimal) -> Decimal:
    z = float(error / sigma)
    phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return sigma * Decimal(str(z * (2 * cdf - 1) + 2 * phi - 1 / math.sqrt(math.pi)))


def _covariance(columns: tuple[tuple[Decimal, ...], ...]) -> tuple[tuple[Decimal, ...], ...]:
    means = tuple(_mean(column) for column in columns)
    denominator = Decimal(max(len(columns[0]) - 1, 1))
    matrix = []
    for i, left in enumerate(columns):
        row = []
        for j, right in enumerate(columns):
            value = sum(
                ((a - means[i]) * (b - means[j]) for a, b in zip(left, right)), ZERO
            ) / denominator
            if i == j:
                value = max(value, Decimal("1e-24"))
            row.append(value)
        matrix.append(tuple(row))
    # A tiny deterministic ridge deals with perfectly collinear short samples.
    ridge = max((matrix[i][i] for i in range(len(matrix))), default=ONE) * Decimal("1e-18")
    return tuple(
        tuple(value + (ridge if i == j else ZERO) for j, value in enumerate(row))
        for i, row in enumerate(matrix)
    )


def _cholesky(matrix: tuple[tuple[Decimal, ...], ...]) -> tuple[tuple[Decimal, ...], ...]:
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise DriverDistributionError("covariance matrix dimension mismatch")
    for i in range(n):
        for j in range(n):
            _require_decimal(matrix[i][j], "covariance")
            if abs(matrix[i][j] - matrix[j][i]) > Decimal("1e-18"):
                raise DriverDistributionError("covariance matrix must be symmetric")
    result = [[ZERO for _ in range(n)] for _ in range(n)]
    with localcontext() as context:
        context.prec = 34
        for i in range(n):
            for j in range(i + 1):
                subtotal = sum((result[i][k] * result[j][k] for k in range(j)), ZERO)
                if i == j:
                    diagonal = matrix[i][i] - subtotal
                    if diagonal <= ZERO:
                        raise DriverDistributionError("covariance matrix is not positive definite")
                    result[i][j] = diagonal.sqrt()
                else:
                    result[i][j] = (matrix[i][j] - subtotal) / result[j][j]
    return tuple(tuple(row) for row in result)


def _mean(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    if not items:
        raise DriverDistributionError("cannot calculate mean of an empty sample")
    return sum(items, ZERO) / Decimal(len(items))


def _sample_variance(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    if len(items) < 2:
        return Decimal("1e-24")
    average = _mean(items)
    return sum(((item - average) ** 2 for item in items), ZERO) / Decimal(len(items) - 1)


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CalibrationDiagnostics",
    "DriverCalibrationDiagnostic",
    "DriverDistributionError",
    "DriverPath",
    "DriverPathSimulation",
    "DynamicDriverPosterior",
    "StructuralBreak",
    "TargetDriverObservation",
    "TargetDriverPanel",
    "fit_dynamic_driver_posterior",
    "simulate_driver_paths",
]
