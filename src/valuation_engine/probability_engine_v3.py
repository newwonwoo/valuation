from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .actual_units import Measure
from .assumption_compiler import CompiledAssumptionSet
from .continuous_predictive_weight import (
    ContinuousWeightPolicy,
    PredictiveEvidenceProfile,
    PredictiveEvidenceWeight,
    continuous_predictive_weight,
)
from .dynamic_hierarchical_posterior import (
    BetaPosterior,
    DataIntegrityAssessment,
    DynamicPosteriorSnapshot,
    HierarchicalEvidenceBlock,
    PosteriorStatus,
    build_dynamic_hierarchical_posterior,
)
from .records import CalibrationStatus
from .runtime_authority import DecisionDomain, forbid_llm_decision
from .scenario_posterior_monte_carlo import (
    CorrelationDependence,
    PosteriorEventFactor,
    PosteriorScenarioRule,
    ScenarioPosteriorSimulation,
    simulate_scenario_posterior,
)


class ProbabilityEngineV3Status(str, Enum):
    ESTIMATED = "ESTIMATED"
    DATA_BLOCKED = "DATA_BLOCKED"


@dataclass(frozen=True)
class ProbabilityLevelInput:
    node_id: str
    success_count: int
    total_count: int
    dataset_hash: str
    predictive_profile: PredictiveEvidenceProfile
    integrity: DataIntegrityAssessment = DataIntegrityAssessment()


@dataclass(frozen=True)
class ProbabilityEventInput:
    event_id: str
    root_prior_mean: Decimal
    root_prior_strength: Decimal
    levels: tuple[ProbabilityLevelInput, ...]


@dataclass(frozen=True)
class ProbabilityEngineV3Spec:
    """Pure probability input contract.

    This contract intentionally contains no market price, target price, scenario
    valuation, intrinsic value, expected value, or return target fields. The
    probability engine may consume only event evidence, hierarchy metadata,
    dependence assumptions, and simulation controls.
    """

    cohort_key: str
    horizon: str
    events: tuple[ProbabilityEventInput, ...]
    scenario_rules: tuple[PosteriorScenarioRule, ...]
    dependence: CorrelationDependence
    credible_level: Decimal = Decimal("0.90")
    outer_draws: int = 300
    inner_draws: int = 200
    seed: int = 20260829


@dataclass(frozen=True)
class ProbabilityEventResult:
    event_id: str
    posterior: DynamicPosteriorSnapshot
    level_weights: tuple[tuple[str, PredictiveEvidenceWeight], ...]


@dataclass(frozen=True)
class ProbabilityEngineV3Certificate:
    cohort_key: str
    snapshot_hash: str
    dataset_hash: str
    scenario_probabilities: tuple[tuple[str, Decimal], ...]
    scenario_intervals: tuple[tuple[str, Decimal, Decimal], ...]
    source_posterior_hashes: tuple[str, ...]
    credible_level: Decimal

    def validate_for_weighting(self) -> None:
        if not self.cohort_key or not self.snapshot_hash or not self.dataset_hash:
            raise ValueError("probability-engine-v3 certificate identity is incomplete")
        if not self.scenario_probabilities:
            raise ValueError("probability-engine-v3 certificate has no scenario probabilities")
        total = sum((value for _, value in self.scenario_probabilities), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("1e-12"):
            raise ValueError(f"v3 scenario probabilities sum to {total}, not one")
        interval_map = {scenario_id: (lower, upper) for scenario_id, lower, upper in self.scenario_intervals}
        for scenario_id, value in self.scenario_probabilities:
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError("v3 scenario probability lies outside [0,1]")
            if scenario_id not in interval_map:
                raise ValueError("v3 scenario probability is missing its credible interval")
            lower, upper = interval_map[scenario_id]
            if not Decimal("0") <= lower <= value <= upper <= Decimal("1"):
                raise ValueError("v3 scenario credible interval is invalid")
        if not Decimal("0") < self.credible_level < Decimal("1"):
            raise ValueError("v3 credible level is invalid")
        if any(not value for value in self.source_posterior_hashes):
            raise ValueError("v3 certificate contains empty posterior lineage")

    @property
    def lineage_hash(self) -> str:
        payload = {
            "contract": "probability_engine_v3_certificate/v2-price-isolated",
            "cohort_key": self.cohort_key,
            "snapshot_hash": self.snapshot_hash,
            "dataset_hash": self.dataset_hash,
            "scenario_probabilities": [(k, str(v)) for k, v in self.scenario_probabilities],
            "scenario_intervals": [(k, str(a), str(b)) for k, a, b in self.scenario_intervals],
            "source_posterior_hashes": self.source_posterior_hashes,
            "credible_level": str(self.credible_level),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProbabilityEngineV3Result:
    """Pure probability output contract; no valuation or market-price fields."""

    status: ProbabilityEngineV3Status
    event_results: tuple[ProbabilityEventResult, ...]
    scenario_simulation: ScenarioPosteriorSimulation | None
    scenario_probabilities: tuple[tuple[str, Decimal], ...]
    scenario_intervals: tuple[tuple[str, Decimal, Decimal], ...]
    integrity_violations: tuple[str, ...]
    snapshot_hash: str
    dataset_hash: str

    @property
    def numeric_weighting_allowed(self) -> bool:
        return self.status is ProbabilityEngineV3Status.ESTIMATED and self.scenario_simulation is not None

    def certificate(self, *, cohort_key: str) -> ProbabilityEngineV3Certificate:
        if not self.numeric_weighting_allowed:
            raise PermissionError("probability-engine-v3 result is blocked by data integrity")
        certificate = ProbabilityEngineV3Certificate(
            cohort_key=cohort_key,
            snapshot_hash=self.snapshot_hash,
            dataset_hash=self.dataset_hash,
            scenario_probabilities=self.scenario_probabilities,
            scenario_intervals=self.scenario_intervals,
            source_posterior_hashes=tuple(result.posterior.snapshot_hash for result in self.event_results),
            credible_level=self.scenario_simulation.credible_level if self.scenario_simulation else Decimal("0.90"),
        )
        certificate.validate_for_weighting()
        return certificate


def run_probability_engine_v3(
    spec: ProbabilityEngineV3Spec,
    *,
    weight_policy: ContinuousWeightPolicy = ContinuousWeightPolicy(),
) -> ProbabilityEngineV3Result:
    forbid_llm_decision(DecisionDomain.PROBABILITY)
    if not spec.cohort_key or not spec.horizon or not spec.events:
        raise ValueError("probability-engine-v3 spec identity/events are required")
    event_ids = tuple(event.event_id for event in spec.events)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("probability-engine-v3 event IDs must be unique")
    if set(event_ids) != set(spec.dependence.event_ids):
        raise ValueError("v3 dependence event IDs must exactly match event inputs")

    event_results: list[ProbabilityEventResult] = []
    violations: list[str] = []
    for event in spec.events:
        root = BetaPosterior.from_mean_strength(event.root_prior_mean, event.root_prior_strength)
        blocks: list[HierarchicalEvidenceBlock] = []
        weights: list[tuple[str, PredictiveEvidenceWeight]] = []
        for level in event.levels:
            evidence_weight = continuous_predictive_weight(level.predictive_profile, weight_policy)
            weights.append((level.node_id, evidence_weight))
            blocks.append(
                HierarchicalEvidenceBlock(
                    node_id=level.node_id,
                    success_count=level.success_count,
                    total_count=level.total_count,
                    likelihood_weight=evidence_weight.likelihood_weight,
                    dataset_hash=level.dataset_hash,
                    integrity=level.integrity,
                )
            )
        posterior = build_dynamic_hierarchical_posterior(
            event_class=event.event_id,
            horizon=spec.horizon,
            root_prior=root,
            evidence_blocks=tuple(blocks),
            credible_level=spec.credible_level,
        )
        event_results.append(ProbabilityEventResult(event.event_id, posterior, tuple(weights)))
        if posterior.status is PosteriorStatus.DATA_BLOCKED:
            violations.extend(f"{event.event_id}:{item}" for item in posterior.integrity_violations)

    dataset_hash = _combined_dataset_hash(tuple(event_results), spec)
    if violations:
        return _result(
            status=ProbabilityEngineV3Status.DATA_BLOCKED,
            event_results=tuple(event_results),
            scenario_simulation=None,
            probabilities=(),
            intervals=(),
            violations=tuple(sorted(violations)),
            dataset_hash=dataset_hash,
            spec=spec,
        )

    factors: list[PosteriorEventFactor] = []
    for event_result in event_results:
        posterior = event_result.posterior.final_posterior
        if posterior is None:
            raise RuntimeError("estimated v3 event is missing final posterior")
        factors.append(
            PosteriorEventFactor(
                event_id=event_result.event_id,
                alpha=posterior.alpha,
                beta=posterior.beta,
                source_hash=event_result.posterior.snapshot_hash,
            )
        )
    simulation = simulate_scenario_posterior(
        factors=tuple(factors),
        rules=spec.scenario_rules,
        dependence=spec.dependence,
        credible_level=spec.credible_level,
        outer_draws=spec.outer_draws,
        inner_draws=spec.inner_draws,
        seed=spec.seed,
    )
    raw_points = tuple((item.scenario_id, item.point_probability) for item in simulation.estimates)
    total = sum((value for _, value in raw_points), Decimal("0"))
    probabilities = tuple((scenario_id, value / total) for scenario_id, value in raw_points)
    intervals = tuple(
        (item.scenario_id, item.lower_probability, item.upper_probability)
        for item in simulation.estimates
    )
    return _result(
        status=ProbabilityEngineV3Status.ESTIMATED,
        event_results=tuple(event_results),
        scenario_simulation=simulation,
        probabilities=probabilities,
        intervals=intervals,
        violations=(),
        dataset_hash=dataset_hash,
        spec=spec,
    )


def apply_v3_probabilities_to_compiled_assumptions(
    compiled: CompiledAssumptionSet,
    result: ProbabilityEngineV3Result,
    *,
    probability_key: str = "scenario_probability",
) -> CompiledAssumptionSet:
    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
    if not result.numeric_weighting_allowed:
        raise PermissionError("cannot bind blocked v3 probabilities into assumptions")
    probability_map = dict(result.scenario_probabilities)
    seen: set[str] = set()
    assumptions = []
    for item in compiled.assumptions:
        if item.key == probability_key and item.scenario_id in probability_map:
            seen.add(item.scenario_id)
            assumptions.append(
                replace(
                    item,
                    measure=Measure(probability_map[item.scenario_id], "ratio", item.measure.as_of),
                    calibration_status=CalibrationStatus.CALIBRATED,
                )
            )
        else:
            assumptions.append(item)
    if seen != set(probability_map):
        missing = sorted(set(probability_map) - seen)
        raise ValueError(f"compiled assumption set is missing v3 scenario probabilities: {missing}")
    provisional = CompiledAssumptionSet(compiled.target_id, tuple(assumptions), "")
    from .run_hash import compiled_assumption_set_hash

    return CompiledAssumptionSet(
        target_id=compiled.target_id,
        assumptions=tuple(assumptions),
        assumption_set_hash=compiled_assumption_set_hash(provisional),
    )


def _combined_dataset_hash(event_results: tuple[ProbabilityEventResult, ...], spec: ProbabilityEngineV3Spec) -> str:
    payload = {
        "contract": "probability_engine_v3_dataset/v2-price-isolated",
        "event_dataset_hashes": [(item.event_id, item.posterior.dataset_hash) for item in event_results],
        "dependence_version": spec.dependence.version,
        "cohort_key": spec.cohort_key,
        "horizon": spec.horizon,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _result(
    *,
    status: ProbabilityEngineV3Status,
    event_results: tuple[ProbabilityEventResult, ...],
    scenario_simulation: ScenarioPosteriorSimulation | None,
    probabilities: tuple[tuple[str, Decimal], ...],
    intervals: tuple[tuple[str, Decimal, Decimal], ...],
    violations: tuple[str, ...],
    dataset_hash: str,
    spec: ProbabilityEngineV3Spec,
) -> ProbabilityEngineV3Result:
    payload = {
        "contract": "probability_engine_v3_result/v2-price-isolated",
        "status": status.value,
        "cohort_key": spec.cohort_key,
        "horizon": spec.horizon,
        "event_snapshot_hashes": [(item.event_id, item.posterior.snapshot_hash) for item in event_results],
        "scenario_simulation_hash": scenario_simulation.simulation_hash if scenario_simulation else None,
        "scenario_probabilities": [(key, str(value)) for key, value in probabilities],
        "scenario_intervals": [(key, str(lower), str(upper)) for key, lower, upper in intervals],
        "violations": violations,
        "dataset_hash": dataset_hash,
    }
    snapshot_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ProbabilityEngineV3Result(
        status=status,
        event_results=event_results,
        scenario_simulation=scenario_simulation,
        scenario_probabilities=probabilities,
        scenario_intervals=intervals,
        integrity_violations=violations,
        snapshot_hash=snapshot_hash,
        dataset_hash=dataset_hash,
    )
