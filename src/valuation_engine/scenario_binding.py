from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256

from .assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from .records import CalibrationStatus


class ScenarioBindingStatus(str, Enum):
    BOUND = "bound"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ScenarioBindingSpec:
    scenario_ids: tuple[str, ...]
    required_keys: tuple[str, ...]
    probability_key: str | None = None

    def validate(self) -> None:
        if not self.scenario_ids or not all(self.scenario_ids):
            raise ValueError("scenario binding requires scenario_ids")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("scenario_ids must be unique")
        if not self.required_keys or not all(self.required_keys):
            raise ValueError("scenario binding requires required_keys")
        if len(self.required_keys) != len(set(self.required_keys)):
            raise ValueError("required_keys must be unique")


@dataclass(frozen=True)
class BoundScenario:
    scenario_id: str
    assumptions: tuple[CompiledAssumption, ...]
    probability: Decimal | None = None

    def get(self, key: str) -> CompiledAssumption:
        for item in self.assumptions:
            if item.key == key:
                return item
        raise KeyError(key)


@dataclass(frozen=True)
class BoundScenarioSet:
    target_id: str
    scenarios: tuple[BoundScenario, ...]
    calibration_status: CalibrationStatus
    numeric_weighting_allowed: bool
    scenario_set_hash: str

    def get(self, scenario_id: str) -> BoundScenario:
        for item in self.scenarios:
            if item.scenario_id == scenario_id:
                return item
        raise KeyError(scenario_id)


@dataclass(frozen=True)
class ScenarioBindingFinding:
    code: str
    detail: str
    blocking: bool


@dataclass(frozen=True)
class ScenarioBindingResult:
    status: ScenarioBindingStatus
    scenario_set: BoundScenarioSet | None
    findings: tuple[ScenarioBindingFinding, ...]

    @property
    def passed(self) -> bool:
        return self.status is ScenarioBindingStatus.BOUND and self.scenario_set is not None


def bind_scenarios(
    compiled: CompiledAssumptionSet,
    spec: ScenarioBindingSpec,
) -> ScenarioBindingResult:
    findings: list[ScenarioBindingFinding] = []
    try:
        spec.validate()
    except ValueError as exc:
        return ScenarioBindingResult(
            ScenarioBindingStatus.BLOCKED,
            None,
            (ScenarioBindingFinding("INVALID_BINDING_SPEC", str(exc), True),),
        )

    by_scenario: dict[str, list[CompiledAssumption]] = {scenario_id: [] for scenario_id in spec.scenario_ids}
    for assumption in compiled.assumptions:
        if assumption.scenario_id in by_scenario:
            by_scenario[assumption.scenario_id].append(assumption)

    bound: list[BoundScenario] = []
    probability_candidates: list[Decimal] = []
    probability_calibration: list[CalibrationStatus | None] = []

    for scenario_id in spec.scenario_ids:
        assumptions = tuple(by_scenario[scenario_id])
        keys = {item.key for item in assumptions}
        missing = tuple(key for key in spec.required_keys if key not in keys)
        if missing:
            findings.append(
                ScenarioBindingFinding(
                    "MISSING_REQUIRED_ASSUMPTION",
                    f"{scenario_id}: {', '.join(missing)}",
                    True,
                )
            )
            continue

        probability: Decimal | None = None
        if spec.probability_key is not None:
            matches = tuple(item for item in assumptions if item.key == spec.probability_key)
            if len(matches) != 1:
                findings.append(
                    ScenarioBindingFinding(
                        "MISSING_SCENARIO_PROBABILITY",
                        f"{scenario_id}: expected one {spec.probability_key}",
                        True,
                    )
                )
                continue
            probability_assumption = matches[0]
            try:
                probability = probability_assumption.measure.convert_to("ratio").amount
            except ValueError as exc:
                findings.append(ScenarioBindingFinding("INVALID_PROBABILITY_UNIT", f"{scenario_id}: {exc}", True))
                continue
            if not Decimal("0") <= probability <= Decimal("1"):
                findings.append(
                    ScenarioBindingFinding("INVALID_PROBABILITY_RANGE", f"{scenario_id}: {probability}", True)
                )
                continue
            probability_candidates.append(probability)
            probability_calibration.append(probability_assumption.calibration_status)

        bound.append(BoundScenario(scenario_id, assumptions, probability))

    if any(item.blocking for item in findings):
        return ScenarioBindingResult(ScenarioBindingStatus.BLOCKED, None, tuple(findings))

    calibration_status = CalibrationStatus.UNCALIBRATED
    numeric_weighting_allowed = False
    if spec.probability_key is not None:
        all_calibrated = all(status is CalibrationStatus.CALIBRATED for status in probability_calibration)
        total = sum(probability_candidates, Decimal("0"))
        if all_calibrated and abs(total - Decimal("1")) <= Decimal("1e-12"):
            calibration_status = CalibrationStatus.CALIBRATED
            numeric_weighting_allowed = True
        else:
            if any(status is CalibrationStatus.DEGRADED for status in probability_calibration):
                calibration_status = CalibrationStatus.DEGRADED
            elif any(status is CalibrationStatus.CALIBRATING for status in probability_calibration):
                calibration_status = CalibrationStatus.CALIBRATING
            findings.append(
                ScenarioBindingFinding(
                    "PROBABILITY_WEIGHTING_WITHHELD",
                    "scenario probabilities remain descriptive until all weights are CALIBRATED and sum to one",
                    False,
                )
            )
            bound = [BoundScenario(item.scenario_id, item.assumptions, None) for item in bound]

    serialized = "\n".join(
        [compiled.assumption_set_hash, calibration_status.value]
        + [
            f"{scenario.scenario_id}|{scenario.probability if scenario.probability is not None else 'NA'}|"
            + ",".join(sorted(f"{item.key}:{item.measure.amount}:{item.measure.unit}" for item in scenario.assumptions))
            for scenario in bound
        ]
    )
    scenario_set = BoundScenarioSet(
        target_id=compiled.target_id,
        scenarios=tuple(bound),
        calibration_status=calibration_status,
        numeric_weighting_allowed=numeric_weighting_allowed,
        scenario_set_hash=sha256(serialized.encode("utf-8")).hexdigest(),
    )
    return ScenarioBindingResult(ScenarioBindingStatus.BOUND, scenario_set, tuple(findings))
