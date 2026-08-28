from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from valuation_engine.calibration_fact_lake import (
    CalibrationFactError,
    CalibrationFactLake,
    MetricChangeEventRule,
    MetricChangeOperator,
    NormalizedCalibrationFact,
    calibration_fact_from_evidence,
    derive_metric_change_outcome,
)
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def evidence(
    *,
    evidence_id: str,
    metric: str,
    value,
    effective_date: str,
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="KRX:000660",
        metric=metric,
        value=value,
        unit="ratio" if metric == "operating_margin" else "KRW",
        source_layer=layer,
        effective_date=effective_date,
        observed_date=effective_date,
        source_name="OpenDART",
        source_ref=f"https://dart.example/{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="company",
    )


def fact(
    fact_id: str,
    *,
    value: str,
    effective: date,
    published: datetime,
    first_seen: datetime | None = None,
    revision_of: str | None = None,
) -> NormalizedCalibrationFact:
    return NormalizedCalibrationFact(
        fact_id=fact_id,
        target_id="KRX:000660",
        metric="revenue",
        value=Decimal(value),
        unit="KRW",
        effective_date=effective,
        published_at=published,
        first_seen_at=first_seen or published,
        evidence_id=f"E-{fact_id}",
        source_ref=f"https://dart.example/{fact_id}",
        revision_of=revision_of,
    )


def test_fact_lake_accepts_realized_evidence_only():
    filing = evidence(
        evidence_id="E1",
        metric="revenue",
        value=100,
        effective_date="2024-03-31",
    )
    converted = calibration_fact_from_evidence(
        filing,
        fact_id="F1",
        published_at=datetime(2024, 5, 15, tzinfo=timezone.utc),
        first_seen_at=datetime(2024, 5, 15, tzinfo=timezone.utc),
    )
    assert converted.value == Decimal("100")

    plan = evidence(
        evidence_id="PLAN",
        metric="revenue",
        value=120,
        effective_date="2024-03-31",
        layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
    )
    with pytest.raises(CalibrationFactError, match="realized/filing"):
        calibration_fact_from_evidence(
            plan,
            fact_id="PLAN-F",
            published_at=datetime(2024, 5, 15, tzinfo=timezone.utc),
            first_seen_at=datetime(2024, 5, 15, tzinfo=timezone.utc),
        )


def test_late_revision_does_not_rewrite_earlier_replay():
    lake = CalibrationFactLake()
    old = fact(
        "F1",
        value="100",
        effective=date(2023, 12, 31),
        published=datetime(2024, 3, 15, tzinfo=timezone.utc),
    )
    revised = fact(
        "F2",
        value="95",
        effective=date(2023, 12, 31),
        published=datetime(2024, 4, 1, tzinfo=timezone.utc),
        revision_of="F1",
    )
    lake.append(old)
    lake.append(revised)

    before = lake.exact_fact(
        target_id="KRX:000660",
        metric="revenue",
        effective_date=date(2023, 12, 31),
        cutoff=datetime(2024, 3, 20, tzinfo=timezone.utc),
    )
    after = lake.exact_fact(
        target_id="KRX:000660",
        metric="revenue",
        effective_date=date(2023, 12, 31),
        cutoff=datetime(2024, 4, 2, tzinfo=timezone.utc),
    )
    assert before.fact_id == "F1"
    assert after.fact_id == "F2"
    assert lake.snapshot_hash(
        cutoff=datetime(2024, 3, 20, tzinfo=timezone.utc)
    ) != lake.snapshot_hash(
        cutoff=datetime(2024, 4, 2, tzinfo=timezone.utc)
    )


def test_future_first_seen_fact_is_not_visible_to_past_cutoff():
    lake = CalibrationFactLake()
    lake.append(
        fact(
            "F1",
            value="100",
            effective=date(2023, 12, 31),
            published=datetime(2024, 3, 15, tzinfo=timezone.utc),
            first_seen=datetime(2024, 4, 10, tzinfo=timezone.utc),
        )
    )
    assert not lake.visible_facts(
        cutoff=datetime(2024, 4, 1, tzinfo=timezone.utc)
    )
    assert lake.visible_facts(
        cutoff=datetime(2024, 4, 11, tzinfo=timezone.utc)
    )


def test_metric_change_outcome_reuses_same_fact_lake():
    lake = CalibrationFactLake()
    lake.append(
        fact(
            "ORIGIN",
            value="100",
            effective=date(2023, 3, 31),
            published=datetime(2023, 5, 15, tzinfo=timezone.utc),
        )
    )
    lake.append(
        fact(
            "OUTCOME",
            value="80",
            effective=date(2024, 3, 31),
            published=datetime(2024, 5, 15, tzinfo=timezone.utc),
        )
    )
    rule = MetricChangeEventRule(
        event_class="revenue_growth_miss",
        horizon="12m",
        metric="revenue",
        operator=MetricChangeOperator.RELATIVE_CHANGE_AT_MOST,
        threshold=Decimal("-0.15"),
        derivation_version="revenue-change-v1",
    )
    result = derive_metric_change_outcome(
        lake,
        event_key="KRX:000660:revenue_growth_miss:2023Q1",
        target_id="KRX:000660",
        origin_effective_date=date(2023, 3, 31),
        outcome_effective_date=date(2024, 3, 31),
        cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc),
        rule=rule,
    )
    assert result.occurred
    assert result.measured_value == Decimal("-0.2")
    assert result.evidence_ids == ("E-ORIGIN", "E-OUTCOME")


def test_outcome_cannot_be_derived_before_result_fact_is_visible():
    lake = CalibrationFactLake()
    lake.append(
        fact(
            "ORIGIN",
            value="100",
            effective=date(2023, 3, 31),
            published=datetime(2023, 5, 15, tzinfo=timezone.utc),
        )
    )
    lake.append(
        fact(
            "OUTCOME",
            value="80",
            effective=date(2024, 3, 31),
            published=datetime(2024, 5, 15, tzinfo=timezone.utc),
        )
    )
    rule = MetricChangeEventRule(
        event_class="revenue_growth_miss",
        horizon="12m",
        metric="revenue",
        operator=MetricChangeOperator.RELATIVE_CHANGE_AT_MOST,
        threshold=Decimal("-0.15"),
        derivation_version="revenue-change-v1",
    )
    with pytest.raises(CalibrationFactError, match="exactly one visible fact"):
        derive_metric_change_outcome(
            lake,
            event_key="EVENT",
            target_id="KRX:000660",
            origin_effective_date=date(2023, 3, 31),
            outcome_effective_date=date(2024, 3, 31),
            cutoff=datetime(2024, 5, 1, tzinfo=timezone.utc),
            rule=rule,
        )
