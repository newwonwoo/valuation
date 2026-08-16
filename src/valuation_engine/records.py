from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class EvidenceSourceLayer(str, Enum):
    REALIZED_OR_FILING = "realized_or_filing"
    COMPANY_OFFICIAL_PLAN = "company_official_plan"
    POLICY_PRIMARY_SOURCE = "policy_primary_source"
    EXTERNAL_REFERENCE = "external_reference"
    MARKET_COMPARISON = "market_comparison"


class EvidenceStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class CalibrationStatus(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"
    CALIBRATED = "CALIBRATED"


class AffectedVariable(str, Enum):
    PRICE = "price"
    QUANTITY = "quantity"
    UTILIZATION = "utilization"
    MIX = "mix"
    YIELD = "yield"
    MARGIN = "margin"
    FUNDING_GAP = "funding_gap"
    NET_DEBT = "net_debt"
    DISCOUNT_RATE = "discount_rate"
    MULTIPLE = "multiple"
    PROBABILITY = "probability"
    SEGMENT_VALUE = "segment_value"
    SHARE_COUNT = "share_count"


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    UNCHANGED = "unchanged"


class RunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    VALUATION_BLOCKED = "VALUATION_BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    target: str
    metric: str
    value: Any
    unit: str
    source_layer: EvidenceSourceLayer
    effective_date: str
    observed_date: str
    source_name: str
    source_ref: str
    source_grade: str
    confidence: float
    segment: str
    status: EvidenceStatus = EvidenceStatus.ACTIVE
    supersedes_id: str | None = None
    notes: str = ""
    critical: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.metric or not self.target:
            raise ValueError("evidence id, target and metric are required")
        if not self.unit:
            raise ValueError("evidence unit is required; use 'dimensionless' when applicable")
        if not 0 <= self.confidence <= 1:
            raise ValueError("evidence confidence must be between 0 and 1")
        _parse_date(self.effective_date, "effective_date")
        _parse_date(self.observed_date, "observed_date")
        if not self.source_name or not self.source_ref:
            raise ValueError("evidence source_name and source_ref are required")


@dataclass(frozen=True)
class HypothesisRecord:
    id: str
    statement: str
    causal_chain: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()
    probability: float = 0.5
    calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED
    kill_conditions: tuple[str, ...] = ()
    next_checks: tuple[str, ...] = ()
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.statement:
            raise ValueError("hypothesis id and statement are required")
        if len(self.causal_chain) < 3:
            raise ValueError("hypothesis causal_chain must include cause, economic variable and value variable")
        if not 0 <= self.probability <= 1:
            raise ValueError("hypothesis probability must be between 0 and 1")
        if not self.kill_conditions:
            raise ValueError("major hypothesis requires at least one kill condition")


@dataclass(frozen=True)
class BridgeRecord:
    id: str
    evidence_ids: tuple[str, ...]
    hypothesis_id: str
    affected_variable: AffectedVariable
    direction: Direction
    old_value: float
    new_value: float
    unit: str
    rationale: str
    confidence: float
    kill_condition: str
    verification_event: str
    economic_path_id: str
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.evidence_ids or not self.hypothesis_id:
            raise ValueError("bridge requires id, evidence_ids and hypothesis_id")
        if not self.unit or not self.rationale or not self.economic_path_id:
            raise ValueError("bridge requires unit, rationale and economic_path_id")
        if not self.kill_condition or not self.verification_event:
            raise ValueError("bridge requires kill_condition and verification_event")
        if not 0 <= self.confidence <= 1:
            raise ValueError("bridge confidence must be between 0 and 1")


@dataclass(frozen=True)
class AssumptionRecord:
    key: str
    scenario_id: str
    value: float
    unit: str
    bridge_id: str
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.unit or not self.bridge_id:
            raise ValueError("assumption requires key, unit and bridge_id")


@dataclass(frozen=True)
class CriticalIssue:
    id: str
    description: str
    blocking: bool = True
    resolved: bool = False
    requested_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditFinding:
    check: str
    passed: bool
    blocking: bool
    detail: str


@dataclass(frozen=True)
class AuditReport:
    findings: tuple[AuditFinding, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed or not item.blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {"pass": self.passed, "findings": [asdict(item) for item in self.findings]}


@dataclass(frozen=True)
class MarketObservation:
    price: float
    as_of: str
    source_ref: str

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("market price must be positive")
        _parse_date(self.as_of, "market as_of")


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    ticker: str
    company: str
    started_at: str
    finished_at: str
    status: RunStatus
    round_count: int
    audit_passed: bool
    parent_run_id: str | None = None
    blocked_reasons: tuple[str, ...] = ()
    artifacts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= self.round_count <= 3:
            raise ValueError("round_count must be between 1 and 3")
        if self.status is RunStatus.COMPLETED and not self.audit_passed:
            raise ValueError("completed run requires a passed audit")


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be ISO date") from exc


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
