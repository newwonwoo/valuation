from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math
import random

from .runtime_authority import DecisionDomain, forbid_llm_decision


@dataclass(frozen=True)
class ContinuousDriverPosterior:
    driver_id: str
    mean_path: tuple[Decimal, ...]
    scale_path: tuple[Decimal, ...]
    mean_uncertainty_path: tuple[Decimal, ...]
    source_hash: str
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None

    def validate(self) -> None:
        if not self.driver_id or not self.source_hash:
            raise ValueError("continuous driver identity is incomplete")
        n = len(self.mean_path)
        if n == 0 or len(self.scale_path) != n or len(self.mean_uncertainty_path) != n:
            raise ValueError("continuous driver paths must be non-empty and aligned")
        if any(not x.is_finite() for x in self.mean_path + self.scale_path + self.mean_uncertainty_path):
            raise ValueError("continuous driver path contains non-finite values")
        if any(x < 0 for x in self.scale_path + self.mean_uncertainty_path):
            raise ValueError("continuous driver scales cannot be negative")
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise ValueError("continuous driver bounds are inverted")


@dataclass(frozen=True)
class ContinuousDriverDependence:
    version: str
    driver_ids: tuple[str, ...]
    correlation_matrix: tuple[tuple[Decimal, ...], ...]
    student_t_df: int = 6

    def validate(self) -> None:
        if not self.version or not self.driver_ids:
            raise ValueError("continuous dependence requires version and driver IDs")
        if self.student_t_df < 3:
            raise ValueError("student-t degrees of freedom must be at least 3")
        n = len(self.driver_ids)
        if len(self.driver_ids) != len(set(self.driver_ids)):
            raise ValueError("continuous dependence contains duplicate driver IDs")
        if len(self.correlation_matrix) != n or any(len(row) != n for row in self.correlation_matrix):
            raise ValueError("continuous dependence matrix dimension mismatch")
        for i, row in enumerate(self.correlation_matrix):
            for j, value in enumerate(row):
                if not value.is_finite() or not Decimal("-1") <= value <= Decimal("1"):
                    raise ValueError("continuous dependence correlation outside [-1,1]")
                if i == j and abs(value - Decimal("1")) > Decimal("1e-12"):
                    raise ValueError("continuous dependence diagonal must equal one")
                if abs(value - self.correlation_matrix[j][i]) > Decimal("1e-12"):
                    raise ValueError("continuous dependence matrix must be symmetric")
        _cholesky(tuple(tuple(float(x) for x in row) for row in self.correlation_matrix))


@dataclass(frozen=True)
class ScenarioFinancialPath:
    scenario_id: str
    driver_paths: tuple[tuple[str, tuple[Decimal, ...]], ...]
    driver_weights: tuple[tuple[str, Decimal], ...] = ()

    def validate(self) -> None:
        if not self.scenario_id or not self.driver_paths:
            raise ValueError("scenario financial path is incomplete")
        ids = tuple(driver_id for driver_id, _ in self.driver_paths)
        if len(ids) != len(set(ids)):
            raise ValueError("scenario financial path contains duplicate drivers")
        if any(not path for _, path in self.driver_paths):
            raise ValueError("scenario financial path contains empty driver path")
        weights = dict(self.driver_weights)
        if any(weight <= 0 or not weight.is_finite() for weight in weights.values()):
            raise ValueError("scenario financial path weights must be positive")
        if weights and not set(weights).issubset(ids):
            raise ValueError("scenario financial path weight references unknown driver")


@dataclass(frozen=True)
class ContinuousScenarioEstimate:
    scenario_id: str
    probability: Decimal
    lower_probability: Decimal
    upper_probability: Decimal


@dataclass(frozen=True)
class ContinuousFinancialPathSimulation:
    estimates: tuple[ContinuousScenarioEstimate, ...]
    credible_level: Decimal
    dependence_version: str
    simulation_hash: str
    source_hashes: tuple[str, ...]
    outer_draws: int
    inner_draws: int


def simulate_continuous_financial_paths(
    *,
    drivers: tuple[ContinuousDriverPosterior, ...],
    scenarios: tuple[ScenarioFinancialPath, ...],
    dependence: ContinuousDriverDependence,
    credible_level: Decimal = Decimal("0.90"),
    outer_draws: int = 300,
    inner_draws: int = 200,
    seed: int = 20260829,
) -> ContinuousFinancialPathSimulation:
    """Estimate scenario probabilities from continuous financial paths.

    LLM callbacks may propose evidence/hypotheses, but cannot execute this
    probability decision. The simulation contains no market-price, target-price,
    intrinsic-value, or return inputs.
    """
    forbid_llm_decision(DecisionDomain.PROBABILITY)
    if not drivers or len(scenarios) < 2:
        raise ValueError("continuous financial path simulation requires drivers and at least two scenarios")
    if outer_draws < 10 or inner_draws < 10:
        raise ValueError("continuous financial path simulation requires at least 10x10 draws")
    if not Decimal("0") < credible_level < Decimal("1"):
        raise ValueError("credible level must lie within (0,1)")
    for driver in drivers:
        driver.validate()
    for scenario in scenarios:
        scenario.validate()
    dependence.validate()

    driver_map = {driver.driver_id: driver for driver in drivers}
    if set(dependence.driver_ids) != set(driver_map):
        raise ValueError("dependence driver IDs must exactly match continuous drivers")
    periods = len(drivers[0].mean_path)
    if any(len(driver.mean_path) != periods for driver in drivers):
        raise ValueError("continuous drivers must use the same number of periods")
    scenario_ids = tuple(item.scenario_id for item in scenarios)
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenario financial path IDs must be unique")
    for scenario in scenarios:
        path_map = dict(scenario.driver_paths)
        if set(path_map) != set(driver_map):
            raise ValueError("scenario financial paths must contain exactly the modeled drivers")
        if any(len(path_map[driver_id]) != periods for driver_id in dependence.driver_ids):
            raise ValueError("scenario and modeled driver horizons must align")

    ordered = tuple(driver_map[driver_id] for driver_id in dependence.driver_ids)
    matrix = tuple(tuple(float(x) for x in row) for row in dependence.correlation_matrix)
    chol = _cholesky(matrix)
    rng = random.Random(seed)
    scenario_samples = {scenario.scenario_id: [] for scenario in scenarios}

    for _ in range(outer_draws):
        sampled_means: list[list[float]] = []
        for driver in ordered:
            sampled_means.append([
                float(mean) + rng.gauss(0.0, float(uncertainty))
                for mean, uncertainty in zip(driver.mean_path, driver.mean_uncertainty_path)
            ])
        counts = {scenario.scenario_id: 0 for scenario in scenarios}
        for _ in range(inner_draws):
            path = {driver.driver_id: [] for driver in ordered}
            for period in range(periods):
                independent = [rng.gauss(0.0, 1.0) for _ in ordered]
                correlated = [sum(chol[i][j] * independent[j] for j in range(i + 1)) for i in range(len(ordered))]
                chi2 = rng.gammavariate(dependence.student_t_df / 2.0, 2.0)
                tail_scale = math.sqrt(dependence.student_t_df / max(chi2, 1e-12))
                for i, driver in enumerate(ordered):
                    level = sampled_means[i][period] + float(driver.scale_path[period]) * correlated[i] * tail_scale
                    if driver.lower_bound is not None:
                        level = max(level, float(driver.lower_bound))
                    if driver.upper_bound is not None:
                        level = min(level, float(driver.upper_bound))
                    path[driver.driver_id].append(level)
            winner = _nearest_scenario(path, scenarios, driver_map)
            counts[winner] += 1
        for scenario_id, count in counts.items():
            scenario_samples[scenario_id].append(count / inner_draws)

    tail = float((Decimal("1") - credible_level) / Decimal("2"))
    estimates: list[ContinuousScenarioEstimate] = []
    for scenario in scenarios:
        values = sorted(scenario_samples[scenario.scenario_id])
        estimates.append(
            ContinuousScenarioEstimate(
                scenario_id=scenario.scenario_id,
                probability=Decimal(str(sum(values) / len(values))),
                lower_probability=Decimal(str(_quantile(values, tail))),
                upper_probability=Decimal(str(_quantile(values, 1.0 - tail))),
            )
        )
    total = sum((estimate.probability for estimate in estimates), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("0.02"):
        raise ValueError("continuous scenario probabilities do not sum approximately to one")
    normalized = tuple(
        ContinuousScenarioEstimate(
            scenario_id=estimate.scenario_id,
            probability=estimate.probability / total,
            lower_probability=estimate.lower_probability,
            upper_probability=estimate.upper_probability,
        )
        for estimate in estimates
    )
    payload = {
        "contract": "continuous_financial_path_probability/v1",
        "drivers": [
            (
                driver.driver_id,
                [str(x) for x in driver.mean_path],
                [str(x) for x in driver.scale_path],
                [str(x) for x in driver.mean_uncertainty_path],
                driver.source_hash,
                str(driver.lower_bound) if driver.lower_bound is not None else None,
                str(driver.upper_bound) if driver.upper_bound is not None else None,
            )
            for driver in ordered
        ],
        "scenarios": [
            (
                scenario.scenario_id,
                [(driver_id, [str(x) for x in path]) for driver_id, path in scenario.driver_paths],
                [(driver_id, str(weight)) for driver_id, weight in scenario.driver_weights],
            )
            for scenario in scenarios
        ],
        "dependence_version": dependence.version,
        "correlation_matrix": [[str(x) for x in row] for row in dependence.correlation_matrix],
        "student_t_df": dependence.student_t_df,
        "credible_level": str(credible_level),
        "outer_draws": outer_draws,
        "inner_draws": inner_draws,
        "seed": seed,
        "estimates": [
            (estimate.scenario_id, str(estimate.probability), str(estimate.lower_probability), str(estimate.upper_probability))
            for estimate in normalized
        ],
    }
    simulation_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ContinuousFinancialPathSimulation(
        estimates=normalized,
        credible_level=credible_level,
        dependence_version=dependence.version,
        simulation_hash=simulation_hash,
        source_hashes=tuple(driver.source_hash for driver in ordered),
        outer_draws=outer_draws,
        inner_draws=inner_draws,
    )


def _nearest_scenario(
    path: dict[str, list[float]],
    scenarios: tuple[ScenarioFinancialPath, ...],
    driver_map: dict[str, ContinuousDriverPosterior],
) -> str:
    scored: list[tuple[float, str]] = []
    for scenario in scenarios:
        anchors = dict(scenario.driver_paths)
        weights = dict(scenario.driver_weights)
        distance = 0.0
        weight_total = 0.0
        for driver_id, observed_path in path.items():
            driver = driver_map[driver_id]
            driver_weight = float(weights.get(driver_id, Decimal("1")))
            for observed, anchor, scale in zip(observed_path, anchors[driver_id], driver.scale_path):
                denominator = max(abs(float(scale)), 1e-9)
                diff = (observed - float(anchor)) / denominator
                distance += driver_weight * diff * diff
                weight_total += driver_weight
        scored.append((distance / max(weight_total, 1e-12), scenario.scenario_id))
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][1]


def _cholesky(matrix: tuple[tuple[float, ...], ...]) -> list[list[float]]:
    n = len(matrix)
    result = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            level = matrix[i][j] - sum(result[i][k] * result[j][k] for k in range(j))
            if i == j:
                if level < -1e-10:
                    raise ValueError("continuous dependence matrix is not positive semidefinite")
                result[i][j] = math.sqrt(max(level, 1e-12))
            else:
                if abs(result[j][j]) < 1e-15:
                    raise ValueError("continuous dependence matrix is singular")
                result[i][j] = level / result[j][j]
    return result


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    q = max(0.0, min(1.0, q))
    pos = q * (len(values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - pos) + values[hi] * (pos - lo)
