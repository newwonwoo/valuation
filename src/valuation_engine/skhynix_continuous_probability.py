"""SK hynix binding for the generic continuous financial-path probability route.

This module declares *which* calibration SK hynix runs on. The assembly, hashing
and knowledge-time enforcement live in :mod:`continuous_probability_assembly`
and are shared by every company; nothing here computes a probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .continuous_probability_assembly import (
    ContinuousCalibrationBinding,
    ContinuousConditioning,
    build_continuous_probability_snapshot,
    conditioning_from_mapping,
)
from .continuous_probability_snapshot import ContinuousProbabilityCalibrationSnapshot


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_PATH = (
    _REPO_ROOT / "config" / "skhynix_continuous_calibration_artifact.json"
)
DEFAULT_PROVENANCE_PATH = (
    _REPO_ROOT / "config" / "skhynix_continuous_calibration_provenance.json"
)
EXPECTED_ARTIFACT_SHA256 = (
    "e64a1b587908bd269c436688666c0817f6a1cac8742c620b3fcaa3575dc66006"
)
EXPECTED_PROVENANCE_ARTIFACT_SHA256 = (
    "97c142dcad407cbdbb6c7b31f8c90a420b5684e56efce8079a164671acfda8d0"
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
TARGET_TICKER = "000660"

# State names of the binary risk-event mapping this calibration replaced. They
# must not reappear anywhere in the artifact, so a regression to the boolean
# route cannot pass unnoticed.
_LEGACY_BINARY_EVENT_KEYS = frozenset(
    {
        "demand_adverse",
        "margin_adverse",
        "cash_conversion_adverse",
        "capex_burden_adverse",
        "adverse_factor_count",
        "bull_joint_state",
    }
)

SKHYNIX_CONTINUOUS_BINDING = ContinuousCalibrationBinding(
    cohort_key=COHORT_KEY,
    forecast_class=FORECAST_CLASS,
    horizon=HORIZON,
    method_version=METHOD_VERSION,
    mapping_version=MAPPING_VERSION,
    driver_ids=DRIVER_IDS,
    scenario_ids=SCENARIOS,
    path_length=9,
    artifact_path=DEFAULT_ARTIFACT_PATH,
    provenance_path=DEFAULT_PROVENANCE_PATH,
    expected_artifact_sha256=EXPECTED_ARTIFACT_SHA256,
    expected_provenance_artifact_sha256=EXPECTED_PROVENANCE_ARTIFACT_SHA256,
    expected_dataset_sha256=EXPECTED_DATASET_SHA256,
    expected_provenance_hash=EXPECTED_PROVENANCE_HASH,
    expected_source_row_count=418,
    expected_source_company_count=29,
    excluded_target_ticker=TARGET_TICKER,
    credible_level=Decimal("0.90"),
    outer_draws=300,
    inner_draws=200,
    seed=20260829,
    non_negative_driver_ids=("capex_intensity",),
    extra_forbidden_artifact_keys=_LEGACY_BINARY_EVENT_KEYS,
)


@dataclass(frozen=True)
class CurrentConditioning:
    """SK hynix's four current driver readings, named for readability at call sites."""

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

    def as_conditioning(self) -> ContinuousConditioning:
        return conditioning_from_mapping(
            self.as_map(),
            binding=SKHYNIX_CONTINUOUS_BINDING,
            source_ref=self.source_ref,
            first_seen_at=self.first_seen_at,
            source_hash=self.source_hash,
        )

    def validate(self) -> None:
        self.as_conditioning().validate(SKHYNIX_CONTINUOUS_BINDING)


def build_skhynix_continuous_probability_snapshot(
    *,
    current: CurrentConditioning,
    as_of_date: str,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    provenance_path: str | Path = DEFAULT_PROVENANCE_PATH,
) -> ContinuousProbabilityCalibrationSnapshot:
    """Run v3.2 continuous financial-path Monte Carlo on the SK hynix calibration."""
    return build_continuous_probability_snapshot(
        binding=SKHYNIX_CONTINUOUS_BINDING,
        conditioning=current.as_conditioning(),
        as_of_date=as_of_date,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
    )
