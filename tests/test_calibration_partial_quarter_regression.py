from datetime import date, datetime, timezone
from decimal import Decimal

from valuation_engine.calibration_dataset import _derive_oos_brier_skill_windows
from valuation_engine.probability_calibration import (
    ForecastOutcome,
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
)


def _forecast(
    *,
    forecast_id: str,
    event_key: str,
    issued_at: datetime,
    deadline: date,
) -> ProbabilityForecast:
    return ProbabilityForecast(
        forecast_id=forecast_id,
        event_key=event_key,
        hypothesis_id=f"H-{forecast_id}",
        company_id=f"C-{forecast_id}",
        forecast_class="project_realization",
        horizon="90d",
        event_definition="project gate within 90 days",
        issued_at=issued_at,
        evaluation_deadline=deadline,
        probability=Decimal("0.6"),
        displayed_band="P60",
        evidence_snapshot_hash=f"SNAP-{forecast_id}",
        model_version="m1",
        resolution_rule="primary evidence by deadline",
        resolution_source_policy="primary only",
        first_seen_at=issued_at,
    )


def _outcome(
    *, forecast_id: str, observed_at: datetime
) -> ForecastOutcome:
    return ForecastOutcome(
        forecast_id=forecast_id,
        observed_at=observed_at,
        outcome=ForecastOutcomeState.OCCURRED,
        outcome_evidence_ids=(f"E-{forecast_id}",),
        resolver_id="R1",
        rationale="primary source confirmed",
        first_seen_at=observed_at,
    )


def test_same_issuance_quarter_is_not_scored_until_every_deadline_closes():
    ledger = ProbabilityCalibrationLedger()
    first_issued = datetime(2025, 1, 1, 9, tzinfo=timezone.utc)
    second_issued = datetime(2025, 3, 1, 9, tzinfo=timezone.utc)
    ledger.append_forecast(
        _forecast(
            forecast_id="F1",
            event_key="EV1",
            issued_at=first_issued,
            deadline=date(2025, 2, 1),
        )
    )
    ledger.append_outcome(
        _outcome(
            forecast_id="F1",
            observed_at=datetime(2025, 2, 1, 9, tzinfo=timezone.utc),
        )
    )
    ledger.append_forecast(
        _forecast(
            forecast_id="F2",
            event_key="EV2",
            issued_at=second_issued,
            deadline=date(2025, 6, 1),
        )
    )
    # F2 resolves positively before its deadline. This early success must not make
    # 2025Q1 eligible while a negative outcome could still remain unresolved until June.
    ledger.append_outcome(
        _outcome(
            forecast_id="F2",
            observed_at=datetime(2025, 5, 1, 9, tzinfo=timezone.utc),
        )
    )

    before_second_outcome = _derive_oos_brier_skill_windows(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 4, 1, 12, tzinfo=timezone.utc),
        base_rate=Decimal("0.4"),
        required_windows=1,
    )
    after_early_success_but_before_deadline = _derive_oos_brier_skill_windows(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 5, 15, 12, tzinfo=timezone.utc),
        base_rate=Decimal("0.4"),
        required_windows=1,
    )
    after_quarter_fully_matures = _derive_oos_brier_skill_windows(
        ledger,
        forecast_class="project_realization",
        horizon="90d",
        cutoff=datetime(2025, 6, 2, 12, tzinfo=timezone.utc),
        base_rate=Decimal("0.4"),
        required_windows=1,
    )

    assert before_second_outcome == ()
    assert after_early_success_but_before_deadline == ()
    assert len(after_quarter_fully_matures) == 1
    assert after_quarter_fully_matures[0] > 0
