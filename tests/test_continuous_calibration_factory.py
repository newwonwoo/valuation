"""The artifact factory: from resolved cohort history to a CALIBRATED certificate.

The round trip is the proof: the factory's output must survive the assembly's
full verification (artifact hash, dataset hash, provenance lineage, knowledge
cutoff, target exclusion, chronological OOS shape) and end in a certificate the
weighting gate accepts. The refusal tests are the containment: a dataset that
smuggles the target in, an artifact edited after the fact, or a cohort too thin
to mean anything must all fail closed with their reason named.
"""

from __future__ import annotations

import copy
from decimal import Decimal
import json
import random

import pytest

from valuation_engine.continuous_calibration_factory import (
    CalibrationFactoryError,
    CohortObservation,
    ConditioningDeclaration,
    build_continuous_calibration_artifact,
    write_artifact_files,
)
from valuation_engine.continuous_probability_assembly import (
    ContinuousCalibrationBinding,
    ContinuousCalibrationError,
    ContinuousConditioning,
    build_continuous_probability_snapshot,
)
from valuation_engine.records import CalibrationStatus


DRIVERS = ("revenue_growth", "operating_margin")
SCENARIOS = ("Down", "Core", "Bull")
TARGET = "104700"


def _cohort(companies: int = 8, years: range = range(2016, 2026)):
    rng = random.Random(7)
    rows = []
    for index in range(companies):
        growth, margin = rng.uniform(-0.1, 0.2), rng.uniform(0.02, 0.12)
        for year in years:
            growth = 0.02 + 0.5 * growth + rng.gauss(0, 0.06)
            margin = 0.03 + 0.6 * margin + rng.gauss(0, 0.02)
            rows.append(
                CohortObservation(
                    company_id=f"P{index:02d}",
                    period_end=f"{year}-12-31",
                    published_at=f"{year + 1}-03-20T09:00:00Z",
                    values=(
                        ("operating_margin", round(margin, 6)),
                        ("revenue_growth", round(growth, 6)),
                    ),
                    source_ref=f"https://dart.fss.or.kr/probe/P{index:02d}/{year}",
                )
            )
    return rows


def _conditioning() -> ConditioningDeclaration:
    return ConditioningDeclaration(
        values=(("operating_margin", 0.05), ("revenue_growth", -0.03)),
        source_ref="https://dart.fss.or.kr/probe/target/2025",
        first_seen_at="2026-03-20T09:00:00Z",
        source_hash="conditioning-hash-1",
    )


def _build(rows=None):
    return build_continuous_calibration_artifact(
        observations=rows if rows is not None else _cohort(),
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=5,
        excluded_ticker=TARGET,
        conditioning=_conditioning(),
    )


def _binding(result, artifact_path, provenance_path) -> ContinuousCalibrationBinding:
    constants = result.constants
    return ContinuousCalibrationBinding(
        cohort_key="kr.steel.long|5y_path|continuous_v1",
        forecast_class="kr.steel.long.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_factory_v1",
        mapping_version="factory_roundtrip_v1",
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=5,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
        expected_artifact_sha256=constants.expected_artifact_sha256,
        expected_provenance_artifact_sha256=constants.expected_provenance_artifact_sha256,
        expected_dataset_sha256=constants.expected_dataset_sha256,
        expected_provenance_hash=constants.expected_provenance_hash,
        expected_source_row_count=constants.expected_source_row_count,
        expected_source_company_count=constants.expected_source_company_count,
        excluded_ticker=TARGET,
        credible_level=Decimal("0.90"),
        outer_draws=200,
        inner_draws=100,
        seed=20260829,
    )


def _snapshot(result, tmp_path, as_of="2026-08-29"):
    artifact_path = tmp_path / "artifact.json"
    provenance_path = tmp_path / "prov.json"
    write_artifact_files(
        result, artifact_path=artifact_path, provenance_path=provenance_path
    )
    conditioning = ContinuousConditioning(
        readings=(
            ("operating_margin", Decimal("0.05")),
            ("revenue_growth", Decimal("-0.03")),
        ),
        source_ref="https://dart.fss.or.kr/probe/target/2025",
        first_seen_at="2026-03-20T09:00:00Z",
        source_hash="conditioning-hash-1",
    )
    return build_continuous_probability_snapshot(
        binding=_binding(result, artifact_path, provenance_path),
        conditioning=conditioning,
        as_of_date=as_of,
    )


def test_the_round_trip_ends_in_a_calibrated_certificate(tmp_path):
    snapshot = _snapshot(_build(), tmp_path)
    assert snapshot.status is CalibrationStatus.CALIBRATED
    total = sum(probability for _, probability in snapshot.probabilities)
    assert abs(total - Decimal("1")) < Decimal("1e-9")
    certificate = snapshot.certificate()
    certificate.validate_for_weighting()
    assert certificate.cohort_key == "kr.steel.long|5y_path|continuous_v1"


def test_a_dataset_containing_the_target_is_refused_not_cleaned():
    rows = _cohort()
    rows.append(
        CohortObservation(
            company_id=TARGET,
            period_end="2024-12-31",
            published_at="2025-03-20T09:00:00Z",
            values=(("operating_margin", 0.01), ("revenue_growth", -0.2)),
            source_ref="https://dart.fss.or.kr/probe/target/2024",
        )
    )
    with pytest.raises(CalibrationFactoryError, match="valuation target"):
        _build(rows)


def test_a_cohort_too_thin_to_mean_anything_is_refused():
    with pytest.raises(CalibrationFactoryError, match="at least 5 distinct"):
        _build(_cohort(companies=3))


def test_a_tampered_artifact_fails_the_assembly_hash_check(tmp_path):
    result = _build()
    tampered = copy.deepcopy(result.artifact)
    # Nudge one scenario path value after sealing: the declared hash no longer
    # replays, so the assembly refuses before anything simulates.
    tampered["scenarios"]["Bull"]["driver_paths"]["revenue_growth"][0] += 0.01
    artifact_path = tmp_path / "artifact.json"
    provenance_path = tmp_path / "prov.json"
    artifact_path.write_text(json.dumps(tampered), encoding="utf-8")
    provenance_path.write_text(json.dumps(result.provenance), encoding="utf-8")
    conditioning = ContinuousConditioning(
        readings=(
            ("operating_margin", Decimal("0.05")),
            ("revenue_growth", Decimal("-0.03")),
        ),
        source_ref="https://dart.fss.or.kr/probe/target/2025",
        first_seen_at="2026-03-20T09:00:00Z",
        source_hash="conditioning-hash-1",
    )
    with pytest.raises(ContinuousCalibrationError, match="hash mismatch"):
        build_continuous_probability_snapshot(
            binding=_binding(result, artifact_path, provenance_path),
            conditioning=conditioning,
            as_of_date="2026-08-29",
        )


def test_knowledge_time_holds_a_snapshot_before_training_publication(tmp_path):
    result = _build()
    with pytest.raises(PermissionError, match="published after"):
        _snapshot(result, tmp_path, as_of="2020-06-30")


def test_the_artifact_is_deterministic_for_the_same_rows():
    first = _build()
    second = _build()
    assert first.constants == second.constants
    assert first.artifact["artifact_sha256"] == second.artifact["artifact_sha256"]
