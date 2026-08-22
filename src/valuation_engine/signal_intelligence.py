from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SignalClass(str, Enum):
    PROJECT_REALIZATION = "project_realization"
    PROCUREMENT_PIPELINE = "procurement_pipeline"
    REGULATORY_PROGRESS = "regulatory_progress"
    PHYSICAL_ACTIVITY = "physical_activity"
    TRADE_LOGISTICS = "trade_logistics"
    TECHNOLOGY_INNOVATION = "technology_innovation"
    LABOR_CAPACITY = "labor_capacity"
    CREDIT_FINANCING = "credit_financing"
    OWNERSHIP_BEHAVIOR = "ownership_behavior"
    MARKET_POSITIONING = "market_positioning"
    CLINICAL_REGULATORY = "clinical_regulatory"
    REMOTE_SENSING = "remote_sensing"
    SUPPLY_CHAIN_NETWORK = "supply_chain_network"
    CONSUMER_DEMAND = "consumer_demand"


class MarketDataRole(str, Enum):
    FINANCING_MARKET_REFERENCE = "financing_market_reference"
    POSITIONING_MARKET_SIGNAL = "positioning_market_signal"
    TARGET_EQUITY_MARKET_REFERENCE = "target_equity_market_reference"


class ProjectState(str, Enum):
    ANNOUNCED = "announced"
    APPLIED = "applied"
    FUNDED = "funded"
    PERMITTED = "permitted"
    AWARDED_CONTRACTED = "awarded_contracted"
    UNDER_CONSTRUCTION = "under_construction"
    COMMISSIONED_DELIVERED = "commissioned_delivered"
    REVENUE = "revenue"


@dataclass(frozen=True)
class SignalTimestamp:
    event_time: datetime | None
    effective_as_of: datetime | None
    published_at: datetime
    first_seen_at: datetime
    revised_at: datetime | None = None
    expected_reporting_lag_days: int | None = None

    def validate(self) -> None:
        if self.first_seen_at < self.published_at:
            raise ValueError("first_seen_at cannot precede published_at")
        if self.expected_reporting_lag_days is not None and self.expected_reporting_lag_days < 0:
            raise ValueError("expected_reporting_lag_days must be non-negative")


@dataclass(frozen=True)
class NegativeEvidenceContext:
    coverage_complete: bool
    reporting_mandatory_or_near_complete: bool
    expected_lag_elapsed: bool
    source_healthy: bool
    no_known_alternate_channel: bool

    def permits_no_event_inference(self) -> bool:
        return all((
            self.coverage_complete,
            self.reporting_mandatory_or_near_complete,
            self.expected_lag_elapsed,
            self.source_healthy,
            self.no_known_alternate_channel,
        ))


_PRE_FREEZE_FINANCING_STAGES = {"upstream_funding_scan", "wacc_validation", "evidence_to_assumption_bridge"}
_POST_FREEZE_STAGES = {"street_gap", "market_compare"}


def market_role_allowed(role: MarketDataRole, stage: str) -> bool:
    if role is MarketDataRole.FINANCING_MARKET_REFERENCE:
        return stage in _PRE_FREEZE_FINANCING_STAGES or stage in _POST_FREEZE_STAGES
    if role is MarketDataRole.POSITIONING_MARKET_SIGNAL:
        return stage == "monitoring" or stage in _POST_FREEZE_STAGES
    if role is MarketDataRole.TARGET_EQUITY_MARKET_REFERENCE:
        return stage in _POST_FREEZE_STAGES
    return False


def validate_project_transition(old: ProjectState, new: ProjectState) -> None:
    order = list(ProjectState)
    if order.index(new) < order.index(old):
        raise ValueError("project realization state cannot move backward without an explicit revision event")
