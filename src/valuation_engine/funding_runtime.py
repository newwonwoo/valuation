from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .actual_units import Measure, measure_from_raw
from .ledger import EvidenceLedger
from .records import EvidenceRecord
from .wacc import CustomerAdvanceCreditEvidence


@dataclass(frozen=True)
class FundingSourceUseBinding:
    """Map observed Evidence metrics into one funding sources/uses assessment.

    The binding names observed money metrics only. It does not define a valuation assumption and
    it cannot infer that total customer advances are growth-related without an explicit metric.
    """

    need_metrics: tuple[str, ...]
    source_metrics: tuple[str, ...]
    reporting_unit: str
    segment: str | None = None
    require_same_effective_date: bool = True

    def validate(self) -> None:
        if not self.need_metrics or not self.source_metrics or not self.reporting_unit:
            raise ValueError("funding binding requires need metrics, source metrics and reporting unit")
        if len(self.need_metrics) != len(set(self.need_metrics)):
            raise ValueError("funding need metrics must be unique")
        if len(self.source_metrics) != len(set(self.source_metrics)):
            raise ValueError("funding source metrics must be unique")
        overlap = set(self.need_metrics).intersection(self.source_metrics)
        if overlap:
            raise ValueError(f"funding metric cannot be both source and use: {sorted(overlap)}")


@dataclass(frozen=True)
class FundingMetricObservation:
    metric: str
    amount: Decimal
    unit: str
    effective_date: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class FundingAssessment:
    target_id: str
    reporting_unit: str
    as_of: str
    funding_need: Decimal
    verified_funding_sources: Decimal
    funding_gap: Decimal
    funding_coverage_ratio: Decimal
    need_observations: tuple[FundingMetricObservation, ...]
    source_observations: tuple[FundingMetricObservation, ...]
    evidence_ids: tuple[str, ...]
    credit_improvement_candidate: bool
    credit_evidence_present: bool

    @property
    def fully_funded(self) -> bool:
        return self.funding_gap == 0


@dataclass(frozen=True)
class FundingAssessmentResult:
    assessment: FundingAssessment | None
    missing_metrics: tuple[str, ...] = ()
    blocking_findings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.assessment is not None and not self.missing_metrics and not self.blocking_findings


def _latest_records(
    ledger: EvidenceLedger,
    *,
    metric: str,
    segment: str | None,
) -> tuple[EvidenceRecord, ...]:
    candidates = tuple(
        item
        for item in ledger.active()
        if item.metric == metric and (segment is None or item.segment == segment)
    )
    if not candidates:
        return ()
    latest_date = max(item.effective_date for item in candidates)
    return tuple(item for item in candidates if item.effective_date == latest_date)


def _metric_observation(
    ledger: EvidenceLedger,
    *,
    metric: str,
    reporting_unit: str,
    segment: str | None,
) -> FundingMetricObservation | None:
    records = _latest_records(ledger, metric=metric, segment=segment)
    if not records:
        return None
    normalized: list[tuple[EvidenceRecord, Measure]] = []
    for record in records:
        measure = measure_from_raw(record.value, record.unit, record.effective_date).convert_to(reporting_unit)
        if measure.amount < 0:
            raise ValueError(f"funding metric {metric} cannot be negative")
        normalized.append((record, measure))
    amounts = {measure.amount for _, measure in normalized}
    if len(amounts) != 1:
        raise ValueError(
            f"unresolved same-date funding conflict for {metric}: "
            + ", ".join(record.id for record, _ in normalized)
        )
    amount = next(iter(amounts))
    return FundingMetricObservation(
        metric=metric,
        amount=amount,
        unit=reporting_unit,
        effective_date=records[0].effective_date,
        evidence_ids=tuple(sorted(record.id for record, _ in normalized)),
    )


def assess_funding_sources_and_uses(
    *,
    target_id: str,
    ledger: EvidenceLedger,
    binding: FundingSourceUseBinding,
    credit_evidence: CustomerAdvanceCreditEvidence | None = None,
) -> FundingAssessmentResult:
    """Deterministically compare verified funding sources with observed funding needs.

    This function does not change WACC. `credit_improvement_candidate=True` merely means a
    separately supplied CustomerAdvanceCreditEvidence gate passed all six structural credit tests.
    """
    if not target_id:
        raise ValueError("target_id is required")
    binding.validate()

    missing: list[str] = []
    needs: list[FundingMetricObservation] = []
    sources: list[FundingMetricObservation] = []
    findings: list[str] = []

    for metric in binding.need_metrics:
        try:
            observation = _metric_observation(
                ledger,
                metric=metric,
                reporting_unit=binding.reporting_unit,
                segment=binding.segment,
            )
        except ValueError as exc:
            findings.append(str(exc))
            continue
        if observation is None:
            missing.append(metric)
        else:
            needs.append(observation)

    for metric in binding.source_metrics:
        try:
            observation = _metric_observation(
                ledger,
                metric=metric,
                reporting_unit=binding.reporting_unit,
                segment=binding.segment,
            )
        except ValueError as exc:
            findings.append(str(exc))
            continue
        if observation is None:
            missing.append(metric)
        else:
            sources.append(observation)

    if missing or findings:
        return FundingAssessmentResult(
            None,
            tuple(dict.fromkeys(missing)),
            tuple(findings),
        )

    all_observations = tuple(needs + sources)
    effective_dates = {item.effective_date for item in all_observations}
    if binding.require_same_effective_date and len(effective_dates) != 1:
        return FundingAssessmentResult(
            None,
            (),
            ("funding sources/uses effective dates do not align: " + ", ".join(sorted(effective_dates)),),
        )

    funding_need = sum((item.amount for item in needs), Decimal("0"))
    verified_sources = sum((item.amount for item in sources), Decimal("0"))
    if funding_need <= 0:
        return FundingAssessmentResult(None, (), ("total funding need must be positive",))
    gap = max(funding_need - verified_sources, Decimal("0"))
    coverage = verified_sources / funding_need
    evidence_ids = tuple(
        sorted({evidence_id for item in all_observations for evidence_id in item.evidence_ids})
    )
    credit_candidate = bool(credit_evidence and credit_evidence.supports_wacc_reduction)
    assessment = FundingAssessment(
        target_id=target_id,
        reporting_unit=binding.reporting_unit,
        as_of=max(effective_dates),
        funding_need=funding_need,
        verified_funding_sources=verified_sources,
        funding_gap=gap,
        funding_coverage_ratio=coverage,
        need_observations=tuple(needs),
        source_observations=tuple(sources),
        evidence_ids=evidence_ids,
        credit_improvement_candidate=credit_candidate,
        credit_evidence_present=credit_evidence is not None,
    )
    return FundingAssessmentResult(assessment)
