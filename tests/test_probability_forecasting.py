from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.ledger import EvidenceLedger
from valuation_engine.probability_calibration import (
    ForecastOutcome,
    ForecastOutcomeState,
)
from valuation_engine.probability_forecasting import (
    ProbabilityForecastDeclaration,
    ProbabilityForecastHistoryStore,
    ScenarioLikelihoodInput,
    ScenarioLikelihoodSpec,
    build_probability_forecast_drafts,
    calculate_scenario_probability_assessment,
)
from valuation_engine.records import (
    CalibrationStatus,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def evidence(
    evidence_id: str,
    *,
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="COMPANY-1",
        metric=evidence_id,
        value=1,
        unit="dimensionless",
        source_layer=layer,
        effective_date="2026-08-26",
        observed_date="2026-08-26",
        source_name="primary source",
        source_ref="https://example.com/primary",
        source_grade="A",
        confidence=1.0,
        segment="segment-1",
    )


def hypothesis(probability: float = 0.70) -> HypothesisRecord:
    return HypothesisRecord(
        id="H:CAPACITY",
        statement="capacity enters commercial production by the declared deadline",
        causal_chain=("official project", "commercial ramp", "cash flow"),
        supporting_evidence_ids=("E:CAPACITY",),
        probability=probability,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        kill_conditions=("project is cancelled",),
        next_checks=("next filing",),
    )


def declaration() -> ProbabilityForecastDeclaration:
    return ProbabilityForecastDeclaration(
        hypothesis_id="H:CAPACITY",
        event_key="COMPANY-1:CAPACITY:2027",
        forecast_class="capacity_ramp",
        horizon="18m",
        event_definition="capacity is commercially operating by 2027-12-31",
        evaluation_deadline=date(2027, 12, 31),
        model_version="underwrite/v1",
        resolution_rule="primary filing confirms commercial production",
        resolution_source_policy="primary company filings only",
    )


def test_relative_scores_produce_visible_but_unweighted_scenario_probabilities():
    ledger = EvidenceLedger(
        (evidence("E:DOWN"), evidence("E:CORE"), evidence("E:BULL"))
    )
    spec = ScenarioLikelihoodSpec(
        forecast_class="intrinsic_scenario_path",
        horizon="5y",
        as_of_date="2026-08-26",
        method_version="analyst_relative_score/v1",
        inputs=(
            ScenarioLikelihoodInput("Down", Decimal("3"), "down case", ("E:DOWN",)),
            ScenarioLikelihoodInput("Core", Decimal("5"), "core case", ("E:CORE",)),
            ScenarioLikelihoodInput("Bull", Decimal("2"), "bull case", ("E:BULL",)),
        ),
    )

    result = calculate_scenario_probability_assessment(
        spec,
        scenario_ids=("Down", "Core", "Bull"),
        ledger=ledger,
    )

    assert result.status is CalibrationStatus.UNCALIBRATED
    assert not result.numeric_weighting_allowed
    assert tuple(row.probability for row in result.rows) == (
        Decimal("0.3"),
        Decimal("0.5"),
        Decimal("0.2"),
    )
    assert tuple(row.displayed_probability for row in result.rows) == (
        Decimal("0.30"),
        Decimal("0.50"),
        Decimal("0.20"),
    )
    assert sum(
        (row.displayed_probability for row in result.rows), Decimal("0")
    ) == Decimal("1")


def test_market_price_cannot_enter_scenario_likelihood():
    ledger = EvidenceLedger(
        (evidence("E:MARKET", layer=EvidenceSourceLayer.MARKET_COMPARISON),)
    )
    spec = ScenarioLikelihoodSpec(
        forecast_class="scenario",
        horizon="1y",
        as_of_date="2026-08-26",
        method_version="analyst_relative_score/v1",
        inputs=(
            ScenarioLikelihoodInput("Core", Decimal("1"), "market anchored", ("E:MARKET",)),
        ),
    )

    with pytest.raises(ValueError, match="target-market Evidence"):
        calculate_scenario_probability_assessment(
            spec,
            scenario_ids=("Core",),
            ledger=ledger,
        )


def test_production_forecast_history_is_append_only_and_resolvable(tmp_path):
    evidence_ledger = EvidenceLedger((evidence("E:CAPACITY"),))
    drafts = build_probability_forecast_drafts(
        (declaration(),),
        hypotheses=(hypothesis(),),
        company_id="COMPANY-1",
        evidence_snapshot_hash="a" * 64,
        ledger=evidence_ledger,
    )
    store = ProbabilityForecastHistoryStore(tmp_path)

    first = store.save_forecast_run(
        ticker="000001",
        run_id="RUN-1",
        drafts=drafts,
        recorded_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    second = store.save_forecast_run(
        ticker="000001",
        run_id="RUN-2",
        drafts=drafts,
        recorded_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )

    ledger = store.load_ledger("000001")
    assert store.forecast_run_count("000001") == 2
    assert len(ledger.forecasts) == 2
    assert ledger.forecasts[1].supersedes_id == first.forecast_ids[0]
    assert second.forecast_ids[0] == "RUN-2:H:CAPACITY"
    with pytest.raises(FileExistsError, match="immutable"):
        store.save_forecast_run(
            ticker="000001",
            run_id="RUN-2",
            drafts=drafts,
        )

    outcome = ForecastOutcome(
        forecast_id=second.forecast_ids[0],
        observed_at=datetime(2027, 12, 31, tzinfo=timezone.utc),
        outcome=ForecastOutcomeState.OCCURRED,
        outcome_evidence_ids=("E:OUTCOME:FILING",),
        resolver_id="PRIMARY_FILING_RESOLVER",
        rationale="official filing confirms commercial production",
        first_seen_at=datetime(2028, 1, 2, tzinfo=timezone.utc),
    )
    outcome_evidence = EvidenceLedger((evidence("E:OUTCOME:FILING"),))
    outcome_path = store.append_outcome(
        ticker="000001",
        outcome=outcome,
        evidence_ledger=outcome_evidence,
    )
    assert outcome_path.endswith(".json")
    assert "https://example.com/primary" in Path(outcome_path).read_text(
        encoding="utf-8"
    )
    reloaded = store.load_ledger("000001")
    assert reloaded.outcome_for(second.forecast_ids[0]) == outcome
    with pytest.raises(ValueError, match="immutable once recorded"):
        store.append_outcome(
            ticker="000001",
            outcome=outcome,
            evidence_ledger=outcome_evidence,
        )


def test_production_outcome_requires_first_seen_boundary(tmp_path):
    store = ProbabilityForecastHistoryStore(tmp_path)
    evidence_ledger = EvidenceLedger((evidence("E:CAPACITY"),))
    drafts = build_probability_forecast_drafts(
        (declaration(),),
        hypotheses=(hypothesis(),),
        company_id="COMPANY-1",
        evidence_snapshot_hash="b" * 64,
        ledger=evidence_ledger,
    )
    ref = store.save_forecast_run(
        ticker="000001",
        run_id="RUN-1",
        drafts=drafts,
        recorded_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    outcome = ForecastOutcome(
        forecast_id=ref.forecast_ids[0],
        observed_at=datetime(2027, 12, 31, tzinfo=timezone.utc),
        outcome=ForecastOutcomeState.NOT_OCCURRED,
        outcome_evidence_ids=("E:OUTCOME:FILING",),
        resolver_id="PRIMARY_FILING_RESOLVER",
        rationale="deadline passed without disclosed commercial production",
    )

    with pytest.raises(ValueError, match="first_seen_at"):
        store.append_outcome(
            ticker="000001",
            outcome=outcome,
            evidence_ledger=EvidenceLedger((evidence("E:OUTCOME:FILING"),)),
        )


def test_production_outcome_rejects_non_primary_evidence(tmp_path):
    store = ProbabilityForecastHistoryStore(tmp_path)
    drafts = build_probability_forecast_drafts(
        (declaration(),),
        hypotheses=(hypothesis(),),
        company_id="COMPANY-1",
        evidence_snapshot_hash="c" * 64,
        ledger=EvidenceLedger((evidence("E:CAPACITY"),)),
    )
    ref = store.save_forecast_run(
        ticker="000001",
        run_id="RUN-1",
        drafts=drafts,
        recorded_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    outcome = ForecastOutcome(
        forecast_id=ref.forecast_ids[0],
        observed_at=datetime(2027, 12, 31, tzinfo=timezone.utc),
        outcome=ForecastOutcomeState.OCCURRED,
        outcome_evidence_ids=("E:OUTCOME:ANALYST",),
        resolver_id="PRIMARY_FILING_RESOLVER",
        rationale="analyst assertion is not a valid resolution source",
        first_seen_at=datetime(2028, 1, 2, tzinfo=timezone.utc),
    )
    analyst_evidence = EvidenceLedger(
        (
            evidence(
                "E:OUTCOME:ANALYST",
                layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
            ),
        )
    )

    with pytest.raises(ValueError, match="realized/filing or policy primary"):
        store.append_outcome(
            ticker="000001",
            outcome=outcome,
            evidence_ledger=analyst_evidence,
        )
