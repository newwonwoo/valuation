from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from statistics import fmean, median


class GapDriverCategory(str, Enum):
    OPERATING = "operating"
    FINANCING = "financing"
    VALUATION_POLICY = "valuation_policy"
    OPTION = "option"
    CAPITAL_STRUCTURE = "capital_structure"
    UNEXPLAINED = "unexplained"


class GapEvidenceQuality(str, Enum):
    PRIMARY_VALIDATED = "primary_validated"
    EXTERNAL_VALIDATED = "external_validated"
    SECONDARY_ONLY = "secondary_only"
    VALUATION_POLICY = "valuation_policy"
    UNEXPLAINED = "unexplained"


class GapQuality(str, Enum):
    PRIMARY_EVIDENCE_DRIVEN = "PRIMARY_EVIDENCE_DRIVEN"
    MIXED = "MIXED"
    VALUATION_POLICY_DRIVEN = "VALUATION_POLICY_DRIVEN"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class StreetEstimate:
    metric: str
    period: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        if not self.metric or not self.period or not self.unit:
            raise ValueError("street estimate requires metric, period and unit")
        if not isfinite(self.value):
            raise ValueError("street estimate value must be finite")


@dataclass(frozen=True)
class StreetResearchReport:
    broker: str
    analyst: str
    published_date: str
    target_price: float
    target_price_currency: str
    valuation_method: str
    base_year: str
    estimates: tuple[StreetEstimate, ...]
    source_ref: str
    aggregate_report_count: int | None = None
    aggregate_median_target_price: float | None = None
    aggregate_min_target_price: float | None = None
    aggregate_max_target_price: float | None = None

    def __post_init__(self) -> None:
        if not self.broker or not self.published_date or not self.source_ref:
            raise ValueError("street report requires broker, date and source_ref")
        date.fromisoformat(self.published_date[:10])
        if not isfinite(self.target_price) or self.target_price <= 0:
            raise ValueError("street target_price must be finite and positive")
        if not self.target_price_currency:
            raise ValueError("target_price_currency is required")
        if not self.valuation_method or not self.base_year:
            raise ValueError("valuation_method and base_year are required")
        aggregate_values = (
            self.aggregate_median_target_price,
            self.aggregate_min_target_price,
            self.aggregate_max_target_price,
        )
        if self.aggregate_report_count is None:
            if any(value is not None for value in aggregate_values):
                raise ValueError("aggregate Street range requires aggregate_report_count")
            return
        if self.aggregate_report_count < 1:
            raise ValueError("aggregate_report_count must be positive")
        if any(value is None for value in aggregate_values):
            raise ValueError("aggregate Street count requires median/min/max target prices")
        median_value, min_value, max_value = (float(value) for value in aggregate_values)
        if not all(isfinite(value) and value > 0 for value in (median_value, min_value, max_value)):
            raise ValueError("aggregate Street target-price statistics must be finite and positive")
        if not min_value <= median_value <= max_value:
            raise ValueError("aggregate Street min/median/max target prices are inconsistent")
        if not min_value <= self.target_price <= max_value:
            raise ValueError("aggregate Street mean target price must lie inside min/max")


@dataclass(frozen=True)
class StreetConsensus:
    report_count: int
    mean_target_price: float
    median_target_price: float
    min_target_price: float
    max_target_price: float
    latest_report_date: str
    target_price_currency: str


@dataclass(frozen=True)
class StreetGapDriver:
    key: str
    category: GapDriverCategory
    intrinsic_assumption: float | None
    street_assumption: float | None
    unit: str
    value_impact_per_share: float | None
    evidence_quality: GapEvidenceQuality
    evidence_ids: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        if not self.key or not self.unit or not self.rationale:
            raise ValueError("street gap driver requires key, unit and rationale")
        for value in (self.intrinsic_assumption, self.street_assumption, self.value_impact_per_share):
            if value is not None and not isfinite(value):
                raise ValueError("street gap driver numeric values must be finite")
        if self.evidence_quality is GapEvidenceQuality.PRIMARY_VALIDATED and not self.evidence_ids:
            raise ValueError("primary-validated street gap driver requires evidence_ids")
        if self.category is GapDriverCategory.VALUATION_POLICY and self.evidence_quality not in {
            GapEvidenceQuality.VALUATION_POLICY,
            GapEvidenceQuality.PRIMARY_VALIDATED,
        }:
            raise ValueError("valuation-policy driver must be labelled as valuation policy or primary validated")


@dataclass(frozen=True)
class StreetGapAnalysis:
    intrinsic_value_per_share: float
    consensus: StreetConsensus
    headline_gap_per_share: float
    headline_gap_pct_of_street: float
    explained_gap_per_share: float
    unexplained_gap_per_share: float
    gap_quality: GapQuality
    drivers: tuple[StreetGapDriver, ...]


def summarize_street_reports(reports: tuple[StreetResearchReport, ...]) -> StreetConsensus:
    if not reports:
        raise ValueError("at least one street report is required")
    currencies = {report.target_price_currency for report in reports}
    if len(currencies) != 1:
        raise ValueError("street target prices must use one currency before consensus")
    aggregate_reports = tuple(report for report in reports if report.aggregate_report_count is not None)
    if aggregate_reports:
        if len(reports) != 1 or len(aggregate_reports) != 1:
            raise ValueError("an aggregate Street consensus snapshot must be supplied as one standalone report")
        report = aggregate_reports[0]
        return StreetConsensus(
            report_count=int(report.aggregate_report_count or 0),
            mean_target_price=report.target_price,
            median_target_price=float(report.aggregate_median_target_price),
            min_target_price=float(report.aggregate_min_target_price),
            max_target_price=float(report.aggregate_max_target_price),
            latest_report_date=report.published_date[:10],
            target_price_currency=report.target_price_currency,
        )
    values = tuple(report.target_price for report in reports)
    latest = max(report.published_date[:10] for report in reports)
    return StreetConsensus(
        report_count=len(values),
        mean_target_price=fmean(values),
        median_target_price=median(values),
        min_target_price=min(values),
        max_target_price=max(values),
        latest_report_date=latest,
        target_price_currency=next(iter(currencies)),
    )


def analyze_street_gap(
    intrinsic_value_per_share: float,
    reports: tuple[StreetResearchReport, ...],
    drivers: tuple[StreetGapDriver, ...] = (),
) -> StreetGapAnalysis:
    if not isfinite(intrinsic_value_per_share) or intrinsic_value_per_share <= 0:
        raise ValueError("intrinsic value must be finite and positive")
    consensus = summarize_street_reports(reports)
    headline_gap = intrinsic_value_per_share - consensus.mean_target_price
    explained = sum(
        driver.value_impact_per_share
        for driver in drivers
        if driver.value_impact_per_share is not None
    )
    unexplained = headline_gap - explained
    quality = classify_gap_quality(drivers)
    return StreetGapAnalysis(
        intrinsic_value_per_share=intrinsic_value_per_share,
        consensus=consensus,
        headline_gap_per_share=headline_gap,
        headline_gap_pct_of_street=headline_gap / consensus.mean_target_price,
        explained_gap_per_share=explained,
        unexplained_gap_per_share=unexplained,
        gap_quality=quality,
        drivers=drivers,
    )


def classify_gap_quality(drivers: tuple[StreetGapDriver, ...]) -> GapQuality:
    impacts = [driver for driver in drivers if driver.value_impact_per_share not in (None, 0)]
    if not impacts:
        return GapQuality.UNRESOLVED
    total = sum(abs(driver.value_impact_per_share or 0.0) for driver in impacts)
    if total == 0:
        return GapQuality.UNRESOLVED
    primary_operating = sum(
        abs(driver.value_impact_per_share or 0.0)
        for driver in impacts
        if driver.category in {GapDriverCategory.OPERATING, GapDriverCategory.OPTION}
        and driver.evidence_quality is GapEvidenceQuality.PRIMARY_VALIDATED
    )
    valuation_policy = sum(
        abs(driver.value_impact_per_share or 0.0)
        for driver in impacts
        if driver.category is GapDriverCategory.VALUATION_POLICY
        or driver.evidence_quality is GapEvidenceQuality.VALUATION_POLICY
    )
    if primary_operating / total >= 0.6:
        return GapQuality.PRIMARY_EVIDENCE_DRIVEN
    if valuation_policy / total >= 0.5:
        return GapQuality.VALUATION_POLICY_DRIVEN
    return GapQuality.MIXED
