from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from valuation_engine.calibration_dataset import (
    CalibrationCohortDeclaration,
    load_declared_calibration_dataset,
)
from valuation_engine.probability_calibration import (
    ForecastOutcome,
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
)


def _declaration():
    return CalibrationCohortDeclaration(
        forecast_class="project_realization",
        horizon="90d",
        base_rate=Decimal("0.40"),
        mapping_version="project-realization-v1",
        dataset_version="2026Q3-v1",
        source_ref="internal://resolved-forecast-history/2026Q3",
    )


def _ledger(*, second_seen_at=None):
    ledger = ProbabilityCalibrationLedger()
    first_seen = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)
    forecast = ProbabilityForecast(
        forecast_id="F1",
        event_key="EV1",
        hypothesis_id="H1",
        company_id="C1",
        forecast_class="project_realization",
        horizon="90d",
        event_definition="project gate within 90 days",
        issued_at=datetime(2025, 1, 1, 9, tzinfo=timezone.utc),
        evaluation_deadline=date(2025, 4, 1),
        probability=Decimal("0.6"),
        displayed_band="P60",
        evidence_snapshot_hash="SNAP1",
        model_version="m1",
        resolution_rule="primary evidence by deadline",
        resolution_source_policy="primary only",
        first_seen_at=first_seen,
    )
    ledger.append_forecast(forecast)
    ledger.append_outcome(
        ForecastOutcome(
            forecast_id="F1",
            observed_at=datetime(2025, 2, 1, 9, tzinfo=timezone.utc),
            outcome=ForecastOutcomeState.OCCURRED,
            outcome_evidence_ids=("E-OUT-1",),
            resolver_id="R1",
            rationale="primary source confirmed",
            first_seen_at=datetime(2025, 2, 1, 10, tzinfo=timezone.utc),
        )
    )
    if second_seen_at is not None:
        ledger.append_forecast(
            ProbabilityForecast(
                forecast_id="F2",
                event_key="EV2",
                hypothesis_id="H2",
                company_id="C2",
                forecast_class="project_realization",
                horizon="90d",
                event_definition="project gate within 90 days",
                issued_at=datetime(2025, 3, 1, 9, tzinfo=timezone.utc),
                evaluation_deadline=date(2025, 6, 1),
                probability=Decimal("0.3"),
                displayed_band="P30",
                evidence_snapshot_hash="SNAP2",
                model_version="m1",
                resolution_rule="primary evidence by deadline",
                resolution_source_policy="primary only",
                first_seen_at=second_seen_at,
            )
        )
    return ledger


def _payload(ledger):
    declaration = _declaration()
    return {
        "cohort_key": declaration.cohort_key,
        "mapping_version": declaration.mapping_version,
        "dataset_version": declaration.dataset_version,
        "source_ref": declaration.source_ref,
        "ledger": ledger.to_payload(),
    }


def test_declared_dataset_hash_is_deterministic_and_replay_preserves_full_hash():
    payload = _payload(
        _ledger(second_seen_at=datetime(2025, 7, 1, 10, tzinfo=timezone.utc))
    )
    full = load_declared_calibration_dataset(payload, declaration=_declaration())
    replay = load_declared_calibration_dataset(
        payload,
        declaration=_declaration(),
        replay_cutoff=datetime(2025, 4, 1, tzinfo=timezone.utc),
    )
    assert full.dataset_hash == replay.dataset_hash
    assert tuple(item.forecast_id for item in full.ledger.forecasts) == ("F1", "F2")
    assert tuple(item.forecast_id for item in replay.ledger.forecasts) == ("F1",)


def test_dataset_rejects_post_hoc_cohort_mapping_or_source_changes():
    payload = _payload(_ledger())
    payload["mapping_version"] = "changed-after-results"
    with pytest.raises(ValueError, match="mapping_version"):
        load_declared_calibration_dataset(payload, declaration=_declaration())

    payload = _payload(_ledger())
    payload["source_ref"] = "other-source"
    with pytest.raises(ValueError, match="source_ref"):
        load_declared_calibration_dataset(payload, declaration=_declaration())


def test_dataset_rejects_forecasts_from_another_cohort():
    ledger = _ledger()
    payload = _payload(ledger)
    payload["ledger"]["forecasts"][0]["forecast_class"] = "clinical_event"
    with pytest.raises(ValueError, match="unexpected cohort"):
        load_declared_calibration_dataset(payload, declaration=_declaration())


def test_dataset_requires_explicit_first_seen_boundaries():
    payload = _payload(_ledger())
    payload["ledger"]["forecasts"][0]["first_seen_at"] = None
    with pytest.raises(ValueError, match="explicit first_seen_at"):
        load_declared_calibration_dataset(payload, declaration=_declaration())

    payload = _payload(_ledger())
    payload["ledger"]["outcomes"][0]["first_seen_at"] = None
    with pytest.raises(ValueError, match="explicit first_seen_at"):
        load_declared_calibration_dataset(payload, declaration=_declaration())


def test_dataset_uses_predeclared_base_rate_not_observed_outcome_rate(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v1
defaults:
  min_resolved_events: 1
  min_companies: 1
  min_quarters: 1
  min_per_displayed_band: 1
  min_oos_windows: 1
  max_ece: 1
  max_ambiguous_censored_rate: 1
  fixed_bin_edges: [0, 0.5, 1]
cohorts: {}
""",
        encoding="utf-8",
    )
    dataset = load_declared_calibration_dataset(
        _payload(_ledger()), declaration=_declaration()
    )
    snapshot = dataset.build_snapshot(
        cutoff=datetime(2025, 4, 1, tzinfo=timezone.utc),
        policy_path=policy,
    )
    # One realized success has an observed rate of 100%; the predeclared base rate is 40%.
    assert snapshot.brier_skill_score is not None
    assert snapshot.mapping_version == "project-realization-v1"
    assert snapshot.policy_version == "test-v1"
    assert snapshot.oos_brier_skill_windows == (Decimal("0.5555555555555555555555555556"),)
    assert snapshot.dataset_hash == dataset.dataset_hash
    assert snapshot.certificate().dataset_hash == dataset.dataset_hash


def test_declared_dataset_rejects_orphan_outcome_rows():
    payload = _payload(_ledger())
    payload["ledger"]["outcomes"].append(
        {**payload["ledger"]["outcomes"][0], "forecast_id": "MISSING"}
    )
    with pytest.raises(ValueError, match="orphan outcomes: MISSING"):
        load_declared_calibration_dataset(payload, declaration=_declaration())


def test_declared_dataset_applies_cohort_policy_overrides(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v2
defaults:
  min_resolved_events: 1
  min_companies: 1
  min_quarters: 1
  min_per_displayed_band: 1
  min_oos_windows: 1
  max_ece: 1
  max_ambiguous_censored_rate: 1
  fixed_bin_edges: [0, 0.5, 1]
cohorts:
  project_realization|90d:
    min_resolved_events: 2
""",
        encoding="utf-8",
    )
    dataset = load_declared_calibration_dataset(
        _payload(_ledger()), declaration=_declaration()
    )
    snapshot = dataset.build_snapshot(
        cutoff=datetime(2025, 4, 1, tzinfo=timezone.utc), policy_path=policy
    )
    assert snapshot.status.value == "CALIBRATING"
    assert "MIN_RESOLVED_EVENTS" in snapshot.gate_failures


def test_declared_dataset_does_not_accept_caller_supplied_oos_scores(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v1
defaults: {}
cohorts: {}
""",
        encoding="utf-8",
    )
    dataset = load_declared_calibration_dataset(
        _payload(_ledger()), declaration=_declaration()
    )
    with pytest.raises(TypeError, match="oos_brier_skill_windows"):
        dataset.build_snapshot(
            cutoff=datetime(2025, 4, 1, tzinfo=timezone.utc),
            policy_path=policy,
            oos_brier_skill_windows=(Decimal("1"),),
        )


def test_dataset_hash_changes_snapshot_and_certificate_lineage(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v1
defaults:
  min_resolved_events: 1
  min_companies: 1
  min_quarters: 1
  min_per_displayed_band: 1
  min_oos_windows: 1
  max_ece: 1
  max_ambiguous_censored_rate: 1
cohorts: {}
""",
        encoding="utf-8",
    )
    first_payload = _payload(_ledger())
    second_payload = _payload(_ledger())
    second_payload["ledger"]["outcomes"][0]["rationale"] = "same result, corrected rationale"
    first = load_declared_calibration_dataset(first_payload, declaration=_declaration())
    second = load_declared_calibration_dataset(second_payload, declaration=_declaration())
    cutoff = datetime(2025, 4, 1, tzinfo=timezone.utc)
    first_snapshot = first.build_snapshot(cutoff=cutoff, policy_path=policy)
    second_snapshot = second.build_snapshot(cutoff=cutoff, policy_path=policy)
    assert first.dataset_hash != second.dataset_hash
    assert first_snapshot.snapshot_hash != second_snapshot.snapshot_hash
    assert first_snapshot.certificate().dataset_hash == first.dataset_hash
    assert second_snapshot.certificate().dataset_hash == second.dataset_hash


def test_oos_excludes_quarter_until_every_forecast_deadline_has_closed(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v1
defaults:
  min_resolved_events: 1
  min_companies: 1
  min_quarters: 1
  min_per_displayed_band: 1
  min_oos_windows: 1
  max_ece: 1
  max_ambiguous_censored_rate: 1
cohorts: {}
""",
        encoding="utf-8",
    )
    dataset = load_declared_calibration_dataset(
        _payload(_ledger()), declaration=_declaration()
    )
    snapshot = dataset.build_snapshot(
        cutoff=datetime(2025, 2, 2, tzinfo=timezone.utc), policy_path=policy
    )
    assert snapshot.oos_brier_skill_windows == ()
    assert "OOS_BRIER_SKILL" in snapshot.gate_failures


def test_oos_uses_only_policy_required_recent_consecutive_windows(tmp_path):
    ledger = ProbabilityCalibrationLedger()
    rows = (
        ("F1", "EV1", datetime(2025, 1, 1, 9, tzinfo=timezone.utc), date(2025, 3, 31), ForecastOutcomeState.NOT_OCCURRED),
        ("F2", "EV2", datetime(2025, 4, 1, 9, tzinfo=timezone.utc), date(2025, 6, 30), ForecastOutcomeState.OCCURRED),
        ("F3", "EV3", datetime(2025, 7, 1, 9, tzinfo=timezone.utc), date(2025, 9, 30), ForecastOutcomeState.OCCURRED),
    )
    for forecast_id, event_key, issued_at, deadline, outcome_state in rows:
        ledger.append_forecast(
            ProbabilityForecast(
                forecast_id=forecast_id,
                event_key=event_key,
                hypothesis_id=f"H-{forecast_id}",
                company_id=f"C-{forecast_id}",
                forecast_class="project_realization",
                horizon="90d",
                event_definition="project gate within 90 days",
                issued_at=issued_at,
                evaluation_deadline=deadline,
                probability=Decimal("0.9"),
                displayed_band="P90",
                evidence_snapshot_hash=f"SNAP-{forecast_id}",
                model_version="m1",
                resolution_rule="primary evidence by deadline",
                resolution_source_policy="primary only",
                first_seen_at=issued_at,
            )
        )
        ledger.append_outcome(
            ForecastOutcome(
                forecast_id=forecast_id,
                observed_at=datetime.combine(deadline, datetime.min.time(), timezone.utc),
                outcome=outcome_state,
                outcome_evidence_ids=(f"E-{forecast_id}",),
                resolver_id="R1",
                rationale="primary source confirmed",
                first_seen_at=datetime.combine(deadline, datetime.min.time(), timezone.utc),
            )
        )
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """version: test-v1
defaults:
  min_resolved_events: 1
  min_companies: 1
  min_quarters: 1
  min_per_displayed_band: 1
  min_oos_windows: 2
  max_ece: 1
  max_ambiguous_censored_rate: 1
cohorts: {}
""",
        encoding="utf-8",
    )
    dataset = load_declared_calibration_dataset(
        _payload(ledger), declaration=_declaration()
    )
    snapshot = dataset.build_snapshot(
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc), policy_path=policy
    )
    assert len(snapshot.oos_brier_skill_windows) == 2
    assert all(score > 0 for score in snapshot.oos_brier_skill_windows)
    assert "OOS_BRIER_SKILL" not in snapshot.gate_failures
