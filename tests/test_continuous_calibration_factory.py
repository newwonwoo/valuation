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
from dataclasses import replace
from decimal import Decimal
import json
import random

import pytest

from valuation_engine.continuous_calibration_factory import (
    _CANONICAL_FLOAT_SIGNIFICANT_DIGITS,
    CalibrationFactoryError,
    CohortObservation,
    ConditioningDeclaration,
    _chronological_splits,
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


def _build(rows=None, conditioning=None):
    return build_continuous_calibration_artifact(
        observations=rows if rows is not None else _cohort(),
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=5,
        excluded_ticker=TARGET,
        conditioning=conditioning or _conditioning(),
    )


def _binding(result, artifact_path, provenance_path) -> ContinuousCalibrationBinding:
    constants = result.constants
    return ContinuousCalibrationBinding(
        cohort_key="kr.steel.long|5y_path|continuous_v1",
        forecast_class="kr.steel.long.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_factory_v2",
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


def _snapshot(result, tmp_path, as_of="2026-08-29", conditioning=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact_path = tmp_path / "artifact.json"
    provenance_path = tmp_path / "prov.json"
    write_artifact_files(
        result, artifact_path=artifact_path, provenance_path=provenance_path
    )
    declaration = conditioning or _conditioning()
    current = ContinuousConditioning(
        readings=tuple(
            sorted((key, Decimal(str(value))) for key, value in declaration.values)
        ),
        source_ref=declaration.source_ref,
        first_seen_at=declaration.first_seen_at,
        source_hash=declaration.source_hash,
    )
    return build_continuous_probability_snapshot(
        binding=_binding(result, artifact_path, provenance_path),
        conditioning=current,
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


def test_target_conditioning_changes_paths_and_calibrated_probabilities(tmp_path):
    baseline = _conditioning()
    changed = replace(
        baseline,
        values=(("operating_margin", 0.051), ("revenue_growth", -0.029)),
        source_hash="conditioning-hash-2",
    )
    left = _build(conditioning=baseline)
    right = _build(conditioning=changed)
    assert left.artifact["drivers"]["revenue_growth"]["path"]["mean"] != (
        right.artifact["drivers"]["revenue_growth"]["path"]["mean"]
    )
    assert left.artifact["scenarios"] == right.artifact["scenarios"]

    left_snapshot = _snapshot(left, tmp_path / "left", conditioning=baseline)
    right_snapshot = _snapshot(right, tmp_path / "right", conditioning=changed)
    assert left_snapshot.probabilities != right_snapshot.probabilities


def test_oos_splits_keep_periods_whole_and_strictly_order_knowledge_time():
    splits = _chronological_splits(_cohort())
    period_owners = {}
    for index, split in enumerate(splits):
        for period_end in {row.period_end for row in split}:
            assert period_end not in period_owners
            period_owners[period_end] = index
    training_latest = max(row.published_at for row in splits[0])
    assert all(
        training_latest < min(row.published_at for row in split)
        for split in splits[1:]
    )


def test_overlapping_period_publication_windows_are_refused():
    rows = _cohort(companies=5, years=range(2018, 2023))
    for index, row in enumerate(rows):
        if row.company_id == "P00" and row.period_end == "2019-12-31":
            rows[index] = replace(row, published_at="2022-06-01T09:00:00Z")
            break
    with pytest.raises(CalibrationFactoryError, match="not strictly later"):
        _chronological_splits(rows)


def test_each_oos_window_keeps_the_same_frozen_training_cutoff():
    result = _build()
    windows = result.artifact["oos_windows"]
    training_latest = windows[0]["evaluation_latest_publication_at"]
    assert all(
        window["training_latest_publication_at"] == training_latest
        for window in windows[1:]
    )
    assert all(len(window["period_ends"]) == 1 for window in windows[1:])


def test_an_oos_window_without_frozen_origin_cases_is_refused():
    rows = [
        row
        for row in _cohort(companies=7)
        if (
            int(row.period_end[:4]) <= 2022
            and row.company_id in {f"P{index:02d}" for index in range(5)}
        )
        or (
            int(row.period_end[:4]) >= 2023
            and row.company_id in {"P05", "P06"}
        )
    ]
    with pytest.raises(CalibrationFactoryError, match="has no OOS forecast cases"):
        _build(rows)


@pytest.mark.parametrize(
    "dataset",
    (
        "config/kr_steel_cohort_dataset.json",
        "config/kr_steel_cohort_dataset_ex084010.json",
    ),
)
def test_committed_steel_oos_windows_are_whole_and_knowledge_ordered(dataset):
    from valuation_engine.continuous_calibration_factory import load_cohort_dataset

    splits = _chronological_splits(load_cohort_dataset(dataset))
    seen_periods = set()
    for split in splits:
        periods = {row.period_end for row in split}
        assert not periods.intersection(seen_periods)
        seen_periods.update(periods)
    training_latest = max(row.published_at for row in splits[0])
    assert all(
        training_latest < min(row.published_at for row in split)
        for split in splits[1:]
    )


def test_computed_artifact_floats_are_canonical_before_hashing():
    result = _build()

    def assert_canonical(value):
        if isinstance(value, float):
            assert value == float(
                format(value, f".{_CANONICAL_FLOAT_SIGNIFICANT_DIGITS}g")
            )
        elif isinstance(value, dict):
            for item in value.values():
                assert_canonical(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                assert_canonical(item)

    assert_canonical(result.artifact)


# ------------------------------------------- the REAL cohort, pinned in-repo


def test_the_committed_kr_steel_artifact_reproduces_from_its_committed_dataset():
    """The repo carries the first real cohort: 91 company-year rows of filed
    OFS financials for 12 listed KR steel companies (2017-2024, fetched from
    public OpenDART endpoints), the artifact fitted from them, and KISCO's own
    FY2025 conditioning. This pins that the committed artifact IS the factory's
    deterministic output for the committed dataset — same hashes, bit for bit —
    and that the pair yields a CALIBRATED snapshot and weighting certificate
    for a post-publication as_of."""
    import json
    from decimal import Decimal
    from pathlib import Path

    from valuation_engine.continuous_calibration_factory import load_cohort_dataset

    root = Path("config")
    committed_artifact = json.loads(
        (root / "kr_steel_calibration_artifact.json").read_text(encoding="utf-8")
    )
    committed_provenance = json.loads(
        (root / "kr_steel_calibration_provenance.json").read_text(encoding="utf-8")
    )
    cond = json.loads(
        (root / "kisco_conditioning_fy2025.json").read_text(encoding="utf-8")
    )
    rows = load_cohort_dataset(root / "kr_steel_cohort_dataset.json")
    assert len(rows) == 91
    assert len({row.company_id for row in rows}) == 12
    assert all(row.company_id != "104700" for row in rows)

    result = build_continuous_calibration_artifact(
        observations=rows,
        driver_ids=("revenue_growth", "operating_margin"),
        scenario_ids=("Down", "Base", "Bull"),
        path_length=5,
        excluded_ticker="104700",
        conditioning=ConditioningDeclaration(
            values=tuple(sorted((k, float(v)) for k, v in cond["values"].items())),
            source_ref=cond["source_ref"],
            first_seen_at=cond["first_seen_at"],
            source_hash=cond["source_hash"],
        ),
    )
    assert result.artifact == committed_artifact
    assert result.provenance == committed_provenance

    binding = ContinuousCalibrationBinding(
        cohort_key="kr.steel.long|5y_path|continuous_v1",
        forecast_class="kr.steel.long.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_factory_v2",
        mapping_version="kr_steel_cohort_v1",
        driver_ids=("revenue_growth", "operating_margin"),
        scenario_ids=("Down", "Base", "Bull"),
        path_length=5,
        artifact_path=root / "kr_steel_calibration_artifact.json",
        provenance_path=root / "kr_steel_calibration_provenance.json",
        expected_artifact_sha256=result.constants.expected_artifact_sha256,
        expected_provenance_artifact_sha256=result.constants.expected_provenance_artifact_sha256,
        expected_dataset_sha256=result.constants.expected_dataset_sha256,
        expected_provenance_hash=result.constants.expected_provenance_hash,
        expected_source_row_count=91,
        expected_source_company_count=12,
        excluded_ticker="104700",
        credible_level=Decimal("0.90"),
        outer_draws=300,
        inner_draws=200,
        seed=20260829,
    )
    conditioning = ContinuousConditioning(
        readings=tuple(
            sorted((k, Decimal(str(v))) for k, v in cond["values"].items())
        ),
        source_ref=cond["source_ref"],
        first_seen_at=cond["first_seen_at"],
        source_hash=cond["source_hash"],
    )
    snapshot = build_continuous_probability_snapshot(
        binding=binding, conditioning=conditioning, as_of_date="2026-08-29"
    )
    assert snapshot.status is CalibrationStatus.CALIBRATED
    snapshot.certificate().validate_for_weighting()
    total = sum(probability for _, probability in snapshot.probabilities)
    assert abs(total - Decimal("1")) < Decimal("1e-9")
