from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

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


def _conditioning_source_matches_target(
    source_ref: str,
    *,
    target_corp_code: str,
    target_filing_receipts: Iterable[str],
) -> bool:
    try:
        parsed = urlparse(source_ref)
    except ValueError:
        return False
    query = parse_qs(parsed.query)
    corp_codes = tuple(query.get("corp_code") or ())
    if corp_codes:
        return len(corp_codes) == 1 and corp_codes[0] == target_corp_code
    receipts = tuple(query.get("rcpNo") or query.get("rcept_no") or ())
    if receipts:
        known = {str(item) for item in target_filing_receipts if str(item)}
        return len(receipts) == 1 and receipts[0] in known
    return False


def validate_declared_calibration(
    cohort: ProductionCalibrationCohort,
    calibration: object,
    *,
    target_ticker: str | None = None,
    target_corp_code: str | None = None,
    conditioning_source_ref: str | None = None,
    target_filing_receipts: Iterable[str] = (),
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

    if target_ticker is None and target_corp_code is None:
        return
    ticker = str(target_ticker or "").strip()
    corp_code = str(target_corp_code or "").strip()
    if not ticker or not corp_code:
        raise CalibrationCohortRegistryError(
            "CALIBRATION_TARGET_MISMATCH: registered production calibration "
            "requires the resolved ticker and OpenDART corp code"
        )
    constants = calibration.get("constants")
    excluded = (
        str(constants.get("excluded_ticker") or "").strip()
        if isinstance(constants, dict)
        else ""
    )
    if excluded != ticker:
        raise CalibrationCohortRegistryError(
            "CALIBRATION_TARGET_MISMATCH: production cohort "
            f"{cohort.registry_id} excludes ticker {excluded!r}, not the "
            f"resolved target {ticker!r}"
        )
    source_ref = str(conditioning_source_ref or "").strip()
    if not _conditioning_source_matches_target(
        source_ref,
        target_corp_code=corp_code,
        target_filing_receipts=target_filing_receipts,
    ):
        raise CalibrationCohortRegistryError(
            "CALIBRATION_TARGET_MISMATCH: conditioning source is not bound to "
            f"resolved target {ticker}/{corp_code}: {source_ref!r}"
        )
