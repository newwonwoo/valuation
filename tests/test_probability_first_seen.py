from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from valuation_engine.probability_calibration import (
    CalibrationPolicy,
    ForecastOutcome,
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
    build_calibration_snapshot,
)


def _forecast(
    forecast_id: str,
    *,
    issued_at: datetime,
    first_seen_at: datetime | None = None,
    supersedes_id: str | None = None,
    probability: str = "0.5",
) -> ProbabilityForecast:
    return ProbabilityForecast(
        forecast_id=forecast_id,
        event_key="EVENT-1",
        hypothesis_id="H-1",
        company_id="C-1",
        forecast_class="project_realization",
        horizon="90d",
        event_definition="gate within 90d",
        issued_at=issued_at,
        evaluation_deadline=date(2025, 6, 30),
        probability=Decimal(probability),
        displayed_band="P50",
        evidence_snapshot_hash=f"SNAP-{forecast_id}",
        model_version="m1",
        resolution_rule="primary source",
        resolution_source_policy="primary only",
        supersedes_id=supersedes_id,
        first_seen_at=first_seen_at,
    )


def _outcome(
    forecast_id: str,
    *,
    observed_at: datetime,
    first_seen_at: datetime | None = None,
) -> ForecastOutcome:
    return ForecastOutcome(
        forecast_id=forecast_id,
        observed_at=observed_at,
        outcome=ForecastOutcomeState.OCCURRED,
        outcome_evidence_ids=("OUT-1",),
        resolver_id="R",
        rationale="primary evidence",
        first_seen_at=first_seen_at,
    )


def _policy() -> CalibrationPolicy:
    return CalibrationPolicy(
        version="test",
        base_rate=Decimal("0.5"),
        min_resolved_events=1,
        min_companies=1,
        min_quarters=1,
        min_per_displayed_band=1,
        min_oos_windows=1,
        max_ece=Decimal("1"),
    )


def test_revision_requires_explicit_timezone_aware_first_seen_boundary():
    ledger = ProbabilityCalibrationLedger()
    first = _forecast(
        "F1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    ledger.append_forecast(first)

    missing = _forecast(
        "F2",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        supersedes_id="F1",
    )
    with pytest.raises(ValueError, match="explicit first_seen_at"):
        ledger.append_forecast(missing)

    naive = _forecast(
        "F3",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 2, 1),
        supersedes_id="F1",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.append_forecast(naive)

    before_issue = _forecast(
        "F4",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 1, 10, tzinfo=timezone.utc),
        supersedes_id="F1",
    )
    with pytest.raises(ValueError, match="cannot precede forecast issuance"):
        ledger.append_forecast(before_issue)


def test_late_discovered_backfill_does_not_rewrite_earlier_snapshot():
    ledger = ProbabilityCalibrationLedger()
    first = _forecast(
        "F1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        probability="0.4",
    )
    late_revision = _forecast(
        "F2",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        supersedes_id="F1",
        probability="0.7",
    )
    ledger.append_forecast(first)
    ledger.append_forecast(late_revision)

    early_terminal = ledger.terminal_forecasts(
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    late_terminal = ledger.terminal_forecasts(
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 5, 1, tzinfo=timezone.utc),
    )
    assert tuple(item.forecast_id for item in early_terminal) == ("F1",)
    assert tuple(item.forecast_id for item in late_terminal) == ("F2",)


def test_snapshot_cutoff_uses_first_seen_for_forecast_and_outcome():
    ledger = ProbabilityCalibrationLedger()
    item = _forecast(
        "F1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    ledger.append_forecast(item)
    ledger.append_outcome(
        _outcome(
            "F1",
            observed_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
            first_seen_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        )
    )

    before = build_calibration_snapshot(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 3, 1, tzinfo=timezone.utc),
        policy=_policy(),
        mapping_version="m1",
        oos_brier_skill_windows=(Decimal("0.1"),),
    )
    after = build_calibration_snapshot(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 5, 1, tzinfo=timezone.utc),
        policy=_policy(),
        mapping_version="m1",
        oos_brier_skill_windows=(Decimal("0.1"),),
    )
    assert before.raw_sample_count == 1
    assert before.effective_sample_count == 0
    assert after.effective_sample_count == 1
    assert before.snapshot_hash != after.snapshot_hash


def test_serialization_and_replay_preserve_historical_knowledge_boundary():
    ledger = ProbabilityCalibrationLedger()
    first = _forecast(
        "F1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    revision = _forecast(
        "F2",
        issued_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        supersedes_id="F1",
    )
    ledger.append_forecast(first)
    ledger.append_forecast(revision)

    payload = ledger.to_payload()
    restored = ProbabilityCalibrationLedger.from_payload(payload)
    assert restored.to_payload() == payload
    assert restored.forecasts[1].first_seen_at == datetime(
        2025, 4, 1, tzinfo=timezone.utc
    )

    replay = restored.replay_as_of(datetime(2025, 3, 1, tzinfo=timezone.utc))
    assert tuple(item.forecast_id for item in replay.forecasts) == ("F1",)
    assert replay.to_payload() == restored.replay_as_of(
        datetime(2025, 3, 1, tzinfo=timezone.utc)
    ).to_payload()


def test_outcome_cannot_be_first_seen_before_forecast_revision():
    ledger = ProbabilityCalibrationLedger()
    item = _forecast(
        "F1",
        issued_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )
    ledger.append_forecast(item)
    outcome = _outcome(
        "F1",
        observed_at=datetime(2025, 1, 20, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 1, 25, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="before its forecast revision"):
        ledger.append_outcome(outcome)
