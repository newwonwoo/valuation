from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .continuous_financial_path_probability import (
    ContinuousDriverDependence,
    ContinuousDriverPosterior,
    ScenarioFinancialPath,
    simulate_continuous_financial_paths,
)
from .continuous_probability_snapshot import (
    ContinuousOOSDriverDiagnostic,
    ContinuousProbabilityCalibrationSnapshot,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_PATH = (
    _REPO_ROOT / "config" / "skhynix_continuous_calibration_artifact.json"
)
EXPECTED_ARTIFACT_SHA256 = (
    "e64a1b587908bd269c436688666c0817f6a1cac8742c620b3fcaa3575dc66006"
)
EXPECTED_DATASET_SHA256 = (
    "7d80665881a571c862722a6f0b7eada6184a5a3b6ca8131eea7954865b7e28e2"
)
EXPECTED_PROVENANCE_HASH = (
    "65e20cd455cd52416bcec5ef3e1475d2aa5f84f095c94a5d36201d9a9f006af0"
)
COHORT_KEY = "semiconductor.memory|9y_path_from_12m_transitions|continuous_v1"
FORECAST_CLASS = "semiconductor.memory.continuous_financial_path"
HORIZON = "9y_path_from_12m_transitions"
METHOD_VERSION = "probability_engine_v3.2_continuous_financial_path_v1"
MAPPING_VERSION = "skhynix_continuous_joint_centroid_v1"
DRIVER_IDS = (
    "revenue_growth",
    "operating_margin",
    "cash_conversion",
    "capex_intensity",
)
SCENARIOS = ("Down", "Core", "Bull")

_FORBIDDEN_ARTIFACT_KEYS = frozenset(
    {
        "market_price",
        "current_market_price",
        "target_price",
        "scenario_intrinsic_value",
        "intrinsic_value",
        "expected_value",
        "valuation_gap",
        "return_target",
        "entry_price",
        "demand_adverse",
        "margin_adverse",
        "cash_conversion_adverse",
        "capex_burden_adverse",
        "adverse_factor_count",
        "bull_joint_state",
    }
)


@dataclass(frozen=True)
class CurrentConditioning:
    revenue_growth: Decimal
    operating_margin: Decimal
    cash_conversion: Decimal
    capex_intensity: Decimal
    source_ref: str
    first_seen_at: str
    source_hash: str

    def as_map(self) -> dict[str, Decimal]:
        return {
            "revenue_growth": self.revenue_growth,
            "operating_margin": self.operating_margin,
            "cash_conversion": self.cash_conversion,
            "capex_intensity": self.capex_intensity,
        }

    def validate(self) -> None:
        if not self.source_ref.startswith("http"):
            raise ValueError(
                "current continuous conditioning requires an HTTP source"
            )
        if not self.first_seen_at or not self.source_hash:
            raise ValueError(
                "current continuous conditioning requires first-seen time and source hash"
            )
        if any(not value.is_finite() for value in self.as_map().values()):
            raise ValueError(
                "current continuous conditioning contains non-finite values"
            )
        if self.capex_intensity < 0:
            raise ValueError("current capex intensity cannot be negative")


def _stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _find_forbidden_keys(value: Any) -> tuple[str, ...]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_ARTIFACT_KEYS:
                found.add(normalized)
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return tuple(sorted(found))


def _decimal_path(values: Any, label: str) -> tuple[Decimal, ...]:
    if not isinstance(values, list) or len(values) != 9:
        raise ValueError(f"{label} must contain exactly nine annual values")
    result = tuple(Decimal(str(value)) for value in values)
    if any(not value.is_finite() for value in result):
        raise ValueError(f"{label} contains non-finite values")
    return result


def _load_artifact(
    path: str | Path = DEFAULT_ARTIFACT_PATH,
) -> tuple[dict[str, Any], str]:
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("continuous calibration artifact must be a mapping")
    declared_hash = str(payload.get("artifact_sha256") or "")
    hash_payload = dict(payload)
    hash_payload.pop("artifact_sha256", None)
    artifact_hash = _stable_hash(hash_payload)
    if declared_hash != artifact_hash or artifact_hash != EXPECTED_ARTIFACT_SHA256:
        raise ValueError("continuous calibration artifact hash mismatch")
    if payload.get("version") != "1.0":
        raise ValueError("continuous calibration artifact version drift")
    if payload.get("source_dataset_sha256") != EXPECTED_DATASET_SHA256:
        raise ValueError("continuous calibration source dataset hash mismatch")
    if payload.get("provenance_hash") != EXPECTED_PROVENANCE_HASH:
        raise ValueError("continuous calibration source provenance hash mismatch")
    if int(payload.get("source_row_count") or 0) != 418:
        raise ValueError("continuous calibration row count must remain 418")
    if int(payload.get("source_company_count") or 0) != 29:
        raise ValueError("continuous calibration company breadth must remain 29")
    if str(payload.get("target_ticker_excluded") or "") != "000660":
        raise ValueError("continuous calibration must exclude SK hynix target rows")
    forbidden = _find_forbidden_keys(payload)
    if forbidden:
        raise ValueError(
            "continuous calibration artifact contains forbidden value/binary-event fields: "
            + ", ".join(forbidden)
        )
    if payload.get("oos_split_order") != [
        "TRAIN",
        "VALIDATION",
        "HOLDOUT",
        "FINAL_OOS",
    ]:
        raise ValueError("continuous calibration OOS chronology drift")
    return payload, artifact_hash


def _validate_conditioning(
    payload: dict[str, Any], current: CurrentConditioning
) -> None:
    current.validate()
    row = payload.get("current_conditioning")
    if not isinstance(row, dict):
        raise ValueError("continuous artifact current conditioning is missing")
    expected = current.as_map()
    for driver_id, value in expected.items():
        artifact_value = Decimal(str(row.get(driver_id)))
        if artifact_value != value:
            raise ValueError(
                f"continuous artifact conditioning drift for {driver_id}"
            )
    if str(row.get("source_hash") or "") != current.source_hash:
        raise ValueError("continuous artifact conditioning source hash mismatch")
    if str(row.get("first_seen_at") or "") != current.first_seen_at:
        raise ValueError("continuous artifact conditioning first-seen mismatch")


def _driver_objects(
    payload: dict[str, Any], artifact_hash: str
) -> tuple[
    tuple[ContinuousDriverPosterior, ...],
    tuple[ContinuousOOSDriverDiagnostic, ...],
]:
    rows = payload.get("drivers")
    if not isinstance(rows, dict) or set(rows) != set(DRIVER_IDS):
        raise ValueError("continuous artifact driver coverage mismatch")
    drivers: list[ContinuousDriverPosterior] = []
    diagnostics: list[ContinuousOOSDriverDiagnostic] = []
    for driver_id in DRIVER_IDS:
        row = rows[driver_id]
        if not isinstance(row, dict):
            raise ValueError(f"continuous driver {driver_id} is malformed")
        path = row.get("path")
        diagnostic = row.get("diagnostic")
        posterior = row.get("posterior")
        if not all(isinstance(item, dict) for item in (path, diagnostic, posterior)):
            raise ValueError(f"continuous driver {driver_id} is incomplete")
        if float(posterior.get("mean_strength") or 0) <= 0:
            raise ValueError(f"continuous driver {driver_id} lacks hierarchical posterior strength")
        source_hash = _stable_hash(
            {
                "artifact_hash": artifact_hash,
                "driver_id": driver_id,
                "driver": row,
            }
        )
        drivers.append(
            ContinuousDriverPosterior(
                driver_id=driver_id,
                mean_path=_decimal_path(path.get("mean"), f"{driver_id} mean"),
                scale_path=_decimal_path(path.get("scale"), f"{driver_id} scale"),
                mean_uncertainty_path=_decimal_path(
                    path.get("mean_uncertainty"),
                    f"{driver_id} mean uncertainty",
                ),
                source_hash=source_hash,
                lower_bound=Decimal(str(path.get("lower_bound"))),
                upper_bound=Decimal(str(path.get("upper_bound"))),
            )
        )
        skill_values = diagnostic.get("skill_windows")
        if not isinstance(skill_values, list) or len(skill_values) != 3:
            raise ValueError(
                f"continuous driver {driver_id} requires three chronological OOS windows"
            )
        diagnostics.append(
            ContinuousOOSDriverDiagnostic(
                driver_id=driver_id,
                skill_windows=tuple(Decimal(str(value)) for value in skill_values),
                likelihood_weight=Decimal(
                    str(diagnostic.get("likelihood_weight"))
                ),
                uncertainty_inflation=Decimal(
                    str(diagnostic.get("uncertainty_inflation"))
                ),
                resolved_cases=int(diagnostic.get("resolved_cases") or 0),
                company_count=int(diagnostic.get("company_count") or 0),
                quarter_count=int(diagnostic.get("quarter_count") or 0),
                regime_similarity=Decimal(
                    str(diagnostic.get("regime_similarity"))
                ),
            )
        )
    for driver in drivers:
        driver.validate()
    for diagnostic in diagnostics:
        diagnostic.validate()
    return tuple(drivers), tuple(diagnostics)


def _scenario_objects(payload: dict[str, Any]) -> tuple[ScenarioFinancialPath, ...]:
    rows = payload.get("scenarios")
    if not isinstance(rows, dict) or set(rows) != set(SCENARIOS):
        raise ValueError("continuous artifact scenario coverage mismatch")
    result: list[ScenarioFinancialPath] = []
    for scenario_id in SCENARIOS:
        row = rows[scenario_id]
        paths = row.get("driver_paths") if isinstance(row, dict) else None
        weights = row.get("driver_weights") if isinstance(row, dict) else None
        if not isinstance(paths, dict) or set(paths) != set(DRIVER_IDS):
            raise ValueError(
                f"continuous scenario {scenario_id} driver coverage mismatch"
            )
        if not isinstance(weights, dict) or set(weights) != set(DRIVER_IDS):
            raise ValueError(
                f"continuous scenario {scenario_id} weights are incomplete"
            )
        result.append(
            ScenarioFinancialPath(
                scenario_id=scenario_id,
                driver_paths=tuple(
                    (
                        driver_id,
                        _decimal_path(
                            paths[driver_id],
                            f"{scenario_id}/{driver_id}",
                        ),
                    )
                    for driver_id in DRIVER_IDS
                ),
                driver_weights=tuple(
                    (driver_id, Decimal(str(weights[driver_id])))
                    for driver_id in DRIVER_IDS
                ),
            )
        )
    for scenario in result:
        scenario.validate()
    return tuple(result)


def _dependence_object(
    payload: dict[str, Any]
) -> tuple[ContinuousDriverDependence, str]:
    row = payload.get("dependence")
    if not isinstance(row, dict):
        raise ValueError("continuous dependence artifact is missing")
    matrix = row.get("correlation_matrix")
    if not isinstance(matrix, list) or len(matrix) != len(DRIVER_IDS):
        raise ValueError("continuous dependence matrix is malformed")
    correlation = tuple(
        tuple(Decimal(str(value)) for value in values)
        for values in matrix
    )
    dependence_hash = _stable_hash(row)
    dependence = ContinuousDriverDependence(
        version=f"{row.get('version')}:{dependence_hash[:12]}",
        driver_ids=DRIVER_IDS,
        correlation_matrix=correlation,
        student_t_df=int(row.get("student_t_df") or 0),
    )
    dependence.validate()
    return dependence, dependence_hash


def build_skhynix_continuous_probability_snapshot(
    *,
    current: CurrentConditioning,
    as_of_date: str,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
) -> ContinuousProbabilityCalibrationSnapshot:
    """Run v3.2 continuous financial-path Monte Carlo from a frozen calibration artifact.

    The artifact was calibrated from 418 target-excluded 12-month financial
    transitions. It contains hierarchical Bayesian partially pooled posterior paths,
    chronological OOS skill weights, and a versioned cross-driver residual
    correlation matrix. No current price, Street target, intrinsic value, return
    target, entry price, or legacy binary risk-event state is accepted.
    """
    payload, artifact_hash = _load_artifact(artifact_path)
    _validate_conditioning(payload, current)
    drivers, diagnostics = _driver_objects(payload, artifact_hash)
    scenarios = _scenario_objects(payload)
    dependence, dependence_hash = _dependence_object(payload)
    simulation = simulate_continuous_financial_paths(
        drivers=drivers,
        scenarios=scenarios,
        dependence=dependence,
        credible_level=Decimal("0.90"),
        outer_draws=300,
        inner_draws=200,
        seed=20260829,
    )
    return ContinuousProbabilityCalibrationSnapshot.build(
        cohort_key=COHORT_KEY,
        forecast_class=FORECAST_CLASS,
        horizon=HORIZON,
        as_of_date=as_of_date,
        method_version=METHOD_VERSION,
        mapping_version=MAPPING_VERSION,
        estimates=simulation.estimates,
        driver_snapshot_hashes=tuple(
            (driver.driver_id, driver.source_hash) for driver in drivers
        ),
        dependence_hash=dependence_hash,
        simulation_hash=simulation.simulation_hash,
        dataset_hash=EXPECTED_DATASET_SHA256,
        oos_diagnostics=diagnostics,
        integrity_findings=(),
    )
