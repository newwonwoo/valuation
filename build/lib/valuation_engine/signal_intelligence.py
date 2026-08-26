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


class ProjectGate(str, Enum):
    """Canonical project-realization gates.

    These are independent gates, not a mandatory linear sequence. Different projects may
    secure land, financing, permits, contracts and grid access in different orders.
    """

    ANNOUNCEMENT = "announcement"
    LAND_CONTROL = "land_control"
    FINANCING = "financing"
    PERMIT_APPLICATION = "permit_application"
    PERMIT_APPROVAL = "permit_approval"
    OFFTAKE_CONTRACT = "offtake_contract"
    GRID_UTILITIES = "grid_utilities"
    CONSTRUCTION = "construction"
    COMMISSIONING = "commissioning"
    REVENUE = "revenue"


@dataclass(frozen=True)
class ProjectGateEvidence:
    gate: ProjectGate
    verified: bool
    evidence_ids: tuple[str, ...] = ()
    effective_at: str | None = None
    note: str = ""

    def validate(self) -> None:
        if self.verified and not self.evidence_ids:
            raise ValueError(f"verified project gate {self.gate.value} requires evidence")


@dataclass(frozen=True)
class ProjectGateSet:
    project_id: str
    required_gates: tuple[ProjectGate, ...]
    observations: tuple[ProjectGateEvidence, ...] = ()

    def validate(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.required_gates:
            raise ValueError("project gate set requires at least one required gate")
        if len(set(self.required_gates)) != len(self.required_gates):
            raise ValueError("required project gates must be unique")
        seen: set[ProjectGate] = set()
        required = set(self.required_gates)
        for observation in self.observations:
            observation.validate()
            if observation.gate not in required:
                raise ValueError(f"observation for non-required gate: {observation.gate.value}")
            if observation.gate in seen:
                raise ValueError(f"duplicate project gate observation: {observation.gate.value}")
            seen.add(observation.gate)

    @property
    def verified_gates(self) -> tuple[ProjectGate, ...]:
        self.validate()
        return tuple(item.gate for item in self.observations if item.verified)

    @property
    def unresolved_gates(self) -> tuple[ProjectGate, ...]:
        verified = set(self.verified_gates)
        return tuple(gate for gate in self.required_gates if gate not in verified)

    @property
    def realization_maturity(self) -> float:
        """Evidence-completion ratio only; never an execution probability."""
        return len(self.verified_gates) / len(self.required_gates)

    @property
    def revenue_verified(self) -> bool:
        return ProjectGate.REVENUE in set(self.verified_gates)


class ProjectState(str, Enum):
    """Legacy linear compatibility state; new logic should use ProjectGateSet."""

    ANNOUNCED = "announced"
    APPLIED = "applied"
    FUNDED = "funded"
    PERMITTED = "permitted"
    AWARDED_CONTRACTED = "awarded_contracted"
    UNDER_CONSTRUCTION = "under_construction"
    COMMISSIONED_DELIVERED = "commissioned_delivered"
    REVENUE = "revenue"


_LEGACY_STATE_GATES = {
    ProjectState.ANNOUNCED: (ProjectGate.ANNOUNCEMENT,),
    ProjectState.APPLIED: (ProjectGate.PERMIT_APPLICATION,),
    ProjectState.FUNDED: (ProjectGate.FINANCING,),
    ProjectState.PERMITTED: (ProjectGate.PERMIT_APPROVAL,),
    ProjectState.AWARDED_CONTRACTED: (ProjectGate.OFFTAKE_CONTRACT,),
    ProjectState.UNDER_CONSTRUCTION: (ProjectGate.CONSTRUCTION,),
    ProjectState.COMMISSIONED_DELIVERED: (ProjectGate.COMMISSIONING,),
    ProjectState.REVENUE: (ProjectGate.REVENUE,),
}


def legacy_state_gate_evidence(
    state: ProjectState,
    *,
    evidence_ids: tuple[str, ...],
) -> tuple[ProjectGateEvidence, ...]:
    """Translate a legacy state to only the gate(s) that state explicitly proves.

    It intentionally does not infer all earlier gates, because project gates are not globally
    ordered and a later operational event does not prove every financing/regulatory detail.
    """
    if not evidence_ids:
        raise ValueError("legacy project-state translation requires evidence")
    return tuple(ProjectGateEvidence(gate, True, evidence_ids) for gate in _LEGACY_STATE_GATES[state])


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
    """Legacy regression helper only; live project reasoning uses independent ProjectGateSet."""
    order = list(ProjectState)
    if order.index(new) < order.index(old):
        raise ValueError("legacy project realization state cannot move backward without an explicit revision event")
