from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


DEFAULT_KR_CALIBRATION_COHORT_REGISTRY = (
    Path(__file__).resolve().parents[2] / "config" / "kr_calibration_cohort_registry.yaml"
)


class CalibrationCohortRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ProductionCalibrationCohort:
    registry_id: str
    ksic_prefix: str
    forecast_years: int
    scenario_ids: tuple[str, ...]
    cohort_key: str
    forecast_class: str
    external_probability_source: str
    status: str = "PRODUCTION"

    def validate(self) -> None:
        if not self.registry_id:
            raise CalibrationCohortRegistryError("cohort registry_id is required")
        if not self.ksic_prefix.isdigit():
            raise CalibrationCohortRegistryError(
                f"cohort {self.registry_id} requires a numeric ksic_prefix"
            )
        if self.forecast_years < 1:
            raise CalibrationCohortRegistryError(
                f"cohort {self.registry_id} requires positive forecast_years"
            )
        if not self.scenario_ids or len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise CalibrationCohortRegistryError(
                f"cohort {self.registry_id} requires unique scenario_ids"
            )
        if not all(
            (self.cohort_key, self.forecast_class, self.external_probability_source)
        ):
            raise CalibrationCohortRegistryError(
                f"cohort {self.registry_id} is missing probability binding identity"
            )
        if self.status != "PRODUCTION":
            raise CalibrationCohortRegistryError(
                f"cohort {self.registry_id} must be PRODUCTION in this registry"
            )


@dataclass(frozen=True)
class ProductionCalibrationRegistry:
    version: int
    cohorts: tuple[ProductionCalibrationCohort, ...]

    def validate(self) -> None:
        if self.version != 1:
            raise CalibrationCohortRegistryError(
                f"unsupported calibration cohort registry version: {self.version}"
            )
        ids = tuple(item.registry_id for item in self.cohorts)
        if len(ids) != len(set(ids)):
            raise CalibrationCohortRegistryError("cohort registry_id values must be unique")
        for cohort in self.cohorts:
            cohort.validate()
        signatures = tuple(
            (
                item.ksic_prefix,
                item.forecast_years,
                item.scenario_ids,
            )
            for item in self.cohorts
        )
        if len(signatures) != len(set(signatures)):
            raise CalibrationCohortRegistryError(
                "duplicate production cohort match signatures are forbidden"
            )


def load_production_calibration_registry(
    path: str | Path = DEFAULT_KR_CALIBRATION_COHORT_REGISTRY,
) -> ProductionCalibrationRegistry:
    registry_path = Path(path)
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CalibrationCohortRegistryError("calibration cohort registry must be a mapping")
    rows = payload.get("cohorts")
    if not isinstance(rows, list):
        raise CalibrationCohortRegistryError("calibration cohort registry requires cohorts")
    cohorts: list[ProductionCalibrationCohort] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CalibrationCohortRegistryError(f"cohorts[{index}] must be a mapping")
        try:
            cohort = ProductionCalibrationCohort(
                registry_id=str(row["registry_id"]),
                ksic_prefix=str(row["ksic_prefix"]),
                forecast_years=int(row["forecast_years"]),
                scenario_ids=tuple(str(item) for item in row["scenario_ids"]),
                cohort_key=str(row["cohort_key"]),
                forecast_class=str(row["forecast_class"]),
                external_probability_source=str(row["external_probability_source"]),
                status=str(row.get("status", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationCohortRegistryError(
                f"invalid cohort row at index {index}"
            ) from exc
        cohorts.append(cohort)
    registry = ProductionCalibrationRegistry(
        version=int(payload.get("version", 0)), cohorts=tuple(cohorts)
    )
    registry.validate()
    return registry


def resolve_production_calibration_cohort(
    registry: ProductionCalibrationRegistry,
    *,
    ksic_code: str | None,
    forecast_years: int,
    scenario_ids: Iterable[str],
) -> ProductionCalibrationCohort | None:
    if not ksic_code:
        return None
    code = str(ksic_code).strip()
    if not code.isdigit():
        raise CalibrationCohortRegistryError("KSIC code must be numeric when supplied")
    scenarios = tuple(str(item) for item in scenario_ids)
    candidates = tuple(
        cohort
        for cohort in registry.cohorts
        if code.startswith(cohort.ksic_prefix)
        and cohort.forecast_years == int(forecast_years)
        and cohort.scenario_ids == scenarios
    )
    if not candidates:
        return None
    longest = max(len(item.ksic_prefix) for item in candidates)
    finalists = tuple(item for item in candidates if len(item.ksic_prefix) == longest)
    if len(finalists) != 1:
        names = ", ".join(sorted(item.registry_id for item in finalists))
        raise CalibrationCohortRegistryError(
            f"ambiguous production calibration cohort for KSIC {code}: {names}"
        )
    return finalists[0]


def validate_declared_calibration(
    cohort: ProductionCalibrationCohort,
    calibration: object,
) -> None:
    if not isinstance(calibration, dict):
        raise CalibrationCohortRegistryError(
            "CALIBRATION_REQUIRED: production cohort "
            f"{cohort.registry_id} ({cohort.cohort_key}) is registered; "
            "run.yaml must bind calibration"
        )
    required = {
        "cohort_key": cohort.cohort_key,
        "forecast_class": cohort.forecast_class,
        "external_probability_source": cohort.external_probability_source,
    }
    mismatches = tuple(
        f"{key}={calibration.get(key)!r} expected {expected!r}"
        for key, expected in required.items()
        if calibration.get(key) != expected
    )
    if mismatches:
        raise CalibrationCohortRegistryError(
            "CALIBRATION_COHORT_MISMATCH: production cohort "
            f"{cohort.registry_id} requires " + "; ".join(mismatches)
        )
