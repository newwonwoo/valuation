"""The continuous probability route runs for any company, not one hard-coded ticker.

Every fixture here describes a company that does not exist in this repository: a
different cohort, different drivers, a different forecast length and a different
scenario set. If the assembler still produces a weighting-grade certificate from
it, the route is generic; if it needed SK hynix's names, sizes or hashes, these
tests fail.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from valuation_engine.continuous_probability_assembly import (
    ContinuousCalibrationBinding,
    ContinuousCalibrationError,
    ContinuousConditioning,
    build_continuous_probability_snapshot,
    conditioning_from_mapping,
    stable_hash,
)
from valuation_engine.probability_calibration import CalibrationCertificate
from valuation_engine.records import CalibrationStatus
from valuation_engine.skhynix_continuous_probability import (
    SKHYNIX_CONTINUOUS_BINDING,
    CurrentConditioning,
)


# A shipbuilder, not a memory maker: three drivers, five years, two scenarios.
DRIVERS = ("order_intake_growth", "yard_utilisation", "steel_cost_ratio")
SCENARIOS = ("Bear", "Base")
YEARS = 5
TICKER = "009540"
LINEAGE_HASH = "b" * 64
CONDITIONING = {
    "order_intake_growth": "0.180",
    "yard_utilisation": "0.910",
    "steel_cost_ratio": "0.240",
}
SOURCE_REF = "https://example.test/shipbuilding/quarterly"
SOURCE_HASH = "c" * 64
FIRST_SEEN = "2026-08-20T09:00:00+00:00"
TRAINING_LATEST = "2026-08-10T23:59:59+09:00"


def _driver_row(index: int) -> dict:
    base = Decimal("0.05") * (index + 1)
    return {
        "path": {
            "mean": [str(base + Decimal("0.01") * year) for year in range(YEARS)],
            "scale": [str(Decimal("0.04")) for _ in range(YEARS)],
            "mean_uncertainty": [str(Decimal("0.02")) for _ in range(YEARS)],
            "lower_bound": "-1",
            "upper_bound": "3",
        },
        "posterior": {"mean_strength": 12.5},
        "diagnostic": {
            "skill_windows": ["0.11", "0.14", "0.12"],
            "likelihood_weight": "0.8",
            "uncertainty_inflation": "1.1",
            "resolved_cases": 210,
            "company_count": 17,
            "quarter_count": 14,
            "regime_similarity": "0.65",
        },
    }


def _scenario_row(offset: Decimal) -> dict:
    return {
        "driver_paths": {
            driver_id: [
                str(Decimal("0.05") * (index + 1) + offset) for _ in range(YEARS)
            ]
            for index, driver_id in enumerate(DRIVERS)
        },
        "driver_weights": {driver_id: "1" for driver_id in DRIVERS},
    }


def _artifact() -> dict:
    payload = {
        "version": "1.0",
        "source_dataset_sha256": "a" * 64,
        "provenance_hash": LINEAGE_HASH,
        "source_row_count": 210,
        "source_company_count": 17,
        "target_ticker_excluded": TICKER,
        "oos_split_order": ["TRAIN", "VALIDATION", "HOLDOUT", "FINAL_OOS"],
        "drivers": {
            driver_id: _driver_row(index) for index, driver_id in enumerate(DRIVERS)
        },
        "scenarios": {
            "Bear": _scenario_row(Decimal("-0.04")),
            "Base": _scenario_row(Decimal("0")),
        },
        "dependence": {
            "version": "shipbuilding_residual_correlation_v1",
            "student_t_df": 6,
            "correlation_matrix": [
                ["1", "0.3", "-0.2"],
                ["0.3", "1", "-0.1"],
                ["-0.2", "-0.1", "1"],
            ],
        },
        "current_conditioning": {
            **CONDITIONING,
            "first_seen_at": FIRST_SEEN,
            "source_hash": SOURCE_HASH,
        },
    }
    payload["artifact_sha256"] = stable_hash(payload)
    return payload


def _provenance() -> dict:
    return {
        "version": "1.0",
        "source_dataset_sha256": "a" * 64,
        "source_provenance_hash": LINEAGE_HASH,
        "target_ticker_excluded": TICKER,
        "training_latest_publication_at": TRAINING_LATEST,
        "current_conditioning_source_ref": SOURCE_REF,
        "current_conditioning_source_hash": SOURCE_HASH,
        "current_conditioning_first_seen_at": FIRST_SEEN,
    }


def _write(root: Path, artifact: dict, provenance: dict) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    artifact_path = root / "artifact.json"
    provenance_path = root / "provenance.json"
    for path, payload in ((artifact_path, artifact), (provenance_path, provenance)):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return artifact_path, provenance_path


def _binding(root: Path, artifact: dict, provenance: dict, **overrides):
    artifact_path, provenance_path = _write(root, artifact, provenance)
    hash_payload = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    defaults = dict(
        cohort_key="shipbuilding|5y_path|continuous_v1",
        forecast_class="industrial.shipbuilding.continuous_financial_path",
        horizon="5y_path_from_12m_transitions",
        method_version="probability_engine_v3.2_continuous_financial_path_v1",
        mapping_version="shipbuilding_continuous_v1",
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=YEARS,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
        expected_artifact_sha256=stable_hash(hash_payload),
        expected_provenance_artifact_sha256=stable_hash(provenance),
        expected_dataset_sha256="a" * 64,
        expected_provenance_hash=LINEAGE_HASH,
        expected_source_row_count=210,
        expected_source_company_count=17,
        excluded_ticker=TICKER,
        seed=7,
        non_negative_driver_ids=("yard_utilisation",),
    )
    defaults.update(overrides)
    return ContinuousCalibrationBinding(**defaults)


def _conditioning(binding: ContinuousCalibrationBinding) -> ContinuousConditioning:
    return conditioning_from_mapping(
        CONDITIONING,
        binding=binding,
        source_ref=SOURCE_REF,
        first_seen_at=FIRST_SEEN,
        source_hash=SOURCE_HASH,
    )


@pytest.fixture
def bound(tmp_path: Path):
    binding = _binding(tmp_path, _artifact(), _provenance())
    return binding, _conditioning(binding)


def _build(bound, **kwargs):
    binding, conditioning = bound
    return build_continuous_probability_snapshot(
        binding=binding,
        conditioning=conditioning,
        as_of_date=kwargs.pop("as_of_date", "2026-08-27"),
        **kwargs,
    )


# ------------------------------------------------------------------- the point


def test_a_company_that_is_not_sk_hynix_reaches_a_calibrated_snapshot(bound):
    snapshot = _build(bound)
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.probability_source == "continuous_financial_path_monte_carlo"
    assert {item.scenario_id for item in snapshot.estimates} == set(SCENARIOS)
    total = sum((item.probability for item in snapshot.estimates), Decimal("0"))
    assert abs(total - Decimal("1")) <= Decimal("1e-12")


def test_that_snapshot_issues_a_certificate_the_runtime_socket_accepts(bound):
    certificate = _build(bound).certificate()
    assert isinstance(certificate, CalibrationCertificate)
    certificate.validate_for_weighting()
    assert certificate.cohort_key == "shipbuilding|5y_path|continuous_v1"


def test_driver_count_and_forecast_length_are_not_fixed_at_sk_hynix_values(bound):
    snapshot = _build(bound)
    assert len(snapshot.driver_snapshot_hashes) == 3
    assert {driver_id for driver_id, _ in snapshot.driver_snapshot_hashes} == set(DRIVERS)
    assert len(snapshot.oos_diagnostics) == 3


def test_a_second_company_binding_produces_a_different_snapshot_hash(tmp_path: Path):
    first = _binding(tmp_path / "a", _artifact(), _provenance())
    second = _binding(
        tmp_path / "b",
        _artifact(),
        _provenance(),
        cohort_key="shipbuilding|5y_path|continuous_v2",
    )
    left = build_continuous_probability_snapshot(
        binding=first, conditioning=_conditioning(first), as_of_date="2026-08-27"
    )
    right = build_continuous_probability_snapshot(
        binding=second, conditioning=_conditioning(second), as_of_date="2026-08-27"
    )
    assert left.snapshot_hash != right.snapshot_hash


# ------------------------------------------------------------ guards travel too


def test_lookahead_replay_is_refused_for_the_new_company(bound):
    with pytest.raises(PermissionError, match="after the requested snapshot cutoff"):
        _build(bound, as_of_date="2026-08-01")


def test_conditioning_seen_after_the_cutoff_is_refused(tmp_path: Path):
    binding = _binding(tmp_path, _artifact(), _provenance())
    with pytest.raises(PermissionError, match="conditioning was first seen after"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=_conditioning(binding),
            as_of_date="2026-08-15",
        )


def test_the_binding_ticker_must_match_the_artifact_exclusion(tmp_path: Path):
    binding = _binding(
        tmp_path, _artifact(), _provenance(), excluded_ticker="005930"
    )
    with pytest.raises(ContinuousCalibrationError, match="must exclude target rows"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=_conditioning(binding),
            as_of_date="2026-08-27",
        )


def test_a_value_field_anywhere_in_the_artifact_is_refused(tmp_path: Path):
    artifact = _artifact()
    artifact["scenarios"]["Base"]["scenario_intrinsic_value"] = 1000
    del artifact["artifact_sha256"]
    artifact["artifact_sha256"] = stable_hash(artifact)
    binding = _binding(tmp_path, artifact, _provenance())
    with pytest.raises(ContinuousCalibrationError, match="forbidden value/binary-event"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=_conditioning(binding),
            as_of_date="2026-08-27",
        )


def test_a_cohort_may_ban_extra_keys_of_its_own(tmp_path: Path):
    artifact = _artifact()
    artifact["drivers"]["yard_utilisation"]["berth_outage_flag"] = True
    del artifact["artifact_sha256"]
    artifact["artifact_sha256"] = stable_hash(artifact)
    binding = _binding(
        tmp_path,
        artifact,
        _provenance(),
        extra_forbidden_artifact_keys=frozenset({"berth_outage_flag"}),
    )
    with pytest.raises(ContinuousCalibrationError, match="berth_outage_flag"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=_conditioning(binding),
            as_of_date="2026-08-27",
        )


def test_conditioning_drift_from_the_artifact_is_refused(bound):
    binding, _ = bound
    drifted = conditioning_from_mapping(
        {**CONDITIONING, "yard_utilisation": "0.950"},
        binding=binding,
        source_ref=SOURCE_REF,
        first_seen_at=FIRST_SEEN,
        source_hash=SOURCE_HASH,
    )
    with pytest.raises(ContinuousCalibrationError, match="conditioning drift"):
        build_continuous_probability_snapshot(
            binding=binding, conditioning=drifted, as_of_date="2026-08-27"
        )


def test_a_forecast_length_that_disagrees_with_the_artifact_is_refused(tmp_path: Path):
    binding = _binding(tmp_path, _artifact(), _provenance(), path_length=9)
    with pytest.raises(ContinuousCalibrationError, match="exactly 9 annual values"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=_conditioning(binding),
            as_of_date="2026-08-27",
        )


# ---------------------------------------------------------------- binding rules


def test_binding_rejects_a_non_negative_constraint_on_an_unmodelled_driver(tmp_path: Path):
    binding = _binding(
        tmp_path, _artifact(), _provenance(), non_negative_driver_ids=("backlog",)
    )
    with pytest.raises(ContinuousCalibrationError, match="unmodelled driver"):
        binding.validate()


def test_binding_rejects_a_single_scenario(tmp_path: Path):
    binding = _binding(tmp_path, _artifact(), _provenance(), scenario_ids=("Base",))
    with pytest.raises(ContinuousCalibrationError, match="two distinct scenarios"):
        binding.validate()


def test_binding_rejects_a_driver_named_after_a_forbidden_key(tmp_path: Path):
    binding = _binding(
        tmp_path,
        _artifact(),
        _provenance(),
        driver_ids=("target_price", "yard_utilisation"),
    )
    with pytest.raises(ContinuousCalibrationError, match="forbidden artifact key"):
        binding.validate()


def test_conditioning_must_cover_exactly_the_bound_drivers(bound):
    binding, _ = bound
    partial = ContinuousConditioning(
        readings=(("order_intake_growth", Decimal("0.18")),),
        source_ref=SOURCE_REF,
        first_seen_at=FIRST_SEEN,
        source_hash=SOURCE_HASH,
    )
    with pytest.raises(ContinuousCalibrationError, match="driver coverage mismatch"):
        partial.validate(binding)


def test_a_non_http_conditioning_source_is_refused(bound):
    binding, _ = bound
    offline = ContinuousConditioning(
        readings=tuple(
            (driver_id, Decimal(CONDITIONING[driver_id])) for driver_id in DRIVERS
        ),
        source_ref="internal-note",
        first_seen_at=FIRST_SEEN,
        source_hash=SOURCE_HASH,
    )
    with pytest.raises(ContinuousCalibrationError, match="requires an HTTP source"):
        offline.validate(binding)


def test_a_negative_reading_on_a_non_negative_driver_is_refused(bound):
    binding, _ = bound
    negative = conditioning_from_mapping(
        {**CONDITIONING, "yard_utilisation": "-0.1"},
        binding=binding,
        source_ref=SOURCE_REF,
        first_seen_at=FIRST_SEEN,
        source_hash=SOURCE_HASH,
    )
    with pytest.raises(ContinuousCalibrationError, match="cannot be negative"):
        negative.validate(binding)


# --------------------------------------------------------- sk hynix is a binding


def test_sk_hynix_is_now_a_declaration_on_the_same_assembler():
    SKHYNIX_CONTINUOUS_BINDING.validate()
    assert SKHYNIX_CONTINUOUS_BINDING.excluded_ticker == "000660"
    assert SKHYNIX_CONTINUOUS_BINDING.path_length == 9
    assert len(SKHYNIX_CONTINUOUS_BINDING.driver_ids) == 4


def test_sk_hynix_conditioning_converts_into_the_generic_form():
    current = CurrentConditioning(
        revenue_growth=Decimal("2.5678"),
        operating_margin=Decimal("0.7633"),
        cash_conversion=Decimal("0.6901"),
        capex_intensity=Decimal("0.13453859948819666"),
        source_ref="https://example.test/000660",
        first_seen_at="2026-08-28T14:59:00.214081+00:00",
        source_hash="d" * 64,
    )
    generic = current.as_conditioning()
    assert tuple(driver_id for driver_id, _ in generic.readings) == (
        SKHYNIX_CONTINUOUS_BINDING.driver_ids
    )
    assert generic.as_map() == current.as_map()
