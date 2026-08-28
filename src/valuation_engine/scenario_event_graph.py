from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from .records import CalibrationStatus


class ScenarioDependenceMethod(str, Enum):
    MUTUALLY_EXCLUSIVE_STATE_TABLE = "mutually_exclusive_state_table"
    VERSIONED_CORRELATION_OR_COPULA = "versioned_correlation_or_copula"
    FRECHET_BOUNDS = "frechet_bounds"


@dataclass(frozen=True)
class ScenarioEventFactor:
    event_id: str
    probability: Decimal
    calibration_certificate_hash: str

    def validate(self) -> None:
        if not self.event_id or not self.calibration_certificate_hash:
            raise ValueError(
                "scenario event factor requires event ID and calibration certificate hash"
            )
        if (
            not self.probability.is_finite()
            or not Decimal("0") <= self.probability <= Decimal("1")
        ):
            raise ValueError("scenario event factor probability must be within [0,1]")


@dataclass(frozen=True)
class ScenarioEventRule:
    scenario_id: str
    required_event_ids: tuple[str, ...] = ()
    forbidden_event_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario event rule requires scenario_id")
        if len(self.required_event_ids) != len(set(self.required_event_ids)):
            raise ValueError("scenario rule has duplicate required event IDs")
        if len(self.forbidden_event_ids) != len(set(self.forbidden_event_ids)):
            raise ValueError("scenario rule has duplicate forbidden event IDs")
        overlap = set(self.required_event_ids).intersection(self.forbidden_event_ids)
        if overlap:
            raise ValueError(
                f"scenario rule requires and forbids the same events: {sorted(overlap)}"
            )

    def matches(self, active_event_ids: frozenset[str]) -> bool:
        self.validate()
        return set(self.required_event_ids).issubset(active_event_ids) and not set(
            self.forbidden_event_ids
        ).intersection(active_event_ids)


@dataclass(frozen=True)
class ScenarioJointState:
    state_id: str
    probability: Decimal
    active_event_ids: tuple[str, ...]

    def validate(self) -> None:
        if not self.state_id:
            raise ValueError("scenario joint state requires state_id")
        if (
            not self.probability.is_finite()
            or not Decimal("0") <= self.probability <= Decimal("1")
        ):
            raise ValueError("scenario joint-state probability must be within [0,1]")
        if len(self.active_event_ids) != len(set(self.active_event_ids)):
            raise ValueError("scenario joint state contains duplicate active event IDs")


@dataclass(frozen=True)
class ScenarioDependenceContract:
    method: ScenarioDependenceMethod
    version: str
    joint_states: tuple[ScenarioJointState, ...] = ()

    def validate(self) -> None:
        if not self.version:
            raise ValueError("scenario dependence contract requires a version")
        for state in self.joint_states:
            state.validate()
        if self.method in {
            ScenarioDependenceMethod.MUTUALLY_EXCLUSIVE_STATE_TABLE,
            ScenarioDependenceMethod.VERSIONED_CORRELATION_OR_COPULA,
        }:
            if not self.joint_states:
                raise ValueError(
                    f"{self.method.value} requires an explicit joint-state distribution"
                )
            total = sum(
                (state.probability for state in self.joint_states), Decimal("0")
            )
            if abs(total - Decimal("1")) > Decimal("1e-12"):
                raise ValueError(
                    f"scenario joint-state probabilities sum to {total}, not one"
                )
        elif self.method is ScenarioDependenceMethod.FRECHET_BOUNDS:
            if self.joint_states:
                raise ValueError(
                    "frechet_bounds does not accept a hidden point-estimate joint distribution"
                )


@dataclass(frozen=True)
class ScenarioProbabilityEstimate:
    scenario_id: str
    lower: Decimal
    upper: Decimal
    point: Decimal | None

    def validate(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario probability estimate requires scenario_id")
        if not (
            Decimal("0")
            <= self.lower
            <= self.upper
            <= Decimal("1")
        ):
            raise ValueError("scenario probability bounds must lie within [0,1]")
        if self.point is not None and not self.lower <= self.point <= self.upper:
            raise ValueError("scenario point probability must lie within its bounds")


@dataclass(frozen=True)
class ScenarioProbabilityAssemblyCertificate:
    cohort_key: str
    snapshot_hash: str
    dataset_hash: str
    dependence_version: str
    source_certificate_hashes: tuple[str, ...]
    scenario_probabilities: tuple[tuple[str, Decimal], ...]
    status: CalibrationStatus = CalibrationStatus.CALIBRATED

    def validate_for_weighting(self) -> None:
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError("scenario probability assembly is not calibrated")
        if not all(
            (
                self.cohort_key,
                self.snapshot_hash,
                self.dataset_hash,
                self.dependence_version,
                self.source_certificate_hashes,
                self.scenario_probabilities,
            )
        ):
            raise ValueError("scenario probability assembly certificate is incomplete")
        if any(not value for value in self.source_certificate_hashes):
            raise ValueError("scenario assembly contains an unbound source certificate")
        total = sum(
            (probability for _, probability in self.scenario_probabilities),
            Decimal("0"),
        )
        if abs(total - Decimal("1")) > Decimal("1e-12"):
            raise ValueError(
                f"scenario assembly probabilities sum to {total}, not one"
            )

    @property
    def lineage_hash(self) -> str:
        payload = {
            "contract": "scenario_probability_assembly_certificate/v1",
            "cohort_key": self.cohort_key,
            "snapshot_hash": self.snapshot_hash,
            "dataset_hash": self.dataset_hash,
            "dependence_version": self.dependence_version,
            "source_certificate_hashes": self.source_certificate_hashes,
            "scenario_probabilities": [
                (scenario_id, str(probability))
                for scenario_id, probability in self.scenario_probabilities
            ],
            "status": self.status.value,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ScenarioEventAssembly:
    estimates: tuple[ScenarioProbabilityEstimate, ...]
    dependence_method: ScenarioDependenceMethod
    dependence_version: str
    numeric_weighting_allowed: bool
    assembly_hash: str
    dataset_hash: str
    source_certificate_hashes: tuple[str, ...]

    def certificate(self, *, cohort_key: str) -> ScenarioProbabilityAssemblyCertificate:
        if not self.numeric_weighting_allowed:
            raise PermissionError(
                "scenario event assembly has only probability bounds, not authorized point weights"
            )
        points = tuple(
            (item.scenario_id, item.point)
            for item in self.estimates
            if item.point is not None
        )
        if len(points) != len(self.estimates):
            raise PermissionError("scenario assembly has unresolved point probabilities")
        certificate = ScenarioProbabilityAssemblyCertificate(
            cohort_key=cohort_key,
            snapshot_hash=self.assembly_hash,
            dataset_hash=self.dataset_hash,
            dependence_version=self.dependence_version,
            source_certificate_hashes=self.source_certificate_hashes,
            scenario_probabilities=tuple(
                (scenario_id, probability)
                for scenario_id, probability in points
                if probability is not None
            ),
        )
        certificate.validate_for_weighting()
        return certificate


@dataclass(frozen=True)
class ScenarioEventGraph:
    factors: tuple[ScenarioEventFactor, ...]
    rules: tuple[ScenarioEventRule, ...]
    dependence: ScenarioDependenceContract

    def validate(self) -> None:
        if not self.factors or not self.rules:
            raise ValueError("scenario event graph requires factors and rules")
        for factor in self.factors:
            factor.validate()
        for rule in self.rules:
            rule.validate()
        self.dependence.validate()
        factor_ids = {item.event_id for item in self.factors}
        if len(factor_ids) != len(self.factors):
            raise ValueError("scenario event graph has duplicate factor IDs")
        scenario_ids = {item.scenario_id for item in self.rules}
        if len(scenario_ids) != len(self.rules):
            raise ValueError("scenario event graph has duplicate scenario IDs")
        referenced = set()
        for rule in self.rules:
            referenced.update(rule.required_event_ids)
            referenced.update(rule.forbidden_event_ids)
        missing = sorted(referenced - factor_ids)
        if missing:
            raise ValueError(
                f"scenario event graph rules reference unknown factors: {missing}"
            )
        for state in self.dependence.joint_states:
            unknown = sorted(set(state.active_event_ids) - factor_ids)
            if unknown:
                raise ValueError(
                    f"joint state {state.state_id} references unknown factors: {unknown}"
                )


def assemble_scenario_probabilities(
    graph: ScenarioEventGraph,
) -> ScenarioEventAssembly:
    graph.validate()
    factor_map = {item.event_id: item for item in graph.factors}
    source_hashes = tuple(
        sorted(item.calibration_certificate_hash for item in graph.factors)
    )

    if graph.dependence.method is ScenarioDependenceMethod.FRECHET_BOUNDS:
        estimates = tuple(
            _frechet_estimate(rule, factor_map) for rule in graph.rules
        )
        numeric_allowed = False
    else:
        estimates = _state_table_estimates(graph)
        numeric_allowed = True

    for estimate in estimates:
        estimate.validate()

    dataset_payload = {
        "contract": "scenario_event_graph_dataset_lineage/v1",
        "factor_certificate_hashes": source_hashes,
    }
    dataset_hash = sha256(
        json.dumps(dataset_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assembly_payload = {
        "contract": "scenario_event_graph_assembly/v1",
        "factors": [
            (
                item.event_id,
                str(item.probability),
                item.calibration_certificate_hash,
            )
            for item in sorted(graph.factors, key=lambda value: value.event_id)
        ],
        "rules": [
            (
                item.scenario_id,
                sorted(item.required_event_ids),
                sorted(item.forbidden_event_ids),
            )
            for item in sorted(graph.rules, key=lambda value: value.scenario_id)
        ],
        "dependence_method": graph.dependence.method.value,
        "dependence_version": graph.dependence.version,
        "joint_states": [
            (
                item.state_id,
                str(item.probability),
                sorted(item.active_event_ids),
            )
            for item in sorted(
                graph.dependence.joint_states, key=lambda value: value.state_id
            )
        ],
        "estimates": [
            (
                item.scenario_id,
                str(item.lower),
                str(item.upper),
                str(item.point) if item.point is not None else None,
            )
            for item in estimates
        ],
        "dataset_hash": dataset_hash,
    }
    assembly_hash = sha256(
        json.dumps(assembly_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ScenarioEventAssembly(
        estimates=estimates,
        dependence_method=graph.dependence.method,
        dependence_version=graph.dependence.version,
        numeric_weighting_allowed=numeric_allowed,
        assembly_hash=assembly_hash,
        dataset_hash=dataset_hash,
        source_certificate_hashes=source_hashes,
    )


def _state_table_estimates(
    graph: ScenarioEventGraph,
) -> tuple[ScenarioProbabilityEstimate, ...]:
    totals = {rule.scenario_id: Decimal("0") for rule in graph.rules}
    for state in graph.dependence.joint_states:
        active = frozenset(state.active_event_ids)
        matches = tuple(rule for rule in graph.rules if rule.matches(active))
        if len(matches) != 1:
            raise ValueError(
                f"joint state {state.state_id} must map to exactly one scenario, "
                f"matched {[item.scenario_id for item in matches]}"
            )
        totals[matches[0].scenario_id] += state.probability
    total = sum(totals.values(), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("1e-12"):
        raise ValueError("scenario state table does not form an exhaustive partition")
    return tuple(
        ScenarioProbabilityEstimate(
            scenario_id=rule.scenario_id,
            lower=totals[rule.scenario_id],
            upper=totals[rule.scenario_id],
            point=totals[rule.scenario_id],
        )
        for rule in graph.rules
    )


def _frechet_estimate(
    rule: ScenarioEventRule,
    factor_map: dict[str, ScenarioEventFactor],
) -> ScenarioProbabilityEstimate:
    literal_probabilities = [
        factor_map[event_id].probability for event_id in rule.required_event_ids
    ] + [
        Decimal("1") - factor_map[event_id].probability
        for event_id in rule.forbidden_event_ids
    ]
    if not literal_probabilities:
        lower = upper = Decimal("1")
    else:
        lower = max(
            Decimal("0"),
            sum(literal_probabilities, Decimal("0"))
            - Decimal(len(literal_probabilities) - 1),
        )
        upper = min(literal_probabilities)
    return ScenarioProbabilityEstimate(
        scenario_id=rule.scenario_id,
        lower=lower,
        upper=upper,
        point=None,
    )
