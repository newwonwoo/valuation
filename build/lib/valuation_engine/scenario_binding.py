from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256

from .assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from .probability_calibration import CalibrationCertificate
from .records import CalibrationStatus


class ScenarioBindingStatus(str, Enum):
    BOUND = "bound"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ScenarioBindingSpec:
    scenario_ids: tuple[str, ...]
    required_keys: tuple[str, ...]
    probability_key: str | None = None
    calibration_cohort_key: str | None = None

    def validate(self) -> None:
        if not self.scenario_ids or not all(self.scenario_ids):
            raise ValueError("scenario binding requires scenario_ids")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("scenario_ids must be unique")
        if not self.required_keys or not all(self.required_keys):
            raise ValueError("scenario binding requires required_keys")
        if len(self.required_keys) != len(set(self.required_keys)):
            raise ValueError("required_keys must be unique")
        if self.calibration_cohort_key is not None and self.probability_key is None:
            raise ValueError("calibration_cohort_key requires a probability_key")


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
    calibration_snapshot_hash: str | None = None

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
    *,
    calibration_certificate: CalibrationCertificate | None = None,
    require_calibration_certificate: bool = False,
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
    calibration_snapshot_hash: str | None = None
    if spec.probability_key is not None:
        all_calibrated = all(status is CalibrationStatus.CALIBRATED for status in probability_calibration)
        total = sum(probability_candidates, Decimal("0"))
        if all_calibrated:
            if require_calibration_certificate:
                if calibration_certificate is None:
                    return ScenarioBindingResult(
                        ScenarioBindingStatus.BLOCKED,
                        None,
                        (
                            ScenarioBindingFinding(
                                "CALIBRATION_CERTIFICATE_REQUIRED",
                                "LIVE_PRIMARY numeric probability weighting requires a calibration certificate",
                                True,
                            ),
                        ),
                    )
                if not spec.calibration_cohort_key:
                    return ScenarioBindingResult(
                        ScenarioBindingStatus.BLOCKED,
                        None,
                        (
                            ScenarioBindingFinding(
                                "CALIBRATION_COHORT_REQUIRED",
                                "LIVE_PRIMARY probability weighting requires an explicit calibration cohort key",
                                True,
                            ),
                        ),
                    )
                try:
                    calibration_certificate.validate_for_weighting()
                except (PermissionError, ValueError) as exc:
                    return ScenarioBindingResult(
                        ScenarioBindingStatus.BLOCKED,
                        None,
                        (ScenarioBindingFinding("INVALID_CALIBRATION_CERTIFICATE", str(exc), True),),
                    )
                if calibration_certificate.cohort_key != spec.calibration_cohort_key:
                    return ScenarioBindingResult(
                        ScenarioBindingStatus.BLOCKED,
                        None,
                        (
                            ScenarioBindingFinding(
                                "CALIBRATION_COHORT_MISMATCH",
                                f"certificate cohort {calibration_certificate.cohort_key} does not match {spec.calibration_cohort_key}",
                                True,
                            ),
                        ),
                    )
                calibration_snapshot_hash = calibration_certificate.snapshot_hash
            elif calibration_certificate is not None:
                try:
                    calibration_certificate.validate_for_weighting()
                    if spec.calibration_cohort_key and calibration_certificate.cohort_key != spec.calibration_cohort_key:
                        raise ValueError("calibration certificate cohort mismatch")
                    calibration_snapshot_hash = calibration_certificate.snapshot_hash
                except (PermissionError, ValueError) as exc:
                    return ScenarioBindingResult(
                        ScenarioBindingStatus.BLOCKED,
                        None,
                        (ScenarioBindingFinding("INVALID_CALIBRATION_CERTIFICATE", str(exc), True),),
                    )
            if abs(total - Decimal("1")) > Decimal("1e-12"):
                return ScenarioBindingResult(
                    ScenarioBindingStatus.BLOCKED,
                    None,
                    (
                        ScenarioBindingFinding(
                            "CALIBRATED_PROBABILITY_SUM_INVALID",
                            f"calibrated scenario probabilities sum to {total}, not one",
                            True,
                        ),
                    ),
                )
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
                    "scenario probabilities remain descriptive until all weights are CALIBRATED",
                    False,
                )
            )
            bound = [BoundScenario(item.scenario_id, item.assumptions, None) for item in bound]

    serialized = "\n".join(
        [compiled.assumption_set_hash, calibration_status.value, calibration_snapshot_hash or "NO_CERTIFICATE"]
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
        calibration_snapshot_hash=calibration_snapshot_hash,
    )
    return ScenarioBindingResult(ScenarioBindingStatus.BOUND, scenario_set, tuple(findings))
