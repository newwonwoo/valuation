"""Generic assembler for continuous financial-path probability snapshots.

This module holds everything about the v3.2 continuous route that is true of
*any* company: how a frozen calibration artifact is loaded and hash-checked, how
knowledge-time provenance is enforced, how driver posteriors, scenario paths and
the residual-dependence matrix are rebuilt, and how the Monte Carlo result is
sealed into a :class:`ContinuousProbabilityCalibrationSnapshot`.

Everything that is true of *one* company — which artifact file, which hashes it
must carry, which drivers and scenarios it models, which ticker it had to
exclude from training — is declared in a :class:`ContinuousCalibrationBinding`
and passed in. A company adapter is therefore a declaration, not a copy of the
assembly logic.

The artifact format itself is company-neutral: the same JSON shape describes any
cohort. Only the expected *values* differ, and those live on the binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

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


ARTIFACT_FORMAT_VERSION = "1.0"
PROBABILITY_SOURCE = "continuous_financial_path_monte_carlo"
REQUIRED_OOS_WINDOWS = 3
REQUIRED_OOS_SPLIT_ORDER = ("TRAIN", "VALIDATION", "HOLDOUT", "FINAL_OOS")

# Value, price and Street tokens may never appear anywhere inside a calibration
# artifact: a probability that was fitted against an outcome the valuation is
# supposed to produce is circular, whatever the company.
UNIVERSAL_FORBIDDEN_ARTIFACT_KEYS = frozenset(
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
    }
)


class ContinuousCalibrationError(ValueError):
    """Raised when a calibration artifact does not satisfy its binding."""


# --------------------------------------------------------------------- binding


@dataclass(frozen=True)
class ContinuousCalibrationBinding:
    """Everything company-specific about one continuous calibration.

    ``excluded_ticker`` is the ticker whose own rows were withheld from
    the training set. It is checked against the artifact and the provenance file
    so a calibration can never be silently re-pointed at a company it was fitted
    on.
    """

    cohort_key: str
    forecast_class: str
    horizon: str
    method_version: str
    mapping_version: str
    driver_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    path_length: int
    artifact_path: Path
    provenance_path: Path
    expected_artifact_sha256: str
    expected_provenance_artifact_sha256: str
    expected_dataset_sha256: str
    expected_provenance_hash: str
    expected_source_row_count: int
    expected_source_company_count: int
    excluded_ticker: str
    credible_level: Decimal = Decimal("0.90")
    outer_draws: int = 300
    inner_draws: int = 200
    seed: int = 0
    # Drivers whose economic definition forbids a negative reading (an intensity
    # or a ratio of spend to revenue), checked on the current conditioning.
    non_negative_driver_ids: tuple[str, ...] = ()
    # Cohort-specific keys that must not survive into the artifact — typically
    # the state names of a legacy binary-event mapping this calibration replaced.
    extra_forbidden_artifact_keys: frozenset[str] = frozenset()

    @property
    def forbidden_artifact_keys(self) -> frozenset[str]:
        return UNIVERSAL_FORBIDDEN_ARTIFACT_KEYS | frozenset(
            str(key).strip().lower() for key in self.extra_forbidden_artifact_keys
        )

    def validate(self) -> None:
        identity = (
            self.cohort_key,
            self.forecast_class,
            self.horizon,
            self.method_version,
            self.mapping_version,
            self.expected_artifact_sha256,
            self.expected_provenance_artifact_sha256,
            self.expected_dataset_sha256,
            self.expected_provenance_hash,
            self.excluded_ticker,
        )
        if not all(identity):
            raise ContinuousCalibrationError(
                "continuous calibration binding identity is incomplete"
            )
        if len(self.driver_ids) < 2 or len(set(self.driver_ids)) != len(self.driver_ids):
            raise ContinuousCalibrationError(
                "continuous calibration binding requires at least two distinct drivers"
            )
        if len(self.scenario_ids) < 2 or len(set(self.scenario_ids)) != len(
            self.scenario_ids
        ):
            raise ContinuousCalibrationError(
                "continuous calibration binding requires at least two distinct scenarios"
            )
        unknown = set(self.non_negative_driver_ids) - set(self.driver_ids)
        if unknown:
            raise ContinuousCalibrationError(
                "non-negative driver constraint names an unmodelled driver: "
                + ", ".join(sorted(unknown))
            )
        if self.path_length <= 0:
            raise ContinuousCalibrationError(
                "continuous calibration path length must be positive"
            )
        if self.expected_source_row_count <= 0 or self.expected_source_company_count <= 0:
            raise ContinuousCalibrationError(
                "continuous calibration breadth expectations must be positive"
            )
        if not Decimal("0") < self.credible_level < Decimal("1"):
            raise ContinuousCalibrationError(
                "continuous calibration credible level must lie within (0,1)"
            )
        if min(self.outer_draws, self.inner_draws) <= 0:
            raise ContinuousCalibrationError(
                "continuous calibration draw counts must be positive"
            )
        leaked = self.forbidden_artifact_keys.intersection(self.driver_ids)
        if leaked:
            raise ContinuousCalibrationError(
                "driver id collides with a forbidden artifact key: "
                + ", ".join(sorted(leaked))
            )


# ---------------------------------------------------------------- conditioning


@dataclass(frozen=True)
class ContinuousConditioning:
    """The company's current driver readings, with the source that carried them.

    ``first_seen_at`` is the moment the reading became publicly observable. It is
    checked against the requested snapshot cutoff, so replaying an older as-of
    date cannot silently condition on information that did not exist yet.
    """

    readings: tuple[tuple[str, Decimal], ...]
    source_ref: str
    first_seen_at: str
    source_hash: str

    def as_map(self) -> dict[str, Decimal]:
        return {driver_id: value for driver_id, value in self.readings}

    def validate(self, binding: ContinuousCalibrationBinding) -> None:
        if not self.source_ref.startswith("http"):
            raise ContinuousCalibrationError(
                "current continuous conditioning requires an HTTP source"
            )
        if not self.first_seen_at or not self.source_hash:
            raise ContinuousCalibrationError(
                "current continuous conditioning requires first-seen time and source hash"
            )
        parse_timestamp(self.first_seen_at, label="current conditioning first_seen_at")
        readings = self.as_map()
        if len(readings) != len(self.readings):
            raise ContinuousCalibrationError(
                "current continuous conditioning repeats a driver"
            )
        if set(readings) != set(binding.driver_ids):
            raise ContinuousCalibrationError(
                "current continuous conditioning driver coverage mismatch"
            )
        if any(not value.is_finite() for value in readings.values()):
            raise ContinuousCalibrationError(
                "current continuous conditioning contains non-finite values"
            )
        for driver_id in binding.non_negative_driver_ids:
            if readings[driver_id] < 0:
                raise ContinuousCalibrationError(
                    f"current {driver_id} cannot be negative"
                )


def conditioning_from_mapping(
    readings: Mapping[str, Any],
    *,
    binding: ContinuousCalibrationBinding,
    source_ref: str,
    first_seen_at: str,
    source_hash: str,
) -> ContinuousConditioning:
    """Build conditioning from a provider snapshot row, in binding driver order."""
    missing = [driver_id for driver_id in binding.driver_ids if driver_id not in readings]
    if missing:
        raise ContinuousCalibrationError(
            "conditioning row is missing drivers: " + ", ".join(missing)
        )
    return ContinuousConditioning(
        readings=tuple(
            (driver_id, Decimal(str(readings[driver_id])))
            for driver_id in binding.driver_ids
        ),
        source_ref=source_ref,
        first_seen_at=first_seen_at,
        source_hash=source_hash,
    )


# -------------------------------------------------------------------- plumbing


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def parse_timestamp(value: str, *, label: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContinuousCalibrationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContinuousCalibrationError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def snapshot_cutoff(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if len(text) == 10:
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise ContinuousCalibrationError(
                "continuous probability as_of_date must be ISO date/timestamp"
            ) from exc
        return datetime.combine(day, time.max, tzinfo=timezone.utc)
    return parse_timestamp(text, label="continuous probability as_of_date")


def _find_forbidden_keys(value: Any, forbidden: frozenset[str]) -> tuple[str, ...]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden:
                found.add(normalized)
            found.update(_find_forbidden_keys(item, forbidden))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_keys(item, forbidden))
    return tuple(sorted(found))


def _decimal_path(values: Any, label: str, *, length: int) -> tuple[Decimal, ...]:
    if not isinstance(values, list) or len(values) != length:
        raise ContinuousCalibrationError(
            f"{label} must contain exactly {length} annual values"
        )
    result = tuple(Decimal(str(value)) for value in values)
    if any(not value.is_finite() for value in result):
        raise ContinuousCalibrationError(f"{label} contains non-finite values")
    return result


# ------------------------------------------------------------------- artifacts


def load_artifact(
    binding: ContinuousCalibrationBinding,
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    raw = Path(path if path is not None else binding.artifact_path).read_text(
        encoding="utf-8"
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ContinuousCalibrationError(
            "continuous calibration artifact must be a mapping"
        )
    declared_hash = str(payload.get("artifact_sha256") or "")
    hash_payload = dict(payload)
    hash_payload.pop("artifact_sha256", None)
    artifact_hash = stable_hash(hash_payload)
    if (
        declared_hash != artifact_hash
        or artifact_hash != binding.expected_artifact_sha256
    ):
        raise ContinuousCalibrationError("continuous calibration artifact hash mismatch")
    if payload.get("version") != ARTIFACT_FORMAT_VERSION:
        raise ContinuousCalibrationError("continuous calibration artifact version drift")
    if payload.get("source_dataset_sha256") != binding.expected_dataset_sha256:
        raise ContinuousCalibrationError(
            "continuous calibration source dataset hash mismatch"
        )
    if payload.get("provenance_hash") != binding.expected_provenance_hash:
        raise ContinuousCalibrationError(
            "continuous calibration source provenance hash mismatch"
        )
    if int(payload.get("source_row_count") or 0) != binding.expected_source_row_count:
        raise ContinuousCalibrationError(
            "continuous calibration row count must remain "
            f"{binding.expected_source_row_count}"
        )
    if (
        int(payload.get("source_company_count") or 0)
        != binding.expected_source_company_count
    ):
        raise ContinuousCalibrationError(
            "continuous calibration company breadth must remain "
            f"{binding.expected_source_company_count}"
        )
    if str(payload.get("target_ticker_excluded") or "") != binding.excluded_ticker:
        raise ContinuousCalibrationError(
            "continuous calibration must exclude target rows for "
            f"{binding.excluded_ticker}"
        )
    forbidden = _find_forbidden_keys(payload, binding.forbidden_artifact_keys)
    if forbidden:
        raise ContinuousCalibrationError(
            "continuous calibration artifact contains forbidden value/binary-event fields: "
            + ", ".join(forbidden)
        )
    if tuple(payload.get("oos_split_order") or ()) != REQUIRED_OOS_SPLIT_ORDER:
        raise ContinuousCalibrationError("continuous calibration OOS chronology drift")
    return payload, artifact_hash


def load_provenance(
    binding: ContinuousCalibrationBinding,
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    raw = Path(path if path is not None else binding.provenance_path).read_text(
        encoding="utf-8"
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ContinuousCalibrationError(
            "continuous calibration provenance must be a mapping"
        )
    provenance_artifact_hash = stable_hash(payload)
    if provenance_artifact_hash != binding.expected_provenance_artifact_sha256:
        raise ContinuousCalibrationError(
            "continuous calibration provenance artifact hash mismatch"
        )
    if payload.get("version") != ARTIFACT_FORMAT_VERSION:
        raise ContinuousCalibrationError(
            "continuous calibration provenance version drift"
        )
    if payload.get("source_dataset_sha256") != binding.expected_dataset_sha256:
        raise ContinuousCalibrationError(
            "continuous calibration provenance dataset hash mismatch"
        )
    if payload.get("source_provenance_hash") != binding.expected_provenance_hash:
        raise ContinuousCalibrationError(
            "continuous calibration provenance lineage hash mismatch"
        )
    if str(payload.get("target_ticker_excluded") or "") != binding.excluded_ticker:
        raise ContinuousCalibrationError(
            "continuous calibration provenance lost target exclusion"
        )
    parse_timestamp(
        str(payload.get("training_latest_publication_at") or ""),
        label="training latest publication",
    )
    parse_timestamp(
        str(payload.get("current_conditioning_first_seen_at") or ""),
        label="frozen conditioning first_seen_at",
    )
    return payload, provenance_artifact_hash


def _validate_knowledge_cutoff(
    *,
    as_of_date: str,
    conditioning: ContinuousConditioning,
    provenance: dict[str, Any],
) -> None:
    cutoff = snapshot_cutoff(as_of_date)
    training_latest = parse_timestamp(
        str(provenance["training_latest_publication_at"]),
        label="training latest publication",
    )
    conditioning_first_seen = parse_timestamp(
        conditioning.first_seen_at,
        label="current conditioning first_seen_at",
    )
    if training_latest > cutoff:
        raise PermissionError(
            "continuous calibration contains training Evidence first published after the requested snapshot cutoff"
        )
    if conditioning_first_seen > cutoff:
        raise PermissionError(
            "current conditioning was first seen after the requested snapshot cutoff"
        )


def _validate_conditioning(
    payload: dict[str, Any],
    conditioning: ContinuousConditioning,
    provenance: dict[str, Any],
    binding: ContinuousCalibrationBinding,
) -> None:
    conditioning.validate(binding)
    row = payload.get("current_conditioning")
    if not isinstance(row, dict):
        raise ContinuousCalibrationError(
            "continuous artifact current conditioning is missing"
        )
    for driver_id, value in conditioning.as_map().items():
        artifact_value = Decimal(str(row.get(driver_id)))
        if artifact_value != value:
            raise ContinuousCalibrationError(
                f"continuous artifact conditioning drift for {driver_id}"
            )
    if str(row.get("source_hash") or "") != conditioning.source_hash:
        raise ContinuousCalibrationError(
            "continuous artifact conditioning source hash mismatch"
        )
    if str(row.get("first_seen_at") or "") != conditioning.first_seen_at:
        raise ContinuousCalibrationError(
            "continuous artifact conditioning first-seen mismatch"
        )
    if (
        str(provenance.get("current_conditioning_source_ref") or "")
        != conditioning.source_ref
    ):
        raise ContinuousCalibrationError(
            "continuous conditioning source URL differs from frozen provenance"
        )
    if (
        str(provenance.get("current_conditioning_source_hash") or "")
        != conditioning.source_hash
    ):
        raise ContinuousCalibrationError(
            "continuous conditioning source hash differs from frozen provenance"
        )
    if (
        str(provenance.get("current_conditioning_first_seen_at") or "")
        != conditioning.first_seen_at
    ):
        raise ContinuousCalibrationError(
            "continuous conditioning first-seen differs from frozen provenance"
        )


def _driver_objects(
    payload: dict[str, Any],
    artifact_hash: str,
    *,
    binding: ContinuousCalibrationBinding,
    conditioning: ContinuousConditioning,
    provenance_artifact_hash: str,
) -> tuple[
    tuple[ContinuousDriverPosterior, ...],
    tuple[ContinuousOOSDriverDiagnostic, ...],
]:
    rows = payload.get("drivers")
    if not isinstance(rows, dict) or set(rows) != set(binding.driver_ids):
        raise ContinuousCalibrationError("continuous artifact driver coverage mismatch")
    conditioning_provenance = {
        "source_ref": conditioning.source_ref,
        "first_seen_at": conditioning.first_seen_at,
        "source_hash": conditioning.source_hash,
        "provenance_artifact_hash": provenance_artifact_hash,
    }
    drivers: list[ContinuousDriverPosterior] = []
    diagnostics: list[ContinuousOOSDriverDiagnostic] = []
    for driver_id in binding.driver_ids:
        row = rows[driver_id]
        if not isinstance(row, dict):
            raise ContinuousCalibrationError(
                f"continuous driver {driver_id} is malformed"
            )
        path = row.get("path")
        diagnostic = row.get("diagnostic")
        posterior = row.get("posterior")
        if not all(isinstance(item, dict) for item in (path, diagnostic, posterior)):
            raise ContinuousCalibrationError(
                f"continuous driver {driver_id} is incomplete"
            )
        if float(posterior.get("mean_strength") or 0) <= 0:
            raise ContinuousCalibrationError(
                f"continuous driver {driver_id} lacks hierarchical posterior strength"
            )
        source_hash = stable_hash(
            {
                "artifact_hash": artifact_hash,
                "driver_id": driver_id,
                "driver": row,
                "conditioning_provenance": conditioning_provenance,
            }
        )
        drivers.append(
            ContinuousDriverPosterior(
                driver_id=driver_id,
                mean_path=_decimal_path(
                    path.get("mean"), f"{driver_id} mean", length=binding.path_length
                ),
                scale_path=_decimal_path(
                    path.get("scale"), f"{driver_id} scale", length=binding.path_length
                ),
                mean_uncertainty_path=_decimal_path(
                    path.get("mean_uncertainty"),
                    f"{driver_id} mean uncertainty",
                    length=binding.path_length,
                ),
                source_hash=source_hash,
                lower_bound=Decimal(str(path.get("lower_bound"))),
                upper_bound=Decimal(str(path.get("upper_bound"))),
            )
        )
        skill_values = diagnostic.get("skill_windows")
        if not isinstance(skill_values, list) or len(skill_values) != REQUIRED_OOS_WINDOWS:
            raise ContinuousCalibrationError(
                f"continuous driver {driver_id} requires "
                f"{REQUIRED_OOS_WINDOWS} chronological OOS windows"
            )
        diagnostics.append(
            ContinuousOOSDriverDiagnostic(
                driver_id=driver_id,
                skill_windows=tuple(Decimal(str(value)) for value in skill_values),
                likelihood_weight=Decimal(str(diagnostic.get("likelihood_weight"))),
                uncertainty_inflation=Decimal(
                    str(diagnostic.get("uncertainty_inflation"))
                ),
                resolved_cases=int(diagnostic.get("resolved_cases") or 0),
                company_count=int(diagnostic.get("company_count") or 0),
                quarter_count=int(diagnostic.get("quarter_count") or 0),
                regime_similarity=Decimal(str(diagnostic.get("regime_similarity"))),
            )
        )
    for driver in drivers:
        driver.validate()
    for diagnostic in diagnostics:
        diagnostic.validate()
    return tuple(drivers), tuple(diagnostics)


def _scenario_objects(
    payload: dict[str, Any], binding: ContinuousCalibrationBinding
) -> tuple[ScenarioFinancialPath, ...]:
    rows = payload.get("scenarios")
    if not isinstance(rows, dict) or set(rows) != set(binding.scenario_ids):
        raise ContinuousCalibrationError(
            "continuous artifact scenario coverage mismatch"
        )
    result: list[ScenarioFinancialPath] = []
    for scenario_id in binding.scenario_ids:
        row = rows[scenario_id]
        paths = row.get("driver_paths") if isinstance(row, dict) else None
        weights = row.get("driver_weights") if isinstance(row, dict) else None
        if not isinstance(paths, dict) or set(paths) != set(binding.driver_ids):
            raise ContinuousCalibrationError(
                f"continuous scenario {scenario_id} driver coverage mismatch"
            )
        if not isinstance(weights, dict) or set(weights) != set(binding.driver_ids):
            raise ContinuousCalibrationError(
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
                            length=binding.path_length,
                        ),
                    )
                    for driver_id in binding.driver_ids
                ),
                driver_weights=tuple(
                    (driver_id, Decimal(str(weights[driver_id])))
                    for driver_id in binding.driver_ids
                ),
            )
        )
    for scenario in result:
        scenario.validate()
    return tuple(result)


def _dependence_object(
    payload: dict[str, Any], binding: ContinuousCalibrationBinding
) -> tuple[ContinuousDriverDependence, str]:
    row = payload.get("dependence")
    if not isinstance(row, dict):
        raise ContinuousCalibrationError("continuous dependence artifact is missing")
    matrix = row.get("correlation_matrix")
    if not isinstance(matrix, list) or len(matrix) != len(binding.driver_ids):
        raise ContinuousCalibrationError("continuous dependence matrix is malformed")
    correlation = tuple(
        tuple(Decimal(str(value)) for value in values) for values in matrix
    )
    dependence_hash = stable_hash(row)
    dependence = ContinuousDriverDependence(
        version=f"{row.get('version')}:{dependence_hash[:12]}",
        driver_ids=binding.driver_ids,
        correlation_matrix=correlation,
        student_t_df=int(row.get("student_t_df") or 0),
    )
    dependence.validate()
    return dependence, dependence_hash


# ------------------------------------------------------------------- assembler


def build_continuous_probability_snapshot(
    *,
    binding: ContinuousCalibrationBinding,
    conditioning: ContinuousConditioning,
    as_of_date: str,
    artifact_path: str | Path | None = None,
    provenance_path: str | Path | None = None,
) -> ContinuousProbabilityCalibrationSnapshot:
    """Run the continuous financial-path Monte Carlo for one bound calibration.

    The calibration artifact holds hierarchical Bayesian partially pooled
    posterior driver paths fitted on target-excluded financial transitions,
    chronological OOS skill weights, and a versioned cross-driver residual
    correlation matrix. Knowledge-time provenance is separately hash-frozen and
    every requested snapshot must be at or after both the training and the
    conditioning first-seen timestamps. No current price, Street target,
    intrinsic value, return target, entry price, or legacy binary risk-event
    state is accepted from the artifact.
    """
    binding.validate()
    payload, artifact_hash = load_artifact(binding, artifact_path)
    provenance, provenance_artifact_hash = load_provenance(binding, provenance_path)
    _validate_knowledge_cutoff(
        as_of_date=as_of_date,
        conditioning=conditioning,
        provenance=provenance,
    )
    _validate_conditioning(payload, conditioning, provenance, binding)
    drivers, diagnostics = _driver_objects(
        payload,
        artifact_hash,
        binding=binding,
        conditioning=conditioning,
        provenance_artifact_hash=provenance_artifact_hash,
    )
    scenarios = _scenario_objects(payload, binding)
    dependence, dependence_hash = _dependence_object(payload, binding)
    simulation = simulate_continuous_financial_paths(
        drivers=drivers,
        scenarios=scenarios,
        dependence=dependence,
        credible_level=binding.credible_level,
        outer_draws=binding.outer_draws,
        inner_draws=binding.inner_draws,
        seed=binding.seed,
    )
    return ContinuousProbabilityCalibrationSnapshot.build(
        cohort_key=binding.cohort_key,
        forecast_class=binding.forecast_class,
        horizon=binding.horizon,
        as_of_date=as_of_date,
        method_version=binding.method_version,
        mapping_version=binding.mapping_version,
        estimates=simulation.estimates,
        driver_snapshot_hashes=tuple(
            (driver.driver_id, driver.source_hash) for driver in drivers
        ),
        dependence_hash=dependence_hash,
        simulation_hash=simulation.simulation_hash,
        dataset_hash=binding.expected_dataset_sha256,
        oos_diagnostics=diagnostics,
        integrity_findings=(),
    )
