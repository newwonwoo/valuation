from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import yaml

from .probability_calibration import (
    CalibrationPolicy,
    CalibrationSnapshot,
    ProbabilityCalibrationLedger,
    build_calibration_snapshot,
)
from .records import CalibrationStatus


@dataclass(frozen=True)
class CalibrationCohortDeclaration:
    forecast_class: str
    horizon: str
    base_rate: Decimal
    mapping_version: str
    dataset_version: str
    source_ref: str

    @property
    def cohort_key(self) -> str:
        return f"{self.forecast_class}|{self.horizon}"

    def validate(self) -> None:
        if not all(
            (
                self.forecast_class,
                self.horizon,
                self.mapping_version,
                self.dataset_version,
                self.source_ref,
            )
        ):
            raise ValueError("calibration cohort declaration is incomplete")
        if not self.base_rate.is_finite() or not Decimal("0") < self.base_rate < Decimal("1"):
            raise ValueError("calibration cohort base_rate must be within (0,1)")


@dataclass(frozen=True)
class DeclaredCalibrationDataset:
    declaration: CalibrationCohortDeclaration
    ledger: ProbabilityCalibrationLedger
    dataset_hash: str

    def build_snapshot(
        self,
        *,
        cutoff: datetime,
        policy_path: str | Path,
        prior_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED,
    ) -> CalibrationSnapshot:
        policy = _policy_for_cohort(
            policy_path,
            cohort_key=self.declaration.cohort_key,
            base_rate=self.declaration.base_rate,
        )
        oos_brier_skill_windows = _derive_oos_brier_skill_windows(
            self.ledger,
            forecast_class=self.declaration.forecast_class,
            horizon=self.declaration.horizon,
            cutoff=cutoff,
            base_rate=self.declaration.base_rate,
            required_windows=policy.min_oos_windows,
        )
        return build_calibration_snapshot(
            self.ledger,
            forecast_class=self.declaration.forecast_class,
            horizon=self.declaration.horizon,
            cutoff=cutoff,
            policy=policy,
            mapping_version=self.declaration.mapping_version,
            oos_brier_skill_windows=oos_brier_skill_windows,
            prior_status=prior_status,
            dataset_hash=self.dataset_hash,
        )


def load_declared_calibration_dataset(
    payload: dict[str, Any],
    *,
    declaration: CalibrationCohortDeclaration,
    replay_cutoff: datetime | None = None,
) -> DeclaredCalibrationDataset:
    declaration.validate()
    if not isinstance(payload, dict):
        raise ValueError("calibration dataset payload must be a mapping")
    ledger_payload = payload.get("ledger")
    if not isinstance(ledger_payload, dict):
        raise ValueError("calibration dataset requires a ledger mapping")

    declared_key = str(payload.get("cohort_key") or "")
    declared_mapping = str(payload.get("mapping_version") or "")
    declared_version = str(payload.get("dataset_version") or "")
    declared_source = str(payload.get("source_ref") or "")
    if declared_key != declaration.cohort_key:
        raise ValueError("calibration dataset cohort_key does not match declaration")
    if declared_mapping != declaration.mapping_version:
        raise ValueError("calibration dataset mapping_version does not match declaration")
    if declared_version != declaration.dataset_version:
        raise ValueError("calibration dataset version does not match declaration")
    if declared_source != declaration.source_ref:
        raise ValueError("calibration dataset source_ref does not match declaration")

    forecast_rows = ledger_payload.get("forecasts")
    outcome_rows = ledger_payload.get("outcomes")
    if not isinstance(forecast_rows, list) or not isinstance(outcome_rows, list):
        raise ValueError("calibration ledger requires forecast and outcome lists")
    forecast_ids = {
        str(row.get("forecast_id") or "")
        for row in forecast_rows
        if isinstance(row, dict)
    }
    orphan_ids = sorted(
        {
            str(row.get("forecast_id") or "")
            for row in outcome_rows
            if isinstance(row, dict)
            and str(row.get("forecast_id") or "") not in forecast_ids
        }
    )
    if orphan_ids:
        raise ValueError(
            f"calibration dataset contains orphan outcomes: {', '.join(orphan_ids)}"
        )

    full_ledger = ProbabilityCalibrationLedger.from_payload(ledger_payload)
    if not full_ledger.forecasts:
        raise ValueError("calibration dataset cannot be empty")
    for forecast in full_ledger.forecasts:
        if forecast.cohort_key != declaration.cohort_key:
            raise ValueError(
                f"forecast {forecast.forecast_id} belongs to unexpected cohort {forecast.cohort_key}"
            )
        if forecast.first_seen_at is None:
            raise ValueError(
                f"forecast {forecast.forecast_id} requires explicit first_seen_at in production calibration data"
            )
    for outcome in full_ledger.outcomes:
        if outcome.first_seen_at is None:
            raise ValueError(
                f"outcome {outcome.forecast_id} requires explicit first_seen_at in production calibration data"
            )

    canonical_ledger_payload = full_ledger.to_payload()
    digest_payload = {
        "contract": "declared_calibration_dataset/v1",
        "cohort_key": declaration.cohort_key,
        "base_rate": str(declaration.base_rate),
        "mapping_version": declaration.mapping_version,
        "dataset_version": declaration.dataset_version,
        "source_ref": declaration.source_ref,
        "ledger": canonical_ledger_payload,
    }
    dataset_hash = sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    ledger = (
        full_ledger
        if replay_cutoff is None
        else ProbabilityCalibrationLedger.from_payload(
            canonical_ledger_payload,
            replay_cutoff=replay_cutoff,
        )
    )
    return DeclaredCalibrationDataset(declaration, ledger, dataset_hash)


def _policy_for_cohort(
    path: str | Path,
    *,
    cohort_key: str,
    base_rate: Decimal,
) -> CalibrationPolicy:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("probability calibration policy root must be a mapping")
    version = str(payload.get("version") or "")
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError("probability calibration policy requires defaults")
    cohorts = payload.get("cohorts") or {}
    if not isinstance(cohorts, dict):
        raise ValueError("probability calibration policy cohorts must be a mapping")
    cohort = cohorts.get(cohort_key) or {}
    if not isinstance(cohort, dict):
        raise ValueError(f"calibration policy cohort {cohort_key} must be a mapping")
    merged = {**defaults, **cohort}
    policy = CalibrationPolicy(
        version=version,
        base_rate=base_rate,
        min_resolved_events=int(merged.get("min_resolved_events", 200)),
        min_companies=int(merged.get("min_companies", 20)),
        min_quarters=int(merged.get("min_quarters", 8)),
        min_per_displayed_band=int(merged.get("min_per_displayed_band", 30)),
        min_oos_windows=int(merged.get("min_oos_windows", 2)),
        max_ece=Decimal(str(merged.get("max_ece", "0.08"))),
        max_ambiguous_censored_rate=Decimal(
            str(merged.get("max_ambiguous_censored_rate", "0.10"))
        ),
        fixed_bin_edges=tuple(
            Decimal(str(value))
            for value in merged.get(
                "fixed_bin_edges",
                (0, 0.2, 0.4, 0.6, 0.8, 1),
            )
        ),
    )
    policy.validate()
    return policy


def _derive_oos_brier_skill_windows(
    ledger: ProbabilityCalibrationLedger,
    *,
    forecast_class: str,
    horizon: str,
    cutoff: datetime,
    base_rate: Decimal,
    required_windows: int,
) -> tuple[Decimal, ...]:
    """Derive chronological issuance-quarter scores from hash-bound history."""

    windows: dict[str, list[tuple[Decimal, Decimal] | None]] = {}
    for forecast in ledger.terminal_forecasts(
        forecast_class=forecast_class,
        horizon=horizon,
        cutoff=cutoff,
    ):
        if forecast.evaluation_deadline > cutoff.date():
            continue
        quarter = f"{forecast.issued_at.year}Q{((forecast.issued_at.month - 1) // 3) + 1}"
        outcome = ledger.outcome_for(forecast.forecast_id, cutoff=cutoff)
        if (
            outcome is None
            or outcome.observed_at < forecast.issued_at
            or outcome.outcome.value not in {"occurred", "not_occurred"}
        ):
            windows.setdefault(quarter, []).append(None)
            continue
        observed = Decimal("1") if outcome.outcome.value == "occurred" else Decimal("0")
        windows.setdefault(quarter, []).append((forecast.probability, observed))

    result: list[Decimal] = []
    for quarter in sorted(windows):
        window = windows[quarter]
        if any(sample is None for sample in window):
            result.append(Decimal("0"))
            continue
        samples = [sample for sample in window if sample is not None]
        brier = sum(
            ((probability - observed) ** 2 for probability, observed in samples),
            Decimal("0"),
        ) / Decimal(len(samples))
        base_brier = sum(
            ((base_rate - observed) ** 2 for _, observed in samples),
            Decimal("0"),
        ) / Decimal(len(samples))
        result.append(
            Decimal("0")
            if base_brier == 0
            else Decimal("1") - (brier / base_brier)
        )
    return tuple(result[-required_windows:])
