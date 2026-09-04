from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from .continuous_probability_snapshot import ContinuousProbabilityCalibrationSnapshot
from .skhynix_continuous_probability import (
    COHORT_KEY,
    CurrentConditioning,
    build_skhynix_continuous_probability_snapshot,
)
from .skhynix_live_primary import (
    SCENARIOS,
    build_skhynix_live_primary_config as _build_base_config,
    load_skhynix_snapshot,
)


EXTERNAL_PROBABILITY_SOURCE = "continuous_financial_path_monte_carlo"
_SCENARIO_LABELS = {
    "Down": "하방",
    "Core": "기준",
    "Bull": "상방",
}


def render_calibrated_probability_summary(
    report: str,
    probability_snapshot: ContinuousProbabilityCalibrationSnapshot,
    probability_distribution_status: object,
) -> str:
    """Render only the already-frozen canonical probability snapshot into the report artifact."""
    if str(probability_distribution_status) != "CALIBRATED":
        return report

    estimates = tuple(probability_snapshot.estimates)
    by_id = {item.scenario_id: item for item in estimates}
    if set(by_id) != set(_SCENARIO_LABELS):
        raise RuntimeError("calibrated probability snapshot must cover Down/Core/Bull")
    total = sum((Decimal(str(item.probability)) for item in estimates), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("1e-12"):
        raise RuntimeError("calibrated probability snapshot must sum to one")

    probability_summary = " · ".join(
        f"{_SCENARIO_LABELS[scenario_id]} "
        f"{Decimal(str(by_id[scenario_id].probability)) * 100:.1f}%"
        for scenario_id in ("Down", "Core", "Bull")
    )
    replacement = (
        f"| **시나리오 가능성** | {probability_summary} "
        "(보정 완료·수치 가중 적용) |"
    )
    probability_rows = tuple(
        line for line in report.splitlines() if line.startswith("| **시나리오 가능성** |")
    )
    if len(probability_rows) != 1:
        raise RuntimeError("canonical report must contain exactly one scenario probability row")
    existing = probability_rows[0]
    if existing == replacement:
        return report
    if "미산출" not in existing:
        raise RuntimeError("refusing to overwrite an unexpected scenario probability rendering")
    return report.replace(existing, replacement, 1)


def _continuous_calibration_loader(snapshot):
    conditioning = snapshot.payload.get("probability_conditioning")
    if not isinstance(conditioning, dict):
        raise ValueError("SK hynix probability_conditioning snapshot is required")
    current = CurrentConditioning(
        revenue_growth=Decimal(str(conditioning["revenue_growth"])),
        operating_margin=Decimal(str(conditioning["operating_margin"])),
        cash_conversion=Decimal(str(conditioning["cash_conversion"])),
        capex_intensity=Decimal(str(conditioning["capex_intensity"])),
        source_ref=snapshot.sources["probability_numeric_snapshot"],
        first_seen_at=str(conditioning["first_seen_at"]),
        source_hash=str(conditioning["source_hash"]),
    )

    def load(_context):
        return build_skhynix_continuous_probability_snapshot(
            current=current,
            as_of_date=snapshot.as_of,
        )

    return load


def build_skhynix_live_primary_config(
    state_root: str | Path,
    *,
    run_id: str = "SKHYNIX-000660-20260829-CONTINUOUS-PROBABILITY",
    snapshot_path: str | Path | None = None,
    post_freeze_snapshot_path: str | Path | None = None,
):
    snapshot = load_skhynix_snapshot(snapshot_path)
    base = _build_base_config(
        state_root,
        run_id=run_id,
        snapshot_path=snapshot_path,
        post_freeze_snapshot_path=post_freeze_snapshot_path,
    )
    providers = replace(
        base.providers,
        calibration_loader=_continuous_calibration_loader(snapshot),
    )
    binding_spec = replace(
        base.scenario_binding_spec,
        scenario_ids=SCENARIOS,
        calibration_cohort_key=COHORT_KEY,
        external_probability_source=EXTERNAL_PROBABILITY_SOURCE,
    )
    initial_data = dict(base.initial_data)
    initial_data.update(
        {
            "underwriting_status": "SOURCE_BACKED_CONTINUOUS_PROBABILITY_CALIBRATION",
            "probability_authority": "CONTINUOUS_FINANCIAL_PATH_SNAPSHOT_REQUIRED",
            "probability_method_version": "v3.2_continuous_financial_path",
            "legacy_boolean_probability_mapping": "FORBIDDEN",
        }
    )
    return replace(
        base,
        scenario_binding_spec=binding_spec,
        providers=providers,
        initial_data=initial_data,
    )


def run_skhynix_live_primary(
    state_root: str | Path,
    *,
    run_id: str = "SKHYNIX-000660-20260829-CONTINUOUS-PROBABILITY",
    snapshot_path: str | Path | None = None,
):
    from .strict_live_runtime import run_prism

    return run_prism(
        build_skhynix_live_primary_config(
            state_root,
            run_id=run_id,
            snapshot_path=snapshot_path,
        )
    )
