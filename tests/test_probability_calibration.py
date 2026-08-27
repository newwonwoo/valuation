from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.probability_adapter import probability_calibration_load_adapter
from valuation_engine.probability_calibration import (
    CalibrationCertificate,
    CalibrationPolicy,
    ForecastOutcome,
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
    build_calibration_snapshot,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import ScenarioBindingSpec, bind_scenarios
from valuation_engine.control_plane import ExecutionMode, StageStatus


def forecast(
    forecast_id: str,
    *,
    event_key: str,
    company_id: str,
    issued_at: datetime,
    probability: Decimal,
    band: str,
    supersedes_id: str | None = None,
    first_seen_at: datetime | None = None,
) -> ProbabilityForecast:
    return ProbabilityForecast(
        forecast_id=forecast_id,
        event_key=event_key,
        hypothesis_id=f"H-{event_key}",
        company_id=company_id,
        forecast_class="project_realization",
        horizon="90d",
        event_definition="binding project gate is achieved within 90 days",
        issued_at=issued_at,
        evaluation_deadline=date(
            issued_at.year + (1 if issued_at.month > 9 else 0),
            ((issued_at.month + 2) % 12) + 1,
            28,
        ),
        probability=probability,
        displayed_band=band,
        evidence_snapshot_hash=f"SNAP-{event_key}-{forecast_id}",
        model_version="m1",
        resolution_rule="primary source confirms gate by deadline",
        resolution_source_policy="primary evidence only",
        supersedes_id=supersedes_id,
        first_seen_at=(first_seen_at or issued_at) if supersedes_id else first_seen_at,
    )


def resolved(
    forecast_id: str,
    occurred: bool,
    observed_at: datetime,
) -> ForecastOutcome:
    return ForecastOutcome(
        forecast_id=forecast_id,
        observed_at=observed_at,
        outcome=(
            ForecastOutcomeState.OCCURRED
            if occurred
            else ForecastOutcomeState.NOT_OCCURRED
        ),
        outcome_evidence_ids=(f"OUTCOME-{forecast_id}",),
        resolver_id="PRIMARY_RESOLVER",
        rationale="resolved from primary evidence",
    )


def production_policy() -> CalibrationPolicy:
    return CalibrationPolicy(version="1.0", base_rate=Decimal("0.50"))


def build_promotable_ledger() -> ProbabilityCalibrationLedger:
    ledger = ProbabilityCalibrationLedger()
    bands = (
        (Decimal("0.10"), "P10", 4),
        (Decimal("0.30"), "P30", 12),
        (Decimal("0.50"), "P50", 20),
        (Decimal("0.70"), "P70", 28),
        (Decimal("0.90"), "P90", 36),
    )
    quarter_dates = (
        (2024, 1),
        (2024, 4),
        (2024, 7),
        (2024, 10),
        (2025, 1),
        (2025, 4),
        (2025, 7),
        (2025, 10),
    )
    index = 0
    for probability, band, successes in bands:
        for local in range(40):
            year, month = quarter_dates[index % len(quarter_dates)]
            issued = datetime(year, month, 1, 9, tzinfo=timezone.utc)
            forecast_id = f"F-{index:03d}"
            item = forecast(
                forecast_id,
                event_key=f"EVENT-{index:03d}",
                company_id=f"C-{index % 20:02d}",
                issued_at=issued,
                probability=probability,
                band=band,
            )
            ledger.append_forecast(item)
            ledger.append_outcome(
                resolved(
                    forecast_id,
                    local < successes,
                    datetime(
                        year,
                        min(month + 2, 12),
                        20,
                        9,
                        tzinfo=timezone.utc,
                    ),
                )
            )
            index += 1
    return ledger


def probability_assumption(scenario: str, probability: str) -> CompiledAssumption:
    return CompiledAssumption(
        key="scenario_probability",
        scenario_id=scenario,
        measure=Measure(Decimal(probability), "ratio", "2026-06-30"),
        bridge_id=f"B-{scenario}",
        evidence_ids=(f"E-{scenario}",),
        hypothesis_id=f"H-{scenario}",
        economic_path_id=f"probability:{scenario}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{scenario}",
        calibration_status=CalibrationStatus.CALIBRATED,
    )


def compiled_probabilities() -> CompiledAssumptionSet:
    return CompiledAssumptionSet(
        "T",
        (
            probability_assumption("Bear", "0.2"),
            probability_assumption("Base", "0.5"),
            probability_assumption("Bull", "0.3"),
        ),
        "ASSUMPTION-HASH",
    )


def test_production_promotion_gate_issues_certificate():
    snapshot = build_calibration_snapshot(
        build_promotable_ledger(),
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        policy=production_policy(),
        mapping_version="map-v1",
        oos_brier_skill_windows=(Decimal("0.10"), Decimal("0.06")),
        dataset_hash="DATASET1",
    )
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.effective_sample_count == 200
    assert snapshot.company_count == 20
    assert snapshot.quarter_count == 8
    assert all(count >= 30 for _, count in snapshot.band_counts)
    assert snapshot.brier_skill_score is not None and snapshot.brier_skill_score > 0
    assert snapshot.ece is not None and snapshot.ece <= Decimal("0.08")
    certificate = snapshot.certificate()
    certificate.validate_for_weighting()
    assert certificate.snapshot_hash == snapshot.snapshot_hash


def test_revisions_do_not_inflate_effective_sample_count():
    ledger = ProbabilityCalibrationLedger()
    first = forecast(
        "F1",
        event_key="EVENT-1",
        company_id="C1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        probability=Decimal("0.4"),
        band="P40",
    )
    second = forecast(
        "F2",
        event_key="EVENT-1",
        company_id="C1",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        probability=Decimal("0.6"),
        band="P60",
        supersedes_id="F1",
    )
    ledger.append_forecast(first)
    ledger.append_forecast(second)
    with pytest.raises(ValueError, match="terminal forecast revision"):
        ledger.append_outcome(
            resolved("F1", True, datetime(2025, 2, 1, tzinfo=timezone.utc))
        )
    ledger.append_outcome(
        resolved("F2", True, datetime(2025, 2, 1, tzinfo=timezone.utc))
    )
    snapshot = build_calibration_snapshot(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 3, 1, tzinfo=timezone.utc),
        policy=CalibrationPolicy(
            version="test",
            base_rate=Decimal("0.5"),
            min_resolved_events=1,
            min_companies=1,
            min_quarters=1,
            min_per_displayed_band=1,
            min_oos_windows=1,
            max_ece=Decimal("1"),
        ),
        mapping_version="test-map",
        oos_brier_skill_windows=(Decimal("0.01"),),
    )
    assert snapshot.raw_sample_count == 2
    assert snapshot.effective_sample_count == 1


def test_insufficient_history_cannot_issue_certificate():
    ledger = ProbabilityCalibrationLedger()
    item = forecast(
        "F1",
        event_key="EVENT-1",
        company_id="C1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        probability=Decimal("0.6"),
        band="P60",
    )
    ledger.append_forecast(item)
    ledger.append_outcome(
        resolved("F1", True, datetime(2025, 2, 1, tzinfo=timezone.utc))
    )
    snapshot = build_calibration_snapshot(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 3, 1, tzinfo=timezone.utc),
        policy=production_policy(),
        mapping_version="map-v1",
        oos_brier_skill_windows=(),
    )
    assert snapshot.status is CalibrationStatus.CALIBRATING
    with pytest.raises(PermissionError):
        snapshot.certificate()


def test_live_weighting_requires_matching_certificate():
    spec = ScenarioBindingSpec(
        ("Bear", "Base", "Bull"),
        ("scenario_probability",),
        "scenario_probability",
        "project_realization|90d",
    )
    without = bind_scenarios(
        compiled_probabilities(), spec, require_calibration_certificate=True
    )
    assert not without.passed
    assert without.findings[0].code == "CALIBRATION_CERTIFICATE_REQUIRED"

    certificate = CalibrationCertificate(
        "project_realization|90d",
        "project_realization",
        "90d",
        "1.0",
        "map-v1",
        "CALIBRATION-SNAPSHOT",
        CalibrationStatus.CALIBRATED,
        "DATASET1",
    )
    with_cert = bind_scenarios(
        compiled_probabilities(),
        spec,
        calibration_certificate=certificate,
        require_calibration_certificate=True,
    )
    assert with_cert.passed
    assert with_cert.scenario_set.numeric_weighting_allowed
    assert with_cert.scenario_set.calibration_snapshot_hash == "CALIBRATION-SNAPSHOT"
    assert with_cert.scenario_set.calibration_dataset_hash == "DATASET1"

    wrong = CalibrationCertificate(
        "clinical|90d",
        "clinical",
        "90d",
        "1.0",
        "map-v1",
        "OTHER",
        CalibrationStatus.CALIBRATED,
        "DATASET1",
    )
    mismatch = bind_scenarios(
        compiled_probabilities(),
        spec,
        calibration_certificate=wrong,
        require_calibration_certificate=True,
    )
    assert not mismatch.passed
    assert mismatch.findings[0].code == "CALIBRATION_COHORT_MISMATCH"


def test_shadow_compatibility_does_not_require_certificate():
    spec = ScenarioBindingSpec(
        ("Bear", "Base", "Bull"),
        ("scenario_probability",),
        "scenario_probability",
    )
    result = bind_scenarios(compiled_probabilities(), spec)
    assert result.passed
    assert result.scenario_set.numeric_weighting_allowed
    assert result.scenario_set.calibration_snapshot_hash is None


def test_calibration_loader_emits_certificate_only_after_promotion():
    calibrated = build_calibration_snapshot(
        build_promotable_ledger(),
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        policy=production_policy(),
        mapping_version="map-v1",
        oos_brier_skill_windows=(Decimal("0.10"), Decimal("0.06")),
        dataset_hash="DATASET1",
    )
    adapter = probability_calibration_load_adapter(
        loader=lambda _: calibrated,
        expected_cohort_key="project_realization|90d",
    )
    result = adapter(
        OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {})
    )
    assert result.status is StageStatus.PASS
    assert "probability_calibration_certificate" in result.outputs
