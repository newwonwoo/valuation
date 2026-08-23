from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Callable

from .actual_units import Measure
from .evaluator_registry import EvaluatorRegistry, ModelKey, SegmentValuation, ValueKind
from .method_capabilities import MethodCapabilityRegistry, require_execution_family
from .orchestrator import OrchestratorContext
from .probability_calibration import CalibrationCertificate
from .records import CalibrationStatus
from .risk_adapters import LiveWACCStageResult
from .scenario_binding import BoundScenario


_FORBIDDEN_PRE_FREEZE_KEYS = {
    "current_market_price",
    "market_price",
    "market_observation",
    "target_market_cap",
    "target_price",
    "consensus_target",
    "target_multiple",
    "street_reference",
}


@dataclass(frozen=True)
class LiveRNPVRegistration:
    archetype: str
    method: str
    version: str
    final_year: int
    calibration_cohort_key: str
    probability_key: str = "probability_of_success"
    assumption_prefix: str = ""

    def validate(self) -> None:
        if not all(
            (
                self.archetype,
                self.method,
                self.version,
                self.calibration_cohort_key,
                self.probability_key,
            )
        ):
            raise ValueError("rNPV registration requires identity, cohort and probability key")
        if self.final_year < 1 or self.final_year > 40:
            raise ValueError("rNPV final_year must be in [1, 40]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")


@dataclass(frozen=True)
class CalibratedRNPVEvaluator:
    archetype: str
    method: str
    version: str
    final_year: int
    calibration_cohort_key: str
    calibration_snapshot_hash: str
    discount_rate: Decimal
    discount_rate_path_id: str
    beta_path_id: str
    probability_key: str = "probability_of_success"
    assumption_prefix: str = ""

    def __post_init__(self) -> None:
        required = (
            self.archetype,
            self.method,
            self.version,
            self.calibration_cohort_key,
            self.calibration_snapshot_hash,
            self.discount_rate_path_id,
            self.beta_path_id,
            self.probability_key,
        )
        if any(not value for value in required):
            raise ValueError("calibrated rNPV evaluator requires identity, calibration and risk paths")
        if self.final_year < 1 or self.final_year > 40:
            raise ValueError("rNPV final_year must be in [1, 40]")
        if not self.discount_rate.is_finite() or self.discount_rate <= 0:
            raise ValueError("rNPV discount rate must be finite and positive")

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    def _unconditional_key(self, year: int) -> str:
        return f"{self.assumption_prefix}unconditional_cashflow_year_{year}"

    def _contingent_key(self, year: int) -> str:
        return f"{self.assumption_prefix}contingent_cashflow_year_{year}"

    def _probability_key(self) -> str:
        return f"{self.assumption_prefix}{self.probability_key}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (
            *(self._unconditional_key(year) for year in range(0, self.final_year + 1)),
            *(self._contingent_key(year) for year in range(0, self.final_year + 1)),
            self._probability_key(),
        )

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        probability_assumption = scenario.get(self._probability_key())
        if probability_assumption.calibration_status is not CalibrationStatus.CALIBRATED:
            raise PermissionError("rNPV probability assumption must be CALIBRATED")
        probability = probability_assumption.measure.convert_to("ratio").amount
        if not Decimal("0") < probability < Decimal("1"):
            raise ValueError("unresolved rNPV probability must be strictly between zero and one")

        unconditional = tuple(
            scenario.get(self._unconditional_key(year)) for year in range(0, self.final_year + 1)
        )
        contingent = tuple(
            scenario.get(self._contingent_key(year)) for year in range(0, self.final_year + 1)
        )
        first_measure = unconditional[0].measure
        if first_measure.dimension.value != "money":
            raise ValueError("rNPV cash flows must use money measures")
        unconditional_measures = tuple(item.measure.convert_to(first_measure.unit) for item in unconditional)
        contingent_measures = tuple(item.measure.convert_to(first_measure.unit) for item in contingent)

        one = Decimal("1")
        present_value = Decimal("0")
        for year in range(0, self.final_year + 1):
            expected_cashflow = (
                unconditional_measures[year].amount
                + probability * contingent_measures[year].amount
            )
            present_value += expected_cashflow / (one + self.discount_rate) ** year

        as_of = max(
            probability_assumption.measure.as_of,
            *(item.measure.as_of for item in unconditional),
            *(item.measure.as_of for item in contingent),
        )
        economic_paths = tuple(
            dict.fromkeys(
                (
                    *(item.economic_path_id for item in unconditional),
                    *(item.economic_path_id for item in contingent),
                    probability_assumption.economic_path_id,
                    f"calibration:{self.calibration_snapshot_hash}:{self.calibration_cohort_key}",
                    f"{self.discount_rate_path_id}:{segment_id}",
                    f"{self.beta_path_id}:{segment_id}",
                )
            )
        )
        return SegmentValuation(
            contribution_id=(
                f"{segment_id}:{scenario.scenario_id}:{self.evaluator_id}:v{self.version}"
            ),
            segment_id=segment_id,
            scenario_id=scenario.scenario_id,
            value_kind=ValueKind.ENTERPRISE_VALUE,
            value=Measure(present_value, first_measure.unit, as_of),
            economic_path_ids=economic_paths,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.version,
        )


RegistryLoader = Callable[[OrchestratorContext], EvaluatorRegistry]


def _certificate_for_context(
    context: OrchestratorContext,
    cohort_key: str,
) -> CalibrationCertificate:
    certificates = context.data.get("probability_calibration_certificates")
    if certificates is not None:
        if not isinstance(certificates, dict):
            raise TypeError("probability_calibration_certificates must be a mapping")
        certificate = certificates.get(cohort_key)
    else:
        certificate = context.data.get("probability_calibration_certificate")
    if not isinstance(certificate, CalibrationCertificate):
        raise PermissionError(f"no CalibrationCertificate is available for rNPV cohort {cohort_key}")
    certificate.validate_for_weighting()
    if certificate.cohort_key != cohort_key:
        raise PermissionError(
            f"rNPV certificate cohort {certificate.cohort_key} does not match {cohort_key}"
        )
    return certificate


def live_rnpv_registry_loader(
    *,
    registrations: tuple[LiveRNPVRegistration, ...],
    base_loader: RegistryLoader | None = None,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> RegistryLoader:
    if not registrations:
        raise ValueError("live rNPV registry loader requires registrations")
    for registration in registrations:
        registration.validate()
        require_execution_family(
            archetype=registration.archetype,
            method=registration.method,
            expected_family="calibrated_single_event_rnpv",
            registry=capability_registry,
        )
    keys = tuple(ModelKey(item.archetype, item.method, item.version) for item in registrations)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate rNPV ModelKey registration")

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        leaked = tuple(sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data))
        if leaked:
            raise PermissionError(
                "pre-freeze rNPV context contains target Street/market fields: "
                + ", ".join(leaked)
            )
        wacc_result = context.data.get("live_wacc_result")
        if not isinstance(wacc_result, LiveWACCStageResult):
            raise ValueError("LiveWACCStageResult is required to build rNPV evaluators")
        wacc = wacc_result.wacc_result.wacc
        if not isfinite(wacc) or wacc <= 0:
            raise ValueError("live WACC must be finite and positive")
        registry = base_loader(context) if base_loader is not None else EvaluatorRegistry()
        for item in registrations:
            certificate = _certificate_for_context(context, item.calibration_cohort_key)
            registry.register(
                CalibratedRNPVEvaluator(
                    archetype=item.archetype,
                    method=item.method,
                    version=item.version,
                    final_year=item.final_year,
                    calibration_cohort_key=item.calibration_cohort_key,
                    calibration_snapshot_hash=certificate.snapshot_hash,
                    discount_rate=Decimal(str(wacc)),
                    discount_rate_path_id=f"wacc:{wacc_result.snapshot_hash}",
                    beta_path_id=f"beta:{wacc_result.beta_result.snapshot_hash}",
                    probability_key=item.probability_key,
                    assumption_prefix=item.assumption_prefix,
                )
            )
        return registry

    return load