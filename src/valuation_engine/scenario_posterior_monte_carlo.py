from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math
import random
from statistics import NormalDist


@dataclass(frozen=True)
class PosteriorEventFactor:
    event_id: str
    alpha: Decimal
    beta: Decimal
    source_hash: str

    def validate(self) -> None:
        if not self.event_id or not self.source_hash:
            raise ValueError("posterior event factor identity is incomplete")
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError("posterior event beta parameters must be positive")


@dataclass(frozen=True)
class PosteriorScenarioRule:
    scenario_id: str
    required_event_ids: tuple[str, ...] = ()
    forbidden_event_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.scenario_id:
            raise ValueError("posterior scenario rule requires scenario_id")
        if len(self.required_event_ids) != len(set(self.required_event_ids)):
            raise ValueError("posterior scenario rule has duplicate required events")
        if len(self.forbidden_event_ids) != len(set(self.forbidden_event_ids)):
            raise ValueError("posterior scenario rule has duplicate forbidden events")
        if set(self.required_event_ids).intersection(self.forbidden_event_ids):
            raise ValueError("posterior scenario rule requires and forbids the same event")

    def matches(self, active: frozenset[str]) -> bool:
        return set(self.required_event_ids).issubset(active) and not set(self.forbidden_event_ids).intersection(active)


@dataclass(frozen=True)
class CorrelationDependence:
    version: str
    event_ids: tuple[str, ...]
    correlation_matrix: tuple[tuple[Decimal, ...], ...]

    def validate(self) -> None:
        if not self.version or not self.event_ids:
            raise ValueError("correlation dependence requires version and event IDs")
        n = len(self.event_ids)
        if len(self.correlation_matrix) != n or any(len(row) != n for row in self.correlation_matrix):
            raise ValueError("correlation matrix dimension mismatch")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("correlation dependence contains duplicate event IDs")
        for i, row in enumerate(self.correlation_matrix):
            for j, value in enumerate(row):
                if not value.is_finite() or not Decimal("-1") <= value <= Decimal("1"):
                    raise ValueError("correlation values must lie within [-1,1]")
                if i == j and abs(value - Decimal("1")) > Decimal("1e-12"):
                    raise ValueError("correlation matrix diagonal must equal one")
                if abs(value - self.correlation_matrix[j][i]) > Decimal("1e-12"):
                    raise ValueError("correlation matrix must be symmetric")
        _cholesky(tuple(tuple(float(x) for x in row) for row in self.correlation_matrix))


@dataclass(frozen=True)
class ScenarioPosteriorEstimate:
    scenario_id: str
    point_probability: Decimal
    lower_probability: Decimal
    upper_probability: Decimal


@dataclass(frozen=True)
class ScenarioPosteriorSimulation:
    estimates: tuple[ScenarioPosteriorEstimate, ...]
    credible_level: Decimal
    dependence_version: str
    simulation_hash: str
    source_hashes: tuple[str, ...]
    outer_draws: int
    inner_draws: int


def simulate_scenario_posterior(
    *,
    factors: tuple[PosteriorEventFactor, ...],
    rules: tuple[PosteriorScenarioRule, ...],
    dependence: CorrelationDependence,
    credible_level: Decimal = Decimal("0.90"),
    outer_draws: int = 300,
    inner_draws: int = 200,
    seed: int = 20260829,
) -> ScenarioPosteriorSimulation:
    if not factors or not rules:
        raise ValueError("scenario posterior simulation requires factors and rules")
    if outer_draws < 10 or inner_draws < 10:
        raise ValueError("scenario posterior simulation requires at least 10x10 draws")
    if not Decimal("0") < credible_level < Decimal("1"):
        raise ValueError("credible level must lie within (0,1)")
    for factor in factors:
        factor.validate()
    for rule in rules:
        rule.validate()
    dependence.validate()
    factor_map = {factor.event_id: factor for factor in factors}
    if set(dependence.event_ids) != set(factor_map):
        raise ValueError("dependence event IDs must exactly match posterior factors")
    referenced = set()
    for rule in rules:
        referenced.update(rule.required_event_ids)
        referenced.update(rule.forbidden_event_ids)
    if not referenced.issubset(factor_map):
        raise ValueError("scenario rules reference unknown posterior factors")

    ordered = tuple(factor_map[event_id] for event_id in dependence.event_ids)
    matrix = tuple(tuple(float(x) for x in row) for row in dependence.correlation_matrix)
    chol = _cholesky(matrix)
    normal = NormalDist()
    rng = random.Random(seed)
    scenario_samples = {rule.scenario_id: [] for rule in rules}

    for _ in range(outer_draws):
        event_probabilities = [rng.betavariate(float(f.alpha), float(f.beta)) for f in ordered]
        counts = {rule.scenario_id: 0 for rule in rules}
        for _ in range(inner_draws):
            independent = [rng.gauss(0.0, 1.0) for _ in ordered]
            correlated = [sum(chol[i][j] * independent[j] for j in range(i + 1)) for i in range(len(ordered))]
            active = frozenset(
                ordered[i].event_id
                for i, z in enumerate(correlated)
                if normal.cdf(z) < event_probabilities[i]
            )
            matches = [rule for rule in rules if rule.matches(active)]
            if len(matches) != 1:
                raise ValueError(f"event state must map to exactly one scenario; active={sorted(active)}")
            counts[matches[0].scenario_id] += 1
        for scenario_id, count in counts.items():
            scenario_samples[scenario_id].append(count / inner_draws)

    tail = float((Decimal("1") - credible_level) / Decimal("2"))
    estimates = []
    for rule in rules:
        values = sorted(scenario_samples[rule.scenario_id])
        estimates.append(
            ScenarioPosteriorEstimate(
                scenario_id=rule.scenario_id,
                point_probability=Decimal(str(sum(values) / len(values))),
                lower_probability=Decimal(str(_quantile(values, tail))),
                upper_probability=Decimal(str(_quantile(values, 1.0 - tail))),
            )
        )
    point_total = sum((item.point_probability for item in estimates), Decimal("0"))
    if abs(point_total - Decimal("1")) > Decimal("0.02"):
        raise ValueError("simulated scenario point probabilities do not sum approximately to one")
    payload = {
        "contract": "scenario_posterior_monte_carlo/v1",
        "factors": [(f.event_id, str(f.alpha), str(f.beta), f.source_hash) for f in ordered],
        "rules": [(r.scenario_id, r.required_event_ids, r.forbidden_event_ids) for r in rules],
        "dependence_version": dependence.version,
        "correlation_matrix": [[str(v) for v in row] for row in dependence.correlation_matrix],
        "credible_level": str(credible_level),
        "outer_draws": outer_draws,
        "inner_draws": inner_draws,
        "seed": seed,
        "estimates": [(e.scenario_id, str(e.point_probability), str(e.lower_probability), str(e.upper_probability)) for e in estimates],
    }
    simulation_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ScenarioPosteriorSimulation(
        estimates=tuple(estimates),
        credible_level=credible_level,
        dependence_version=dependence.version,
        simulation_hash=simulation_hash,
        source_hashes=tuple(f.source_hash for f in ordered),
        outer_draws=outer_draws,
        inner_draws=inner_draws,
    )


def _cholesky(matrix: tuple[tuple[float, ...], ...]) -> list[list[float]]:
    n = len(matrix)
    result = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            value = matrix[i][j] - sum(result[i][k] * result[j][k] for k in range(j))
            if i == j:
                if value < -1e-10:
                    raise ValueError("correlation matrix is not positive semidefinite")
                result[i][j] = math.sqrt(max(value, 1e-12))
            else:
                if abs(result[j][j]) < 1e-15:
                    raise ValueError("correlation matrix is singular")
                result[i][j] = value / result[j][j]
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
