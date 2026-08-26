from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from .records import MarketObservation
from .street import (
    StreetConsensus,
    StreetGapAnalysis,
    StreetGapDriver,
    StreetResearchReport,
    analyze_street_gap,
    summarize_street_reports,
)
from .valuation_execution import (
    GenericValuationResult,
    IntrinsicValuationScope,
)


@dataclass(frozen=True)
class ScenarioReferenceGap:
    scenario_id: str
    intrinsic_value_per_share: Decimal
    reference_value_per_share: Decimal
    gap_per_share: Decimal
    gap_pct_of_reference: Decimal


@dataclass(frozen=True)
class ReferenceGapEnvelope:
    reference_name: str
    reference_as_of: str
    currency: str
    scenario_gaps: tuple[ScenarioReferenceGap, ...]
    expected_gap: ScenarioReferenceGap | None
    comparison_hash: str

    def get(self, scenario_id: str) -> ScenarioReferenceGap:
        for item in self.scenario_gaps:
            if item.scenario_id == scenario_id:
                return item
        raise KeyError(scenario_id)


@dataclass(frozen=True)
class StreetComparisonBundle:
    consensus: StreetConsensus
    envelope: ReferenceGapEnvelope
    expected_gap_analysis: StreetGapAnalysis | None


@dataclass(frozen=True)
class MarketComparisonBundle:
    observation: MarketObservation
    envelope: ReferenceGapEnvelope


def _gap_point(scenario_id: str, intrinsic: Decimal, reference: Decimal) -> ScenarioReferenceGap:
    if intrinsic <= 0:
        raise ValueError(f"intrinsic value must be positive for {scenario_id}")
    if reference <= 0:
        raise ValueError("comparison reference must be positive")
    gap = intrinsic - reference
    return ScenarioReferenceGap(
        scenario_id=scenario_id,
        intrinsic_value_per_share=intrinsic,
        reference_value_per_share=reference,
        gap_per_share=gap,
        gap_pct_of_reference=gap / reference,
    )


def _require_full_company_intrinsic(valuation: GenericValuationResult) -> None:
    if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
        raise ValueError(
            "PARTIAL_INTRINSIC valued-segment subtotal cannot be compared with a whole-company market or Street reference"
        )


def _envelope(
    valuation: GenericValuationResult,
    *,
    reference_name: str,
    reference_as_of: str,
    reference_value: Decimal,
    currency: str,
) -> ReferenceGapEnvelope:
    _require_full_company_intrinsic(valuation)
    if currency != valuation.reporting_unit:
        raise ValueError(
            f"reference currency {currency} does not match intrinsic reporting unit {valuation.reporting_unit}"
        )
    scenario_gaps = tuple(
        _gap_point(item.scenario_id, item.value_per_share, reference_value)
        for item in valuation.scenarios
    )
    expected_gap = (
        _gap_point("Expected", valuation.expected_value_per_share, reference_value)
        if valuation.expected_value_per_share is not None
        else None
    )
    payload = "\n".join(
        [valuation.valuation_hash, reference_name, reference_as_of, currency, str(reference_value)]
        + [
            f"{item.scenario_id}|{item.intrinsic_value_per_share}|{item.gap_per_share}|{item.gap_pct_of_reference}"
            for item in scenario_gaps
        ]
        + [
            "expected=NA"
            if expected_gap is None
            else f"expected={expected_gap.intrinsic_value_per_share}|{expected_gap.gap_per_share}|{expected_gap.gap_pct_of_reference}"
        ]
    )
    return ReferenceGapEnvelope(
        reference_name=reference_name,
        reference_as_of=reference_as_of,
        currency=currency,
        scenario_gaps=scenario_gaps,
        expected_gap=expected_gap,
        comparison_hash=sha256(payload.encode("utf-8")).hexdigest(),
    )


def compare_generic_to_street(
    valuation: GenericValuationResult,
    reports: tuple[StreetResearchReport, ...],
    *,
    drivers: tuple[StreetGapDriver, ...] = (),
) -> StreetComparisonBundle:
    _require_full_company_intrinsic(valuation)
    consensus = summarize_street_reports(reports)
    envelope = _envelope(
        valuation,
        reference_name="Street mean target price",
        reference_as_of=consensus.latest_report_date,
        reference_value=Decimal(str(consensus.mean_target_price)),
        currency=consensus.target_price_currency,
    )
    expected_analysis = (
        analyze_street_gap(float(valuation.expected_value_per_share), reports, drivers)
        if valuation.expected_value_per_share is not None
        else None
    )
    return StreetComparisonBundle(consensus, envelope, expected_analysis)


def compare_generic_to_market(
    valuation: GenericValuationResult,
    observation: MarketObservation,
    *,
    currency: str,
) -> MarketComparisonBundle:
    _require_full_company_intrinsic(valuation)
    envelope = _envelope(
        valuation,
        reference_name="Current market price",
        reference_as_of=observation.as_of,
        reference_value=Decimal(str(observation.price)),
        currency=currency,
    )
    return MarketComparisonBundle(observation, envelope)