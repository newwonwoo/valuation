from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

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
):
    snapshot = load_skhynix_snapshot(snapshot_path)
    base = _build_base_config(
        state_root,
        run_id=run_id,
        snapshot_path=snapshot_path,
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
