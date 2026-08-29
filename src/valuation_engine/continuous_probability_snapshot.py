from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

from .continuous_financial_path_probability import ContinuousScenarioEstimate
from .probability_calibration import CalibrationCertificate
from .records import CalibrationStatus


@dataclass(frozen=True)
class ContinuousOOSDriverDiagnostic:
    driver_id: str
    skill_windows: tuple[Decimal, ...]
    likelihood_weight: Decimal
    uncertainty_inflation: Decimal
    resolved_cases: int
    company_count: int
    quarter_count: int
    regime_similarity: Decimal

    def validate(self) -> None:
        if not self.driver_id or not self.skill_windows:
            raise ValueError("continuous OOS diagnostic requires driver and skill windows")
        if any(not value.is_finite() for value in self.skill_windows):
            raise ValueError("continuous OOS skill windows must be finite")
        if not Decimal("0") < self.likelihood_weight <= Decimal("1"):
            raise ValueError("continuous OOS likelihood weight must lie within (0,1]")
        if self.uncertainty_inflation < Decimal("1"):
            raise ValueError("continuous OOS uncertainty inflation cannot be below one")
        if min(self.resolved_cases, self.company_count, self.quarter_count) <= 0:
            raise ValueError("continuous OOS breadth counts must be positive")
        if not Decimal("0") <= self.regime_similarity <= Decimal("1"):
            raise ValueError("continuous OOS regime similarity must lie within [0,1]")


@dataclass(frozen=True)
class ContinuousProbabilityCalibrationSnapshot:
    cohort_key: str
    forecast_class: str
    horizon: str
    as_of_date: str
    method_version: str
    mapping_version: str
    probability_source: str
    estimates: tuple[ContinuousScenarioEstimate, ...]
    driver_snapshot_hashes: tuple[tuple[str, str], ...]
    dependence_hash: str
    simulation_hash: str
    dataset_hash: str
    oos_diagnostics: tuple[ContinuousOOSDriverDiagnostic, ...]
    integrity_findings: tuple[str, ...]
    status: CalibrationStatus
    snapshot_hash: str

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
                self.dependence_hash,
                self.simulation_hash,
                self.dataset_hash,
                self.snapshot_hash,
            )
        ):
            raise ValueError("continuous probability snapshot identity is incomplete")
        date.fromisoformat(self.as_of_date[:10])
        if self.probability_source != "continuous_financial_path_monte_carlo":
            raise ValueError("continuous probability snapshot source must remain continuous financial-path Monte Carlo")
        if not self.estimates:
            raise ValueError("continuous probability snapshot requires scenario estimates")
        ids = tuple(item.scenario_id for item in self.estimates)
        if len(ids) != len(set(ids)):
            raise ValueError("continuous probability snapshot contains duplicate scenarios")
        total = sum((item.probability for item in self.estimates), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("1e-12"):
            raise ValueError("continuous scenario probabilities must sum to one")
        for item in self.estimates:
            if not all(
                value.is_finite()
                for value in (
                    item.probability,
                    item.lower_probability,
                    item.upper_probability,
                )
            ):
                raise ValueError("continuous scenario estimate contains non-finite probability")
            if not (
                Decimal("0") <= item.lower_probability
                <= item.probability
                <= item.upper_probability
                <= Decimal("1")
            ):
                raise ValueError("continuous scenario estimate interval is invalid")
        driver_ids = tuple(driver_id for driver_id, _ in self.driver_snapshot_hashes)
        if not driver_ids or len(driver_ids) != len(set(driver_ids)):
            raise ValueError("continuous probability snapshot driver hashes are invalid")
        if any(not digest for _, digest in self.driver_snapshot_hashes):
            raise ValueError("continuous probability snapshot driver hash is empty")
        diagnostic_ids = tuple(item.driver_id for item in self.oos_diagnostics)
        if set(diagnostic_ids) != set(driver_ids):
            raise ValueError("continuous OOS diagnostics must cover every modeled driver")
        for item in self.oos_diagnostics:
            item.validate()
        if self.status is CalibrationStatus.CALIBRATED and self.integrity_findings:
            raise ValueError("CALIBRATED continuous probability snapshot cannot contain integrity failures")
        if self.snapshot_hash != self.expected_hash():
            raise ValueError("continuous probability snapshot hash mismatch")

    @property
    def probabilities(self) -> tuple[tuple[str, Decimal], ...]:
        return tuple((item.scenario_id, item.probability) for item in self.estimates)

    def probability_for(self, scenario_id: str) -> Decimal:
        for item in self.estimates:
            if item.scenario_id == scenario_id:
                return item.probability
        raise KeyError(scenario_id)

    def expected_hash(self) -> str:
        payload = {
            "contract": "continuous_probability_calibration_snapshot/v1",
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
            "driver_snapshot_hashes": list(self.driver_snapshot_hashes),
            "dependence_hash": self.dependence_hash,
            "simulation_hash": self.simulation_hash,
            "dataset_hash": self.dataset_hash,
            "oos_diagnostics": [
                {
                    "driver_id": item.driver_id,
                    "skill_windows": [str(value) for value in item.skill_windows],
                    "likelihood_weight": str(item.likelihood_weight),
                    "uncertainty_inflation": str(item.uncertainty_inflation),
                    "resolved_cases": item.resolved_cases,
                    "company_count": item.company_count,
                    "quarter_count": item.quarter_count,
                    "regime_similarity": str(item.regime_similarity),
                }
                for item in self.oos_diagnostics
            ],
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
            raise PermissionError("continuous probability snapshot has not passed integrity calibration")
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
        cohort_key: str,
        forecast_class: str,
        horizon: str,
        as_of_date: str,
        method_version: str,
        mapping_version: str,
        estimates: tuple[ContinuousScenarioEstimate, ...],
        driver_snapshot_hashes: tuple[tuple[str, str], ...],
        dependence_hash: str,
        simulation_hash: str,
        dataset_hash: str,
        oos_diagnostics: tuple[ContinuousOOSDriverDiagnostic, ...],
        integrity_findings: tuple[str, ...] = (),
    ) -> "ContinuousProbabilityCalibrationSnapshot":
        status = (
            CalibrationStatus.CALIBRATED
            if not integrity_findings
            else CalibrationStatus.DEGRADED
        )
        provisional = cls(
            cohort_key=cohort_key,
            forecast_class=forecast_class,
            horizon=horizon,
            as_of_date=as_of_date,
            method_version=method_version,
            mapping_version=mapping_version,
            probability_source="continuous_financial_path_monte_carlo",
            estimates=estimates,
            driver_snapshot_hashes=driver_snapshot_hashes,
            dependence_hash=dependence_hash,
            simulation_hash=simulation_hash,
            dataset_hash=dataset_hash,
            oos_diagnostics=oos_diagnostics,
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
