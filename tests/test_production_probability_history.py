from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

import pytest

from valuation_engine.probability_calibration import (
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
)
from valuation_engine.production_probability_history import (
    ProductionProbabilityHistoryError,
    append_production_forecast,
    append_production_outcome,
    export_production_ledger,
    initialize_production_history,
    load_production_history,
    main,
)


ISSUED = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
FORECAST_RECORDED = datetime(2026, 1, 10, 9, 1, tzinfo=timezone.utc)
OUTCOME_OBSERVED = datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)
OUTCOME_RECORDED = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)


def _history(tmp_path: Path) -> Path:
    return (tmp_path / "probability" / "history.jsonl").resolve()


def _append_forecast(path: Path, *, forecast_id: str = "F-001"):
    return append_production_forecast(
        path,
        forecast_id=forecast_id,
        event_key="EVT:COMPANY:FY2026:margin_pressure",
        hypothesis_id="H-MARGIN",
        company_id="000001",
        forecast_class="kr-listed-margin-pressure",
        horizon="12m",
        event_definition="FY2026 operating margin falls below 8 percent",
        issued_at=ISSUED,
        evaluation_deadline=date(2026, 12, 31),
        probability=Decimal("0.40"),
        displayed_band="30-50%",
        evidence_snapshot_hash="a" * 64,
        model_version="probability-v3",
        resolution_rule="resolved from the first annual filing after FY2026 close",
        resolution_source_policy="REALIZED_OR_FILING primary Evidence only",
        recorded_at=FORECAST_RECORDED,
    )


def test_initialize_append_resolve_and_export_round_trip(tmp_path):
    path = _history(tmp_path)
    initialized = initialize_production_history(path)
    assert initialized["status"] == "INITIALIZED"
    assert initialized["event_count"] == 0

    forecast_result = _append_forecast(path)
    assert forecast_result["status"] == "FORECAST_APPENDED"
    assert forecast_result["event_count"] == 1
    assert forecast_result["first_seen_at"] == FORECAST_RECORDED.isoformat()

    outcome_result = append_production_outcome(
        path,
        forecast_id="F-001",
        observed_at=OUTCOME_OBSERVED,
        outcome=ForecastOutcomeState.OCCURRED,
        outcome_evidence_ids=("EVIDENCE:DART:2026FY:MARGIN",),
        resolver_id="production-resolver",
        rationale="The first-seen FY2026 annual filing reports margin below the rule threshold.",
        recorded_at=OUTCOME_RECORDED,
    )
    assert outcome_result["status"] == "OUTCOME_APPENDED"
    assert outcome_result["event_count"] == 2
    assert outcome_result["outcome_state_counts"]["occurred"] == 1

    snapshot = load_production_history(path)
    assert len(snapshot.ledger.forecasts) == 1
    assert len(snapshot.ledger.outcomes) == 1
    assert snapshot.events[1].previous_hash == snapshot.events[0].event_hash

    output = (tmp_path / "export" / "ledger.json").resolve()
    exported = export_production_ledger(path, output)
    assert exported["status"] == "EXPORTED"
    restored = ProbabilityCalibrationLedger.from_payload(
        json.loads(output.read_text(encoding="utf-8"))
    )
    assert len(restored.forecasts) == 1
    assert len(restored.outcomes) == 1


def test_first_seen_is_writer_controlled_and_cannot_pollute_earlier_replay(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    snapshot = load_production_history(path)
    forecast = snapshot.ledger.forecasts[0]
    assert forecast.first_seen_at == FORECAST_RECORDED

    replay = snapshot.ledger.replay_as_of(
        datetime(2026, 1, 10, 9, 0, 30, tzinfo=timezone.utc)
    )
    assert replay.forecasts == ()


def test_duplicate_forecast_rejection_leaves_journal_byte_identical(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="duplicate forecast_id"):
        _append_forecast(path)
    assert path.read_bytes() == before


def test_binary_outcome_without_primary_evidence_is_not_appended(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="primary outcome Evidence IDs"):
        append_production_outcome(
            path,
            forecast_id="F-001",
            observed_at=OUTCOME_OBSERVED,
            outcome=ForecastOutcomeState.NOT_OCCURRED,
            outcome_evidence_ids=(),
            resolver_id="production-resolver",
            rationale="No qualifying event occurred by the deadline.",
            recorded_at=OUTCOME_RECORDED,
        )
    assert path.read_bytes() == before


def test_future_observation_cannot_be_first_seen_earlier(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    with pytest.raises(ValueError, match="first_seen_at cannot precede observed_at"):
        append_production_outcome(
            path,
            forecast_id="F-001",
            observed_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            outcome=ForecastOutcomeState.CENSORED,
            outcome_evidence_ids=(),
            resolver_id="production-resolver",
            rationale="The resolution window has not closed.",
            recorded_at=OUTCOME_RECORDED,
        )
    assert load_production_history(path).summary()["event_count"] == 1


def test_tampered_payload_fails_event_hash_validation(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["payload"]["probability"] = "0.99"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(
        ProductionProbabilityHistoryError,
        match="event hash mismatch",
    ):
        load_production_history(path)


def test_broken_previous_hash_fails_chain_validation(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    append_production_outcome(
        path,
        forecast_id="F-001",
        observed_at=OUTCOME_OBSERVED,
        outcome=ForecastOutcomeState.AMBIGUOUS,
        outcome_evidence_ids=(),
        resolver_id="production-resolver",
        rationale="Primary evidence is contradictory and cannot resolve the event.",
        recorded_at=OUTCOME_RECORDED,
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["previous_hash"] = "f" * 64
    base = {key: value for key, value in rows[1].items() if key != "event_hash"}
    rows[1]["event_hash"] = sha256(
        json.dumps(
            base,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    with pytest.raises(
        ProductionProbabilityHistoryError,
        match="hash chain break",
    ):
        load_production_history(path)


def test_relative_history_path_fails_closed():
    with pytest.raises(
        ProductionProbabilityHistoryError,
        match="must be absolute",
    ):
        initialize_production_history(Path("relative/history.jsonl"))


def test_export_cannot_overwrite_the_append_only_journal(tmp_path):
    path = _history(tmp_path)
    _append_forecast(path)
    with pytest.raises(
        ProductionProbabilityHistoryError,
        match="cannot overwrite",
    ):
        export_production_ledger(path, path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes required")
def test_history_and_lock_files_are_owner_private(tmp_path):
    path = _history(tmp_path)
    initialize_production_history(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_name(path.name + ".lock").stat().st_mode) == 0o600


def test_cli_init_validate_and_summary(tmp_path, capsys):
    path = _history(tmp_path)
    assert main(("init", "--history", str(path))) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["status"] == "INITIALIZED"

    _append_forecast(path)
    assert main(("validate", "--history", str(path))) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["status"] == "VALID"
    assert validated["forecast_revisions"] == 1
