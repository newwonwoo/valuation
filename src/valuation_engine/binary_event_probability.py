"""Runtime entrance for the binary-event probability route (Route A).

``probability_engine_v3`` already computes scenario probabilities from
hierarchical Bayesian event posteriors and a copula Monte Carlo. What it did not
have was a way in: nothing in the engine called ``run_probability_engine_v3``,
and its result type was not one the SCENARIO_BUILD calibration socket accepts.
The engine's two other generators — ``simulate_scenario_posterior`` and
``build_dynamic_hierarchical_posterior`` — are reachable only through it, so the
whole branch was an island.

This module is the bridge. A :class:`BinaryEventCalibrationBinding` declares the
cohort identity and the simulation controls; the assembler runs the engine over
provider-supplied event Evidence and seals the outcome into a snapshot that
issues the same canonical :class:`CalibrationCertificate` the continuous route
issues. Route A and Route B therefore reach the runtime through one socket.

Nothing here re-derives a probability. The arithmetic stays in
``probability_engine_v3``; this module supplies identity, integrity findings and
issuance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

from .continuous_predictive_weight import ContinuousWeightPolicy
from .probability_calibration import CalibrationCertificate
from .probability_engine_v3 import (
    ProbabilityEngineV3Result,
    ProbabilityEngineV3Spec,
    ProbabilityEngineV3Status,
    ProbabilityEventInput,
    run_probability_engine_v3,
)
from .records import CalibrationStatus
from .scenario_posterior_monte_carlo import CorrelationDependence, PosteriorScenarioRule


PROBABILITY_SOURCE = "binary_event_posterior_monte_carlo"


class BinaryEventCalibrationError(ValueError):
    """Raised when a binary-event calibration does not satisfy its binding."""


@dataclass(frozen=True)
class BinaryEventCalibrationBinding:
    """Cohort identity and simulation controls for one binary-event calibration.

    The binding names the cohort; the events themselves arrive from the provider
    as Evidence-derived inputs, because a binary-event cohort is fitted on
    resolved outcomes rather than read from a frozen driver artifact.
    """

    cohort_key: str
    forecast_class: str
    horizon: str
    method_version: str
    mapping_version: str
    scenario_ids: tuple[str, ...]
    credible_level: Decimal = Decimal("0.90")
    outer_draws: int = 300
    inner_draws: int = 200
    seed: int = 0

    def validate(self) -> None:
        if not all(
            (
                self.cohort_key,
                self.forecast_class,
                self.horizon,
                self.method_version,
                self.mapping_version,
            )
        ):
            raise BinaryEventCalibrationError(
                "binary-event calibration binding identity is incomplete"
            )
        if len(self.scenario_ids) < 2 or len(set(self.scenario_ids)) != len(
            self.scenario_ids
        ):
            raise BinaryEventCalibrationError(
                "binary-event calibration binding requires at least two distinct scenarios"
            )
        if not Decimal("0") < self.credible_level < Decimal("1"):
            raise BinaryEventCalibrationError(
                "binary-event calibration credible level must lie within (0,1)"
            )
        if min(self.outer_draws, self.inner_draws) <= 0:
            raise BinaryEventCalibrationError(
                "binary-event calibration draw counts must be positive"
            )


@dataclass(frozen=True)
class BinaryEventScenarioEstimate:
    scenario_id: str
    probability: Decimal
    lower_probability: Decimal
    upper_probability: Decimal


@dataclass(frozen=True)
class BinaryEventProbabilityCalibrationSnapshot:
    """Sealed binary-event probability distribution, ready for the runtime socket.

    Shares the certificate boundary with the v1 single-cohort, v2 hierarchical
    and v3.2 continuous snapshots: a snapshot that is not CALIBRATED is a
    monitoring artifact and refuses to issue a certificate.
    """

    cohort_key: str
    forecast_class: str
    horizon: str
    as_of_date: str
    method_version: str
    mapping_version: str
    probability_source: str
    estimates: tuple[BinaryEventScenarioEstimate, ...]
    event_snapshot_hashes: tuple[tuple[str, str], ...]
    simulation_hash: str
    dataset_hash: str
    integrity_findings: tuple[str, ...]
    status: CalibrationStatus
    snapshot_hash: str

    @property
    def probabilities(self) -> tuple[tuple[str, Decimal], ...]:
        return tuple((item.scenario_id, item.probability) for item in self.estimates)

    def probability_for(self, scenario_id: str) -> Decimal:
        for item in self.estimates:
            if item.scenario_id == scenario_id:
                return item.probability
        raise KeyError(scenario_id)

    def validate(self) -> None:
        if not all(
            (
                self.cohort_key,
                self.forecast_class,
                self.horizon,
                self.as_of_date,
                self.method_version,
                self.mapping_version,
                self.probability_source,
                self.simulation_hash,
                self.dataset_hash,
                self.snapshot_hash,
            )
        ):
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot identity is incomplete"
            )
        date.fromisoformat(self.as_of_date[:10])
        if self.probability_source != PROBABILITY_SOURCE:
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot source must remain "
                "binary-event posterior Monte Carlo"
            )
        if not self.estimates:
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot requires scenario estimates"
            )
        ids = tuple(item.scenario_id for item in self.estimates)
        if len(ids) != len(set(ids)):
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot contains duplicate scenarios"
            )
        total = sum((item.probability for item in self.estimates), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("1e-12"):
            raise BinaryEventCalibrationError(
                "binary-event scenario probabilities must sum to one"
            )
        for item in self.estimates:
            if not all(
                value.is_finite()
                for value in (
                    item.probability,
                    item.lower_probability,
                    item.upper_probability,
                )
            ):
                raise BinaryEventCalibrationError(
                    "binary-event scenario estimate contains non-finite probability"
                )
            if not (
                Decimal("0")
                <= item.lower_probability
                <= item.probability
                <= item.upper_probability
                <= Decimal("1")
            ):
                raise BinaryEventCalibrationError(
                    "binary-event scenario estimate interval is invalid"
                )
        event_ids = tuple(event_id for event_id, _ in self.event_snapshot_hashes)
        if not event_ids or len(event_ids) != len(set(event_ids)):
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot event lineage is invalid"
            )
        if any(not digest for _, digest in self.event_snapshot_hashes):
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot event hash is empty"
            )
        if self.status is CalibrationStatus.CALIBRATED and self.integrity_findings:
            raise BinaryEventCalibrationError(
                "CALIBRATED binary-event snapshot cannot contain integrity failures"
            )
        if self.snapshot_hash != self.expected_hash():
            raise BinaryEventCalibrationError(
                "binary-event probability snapshot hash mismatch"
            )

    def expected_hash(self) -> str:
        payload = {
            "contract": "binary_event_probability_calibration_snapshot/v1",
            "cohort_key": self.cohort_key,
            "forecast_class": self.forecast_class,
            "horizon": self.horizon,
            "as_of_date": self.as_of_date,
            "method_version": self.method_version,
            "mapping_version": self.mapping_version,
            "probability_source": self.probability_source,
            "estimates": [
                {
                    "scenario_id": item.scenario_id,
                    "probability": str(item.probability),
                    "lower_probability": str(item.lower_probability),
                    "upper_probability": str(item.upper_probability),
                }
                for item in self.estimates
            ],
            "event_snapshot_hashes": list(self.event_snapshot_hashes),
            "simulation_hash": self.simulation_hash,
            "dataset_hash": self.dataset_hash,
            "integrity_findings": list(self.integrity_findings),
            "status": self.status.value,
        }
        return sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def certificate(self) -> CalibrationCertificate:
        self.validate()
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError(
                "binary-event probability snapshot has not passed integrity calibration"
            )
        certificate = CalibrationCertificate(
            cohort_key=self.cohort_key,
            forecast_class=self.forecast_class,
            horizon=self.horizon,
            policy_version=self.method_version,
            mapping_version=self.mapping_version,
            snapshot_hash=self.snapshot_hash,
            status=self.status,
            dataset_hash=self.dataset_hash,
        )
        certificate.validate_for_weighting()
        return certificate

    @classmethod
    def build(
        cls,
        *,
        binding: BinaryEventCalibrationBinding,
        as_of_date: str,
        estimates: tuple[BinaryEventScenarioEstimate, ...],
        event_snapshot_hashes: tuple[tuple[str, str], ...],
        simulation_hash: str,
        dataset_hash: str,
        integrity_findings: tuple[str, ...] = (),
    ) -> "BinaryEventProbabilityCalibrationSnapshot":
        status = (
            CalibrationStatus.CALIBRATED
            if not integrity_findings
            else CalibrationStatus.DEGRADED
        )
        provisional = cls(
            cohort_key=binding.cohort_key,
            forecast_class=binding.forecast_class,
            horizon=binding.horizon,
            as_of_date=as_of_date,
            method_version=binding.method_version,
            mapping_version=binding.mapping_version,
            probability_source=PROBABILITY_SOURCE,
            estimates=estimates,
            event_snapshot_hashes=event_snapshot_hashes,
            simulation_hash=simulation_hash,
            dataset_hash=dataset_hash,
            integrity_findings=integrity_findings,
            status=status,
            snapshot_hash="PENDING",
        )
        completed = cls(
            **{
                **provisional.__dict__,
                "snapshot_hash": provisional.expected_hash(),
            }
        )
        completed.validate()
        return completed


def _estimates(
    result: ProbabilityEngineV3Result,
) -> tuple[tuple[BinaryEventScenarioEstimate, ...], tuple[str, ...]]:
    """Pair each normalised probability with its credible interval.

    The engine normalises point probabilities across the scenario rules but
    reports intervals as simulated. A rule set that does not partition the event
    space can therefore push a normalised point outside its own interval. That is
    a real modelling defect, not a rounding artifact, so it is recorded as an
    integrity finding and degrades the snapshot rather than being clamped away.
    """
    intervals = {
        scenario_id: (lower, upper)
        for scenario_id, lower, upper in result.scenario_intervals
    }
    estimates: list[BinaryEventScenarioEstimate] = []
    findings: list[str] = []
    for scenario_id, probability in result.scenario_probabilities:
        if scenario_id not in intervals:
            findings.append(f"missing_credible_interval:{scenario_id}")
            continue
        lower, upper = intervals[scenario_id]
        if not lower <= probability <= upper:
            findings.append(f"normalised_probability_outside_interval:{scenario_id}")
        estimates.append(
            BinaryEventScenarioEstimate(
                scenario_id=scenario_id,
                probability=probability,
                lower_probability=lower,
                upper_probability=upper,
            )
        )
    return tuple(estimates), tuple(sorted(findings))


class BinaryEventProbabilityBlocked(PermissionError):
    """Raised when the engine refuses to produce a distribution at all.

    Carries the engine's integrity violations so the calibration stage can report
    exactly which event evidence failed rather than a generic load failure.
    """

    def __init__(
        self,
        violations: tuple[str, ...],
        event_snapshot_hashes: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.violations = violations
        self.event_snapshot_hashes = event_snapshot_hashes
        super().__init__(
            "binary-event probability engine is data-blocked: "
            + (", ".join(violations) if violations else "no scenario simulation")
        )


def build_binary_event_probability_snapshot(
    *,
    binding: BinaryEventCalibrationBinding,
    events: tuple[ProbabilityEventInput, ...],
    scenario_rules: tuple[PosteriorScenarioRule, ...],
    dependence: CorrelationDependence,
    as_of_date: str,
    weight_policy: ContinuousWeightPolicy = ContinuousWeightPolicy(),
) -> BinaryEventProbabilityCalibrationSnapshot:
    """Run the binary-event probability engine and seal the result for the runtime.

    A ``DATA_BLOCKED`` engine result raises :class:`BinaryEventProbabilityBlocked`
    carrying the engine's own violations. A run that estimates but whose
    normalised probabilities fall outside their credible intervals seals as
    DEGRADED, so it reaches SCENARIO_BUILD as a monitoring artifact and
    probabilities stay descriptive. Only a clean run issues a certificate.
    """
    binding.validate()
    rule_ids = tuple(rule.scenario_id for rule in scenario_rules)
    if set(rule_ids) != set(binding.scenario_ids):
        raise BinaryEventCalibrationError(
            "binary-event scenario rules must cover exactly the bound scenarios"
        )
    spec = ProbabilityEngineV3Spec(
        cohort_key=binding.cohort_key,
        horizon=binding.horizon,
        events=events,
        scenario_rules=scenario_rules,
        dependence=dependence,
        credible_level=binding.credible_level,
        outer_draws=binding.outer_draws,
        inner_draws=binding.inner_draws,
        seed=binding.seed,
    )
    result = run_probability_engine_v3(spec, weight_policy=weight_policy)
    event_snapshot_hashes = tuple(
        (item.event_id, item.posterior.snapshot_hash) for item in result.event_results
    )
    if result.status is not ProbabilityEngineV3Status.ESTIMATED:
        # A blocked run has no distribution to seal. Raising here keeps the
        # engine's own violations attached, and the calibration stage turns them
        # into a blocking finding naming the event evidence that failed.
        raise BinaryEventProbabilityBlocked(
            result.integrity_violations, event_snapshot_hashes
        )
    estimates, findings = _estimates(result)
    return BinaryEventProbabilityCalibrationSnapshot.build(
        binding=binding,
        as_of_date=as_of_date,
        estimates=estimates,
        event_snapshot_hashes=event_snapshot_hashes,
        simulation_hash=(
            result.scenario_simulation.simulation_hash
            if result.scenario_simulation is not None
            else ""
        ),
        dataset_hash=result.dataset_hash,
        integrity_findings=findings,
    )


def binary_event_probability_loader(
    *,
    binding: BinaryEventCalibrationBinding,
    events: tuple[ProbabilityEventInput, ...],
    scenario_rules: tuple[PosteriorScenarioRule, ...],
    dependence: CorrelationDependence,
    as_of_date: str,
    weight_policy: ContinuousWeightPolicy = ContinuousWeightPolicy(),
):
    """Provider-side loader for ``LiveProviders.calibration_loader``."""

    def load(_context) -> BinaryEventProbabilityCalibrationSnapshot:
        return build_binary_event_probability_snapshot(
            binding=binding,
            events=events,
            scenario_rules=scenario_rules,
            dependence=dependence,
            as_of_date=as_of_date,
            weight_policy=weight_policy,
        )

    return load
