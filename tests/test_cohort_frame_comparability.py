"""A cohort that cannot frame its target says so, and says it by name.

셀트리온's FY2025 operating margin is 28.1%. The committed KR pharma cohort —
six listed Korean drug makers, FY2021-FY2025 — sits at a 7.0% latest-mean
margin, and the factory places its Bull reference path only one scale above
that. Conditioning the simulation on the target therefore starts it about five
scales above the outermost anchor, and nearest-scenario assignment collapses:
every simulated path is closer to Bull than to anything else.

That collapse used to surface twice as the wrong thing. It read as a 99.98%
Bull probability, which looks like near-certainty and is really the absence of
discrimination; and the snapshot's own invariant then threw, because a
saturated scenario's 5% sample quantile can sit above its normalized mean.

Both are pinned here. The run does not bind this cohort — the binding would be
a claim that it applies — but the artifact stays committed so the diagnosis is
reproducible rather than a story in a commit message.
"""

from __future__ import annotations

from decimal import Decimal
import json

import pytest
from pathlib import Path

from valuation_engine.continuous_probability_assembly import (
    ContinuousCalibrationBinding,
    build_continuous_probability_snapshot,
    conditioning_from_mapping,
)
from valuation_engine.continuous_probability_snapshot import CalibrationStatus

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "config" / "kr_pharma_calibration_artifact_ex068270.json"
PROVENANCE = ROOT / "config" / "kr_pharma_calibration_provenance_ex068270.json"
CONDITIONING = ROOT / "config" / "celltrion_conditioning_fy2025.json"


def _snapshot():
    binding = ContinuousCalibrationBinding(
        cohort_key="kr.pharma.manufacturing|5y_path|continuous_v1",
        forecast_class="kr.pharma.manufacturing.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_factory_v2",
        mapping_version="kr_pharma_cohort_ex068270_v1",
        driver_ids=("revenue_growth", "operating_margin"),
        scenario_ids=("Down", "Base", "Bull"),
        path_length=5,
        expected_artifact_sha256=(
            "97ecedf7e934d778c6c1211d69a40185343af65c01e3ab52bf6634f42365ed65"
        ),
        expected_provenance_artifact_sha256=(
            "e765a98970de992cdc8b1e553a25107865d1e48de9cd1f9b218253381daa88d9"
        ),
        expected_dataset_sha256=(
            "268b733f9983eb85630dd8033e16a5a3db3487db8e609d72b09b1d03155929f0"
        ),
        expected_provenance_hash=(
            "6197ca95f67ba71ae777cf947d07f98d500bc69d66523dbbfe6a9355df50f7fb"
        ),
        expected_source_row_count=30,
        expected_source_company_count=6,
        excluded_ticker="068270",
        artifact_path=str(ARTIFACT),
        provenance_path=str(PROVENANCE),
        seed=20260904,
        outer_draws=300,
        inner_draws=200,
    )
    declared = json.loads(CONDITIONING.read_text(encoding="utf-8"))
    conditioning = conditioning_from_mapping(
        declared["values"],
        binding=binding,
        source_ref=declared["source_ref"],
        first_seen_at=declared["first_seen_at"],
        source_hash=declared["source_hash"],
    )
    return build_continuous_probability_snapshot(
        binding=binding,
        conditioning=conditioning,
        as_of_date="2026-09-04",
        artifact_path=ARTIFACT,
        provenance_path=PROVENANCE,
    )


def test_a_cohort_that_cannot_frame_its_target_degrades_and_names_the_driver():
    snapshot = _snapshot()
    assert snapshot.status is CalibrationStatus.DEGRADED
    assert len(snapshot.integrity_findings) == 1
    finding = snapshot.integrity_findings[0]
    assert "operating_margin" in finding
    assert "cohort scenario anchors do not frame the target" in finding
    # Growth is not the problem: 셀트리온's conditioned growth path sits on the
    # cohort's central reference, so the check must not flag it.
    assert "revenue_growth" not in finding


def test_a_degraded_cohort_issues_no_weighting_certificate():
    with pytest.raises(PermissionError):
        _snapshot().certificate()


def test_a_saturated_scenario_still_reports_an_interval_containing_its_estimate():
    snapshot = _snapshot()
    for estimate in snapshot.estimates:
        assert (
            estimate.lower_probability
            <= estimate.probability
            <= estimate.upper_probability
        ), estimate.scenario_id
    bull = next(item for item in snapshot.estimates if item.scenario_id == "Bull")
    assert bull.probability > Decimal("0.99")
