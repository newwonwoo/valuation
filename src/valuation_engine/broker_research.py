from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .signal_intelligence import ProjectGate, ProjectGateEvidence


class BrokerAccessMode(str, Enum):
    PUBLIC_SUMMARY = "public_summary"
    PUBLIC_FILE = "public_file"
    PUBLIC_INDEX_RESTRICTED = "public_index_restricted"
    CLIENT_PORTAL = "client_portal"
    THIRD_PARTY_ENTITLED = "third_party_entitled"
    METADATA_ONLY = "metadata_only"


class BrokerReportType(str, Enum):
    INDUSTRY_PRIMER = "industry_primer"
    INDUSTRY_DEEP_DIVE = "industry_deep_dive"
    INDUSTRY_OUTLOOK = "industry_outlook"
    EARNINGS_PREVIEW = "earnings_preview"
    EARNINGS_REVIEW = "earnings_review"
    CHANNEL_CHECK = "channel_check"
    CONFERENCE_FIELD_TRIP = "conference_field_trip"
    QUANT_ALT_DATA = "quant_alt_data"
    THEMATIC = "thematic"
    INITIATION = "initiation"
    COMPANY_UPDATE = "company_update"
    VALUATION_CHANGE = "valuation_change"
    STRATEGY = "strategy"


class BrokerFieldClass(str, Enum):
    INDUSTRY_DEFINITION = "industry_definition"
    VALUE_CHAIN = "value_chain"
    KPI_DEFINITION = "kpi_definition"
    MECHANISM_CANDIDATE = "mechanism_candidate"
    LEADING_INDICATOR_CANDIDATE = "leading_indicator_candidate"
    INDUSTRY_FORECAST = "industry_forecast"
    UNDERLYING_DATA_REFERENCE = "underlying_data_reference"
    TARGET_COMPANY_FORECAST = "target_company_forecast"
    TARGET_PRICE = "target_price"
    RATING = "rating"
    TARGET_MULTIPLE = "target_multiple"
    CONSENSUS = "consensus"


@dataclass(frozen=True)
class BrokerSourceSpec:
    source_id: str
    broker_family: str
    access_mode: BrokerAccessMode
    public_raw_storage_allowed: bool
    url: str
    notes: str = ""

    def validate(self) -> None:
        if not self.source_id or not self.broker_family or not self.url:
            raise ValueError("source_id, broker_family and url are required")
        if self.access_mode in {BrokerAccessMode.CLIENT_PORTAL, BrokerAccessMode.THIRD_PARTY_ENTITLED} and self.public_raw_storage_allowed:
            raise ValueError("licensed/entitled broker research raw content must not be stored publicly")


@dataclass(frozen=True)
class BrokerClaim:
    claim_id: str
    source_id: str
    broker_family: str
    report_type: BrokerReportType
    field_class: BrokerFieldClass
    industry_node: str
    statement: str
    target_company_specific: bool = False
    underlying_data_families: tuple[str, ...] = ()
    report_date: str = ""


def pre_freeze_allowed(claim: BrokerClaim) -> bool:
    """Protect blind intrinsic valuation while still allowing industry-learning use."""
    if claim.target_company_specific:
        return False
    if claim.field_class in {
        BrokerFieldClass.TARGET_COMPANY_FORECAST,
        BrokerFieldClass.TARGET_PRICE,
        BrokerFieldClass.RATING,
        BrokerFieldClass.TARGET_MULTIPLE,
        BrokerFieldClass.CONSENSUS,
    }:
        return False
    return True


def raw_storage_allowed(source: BrokerSourceSpec) -> bool:
    source.validate()
    return source.public_raw_storage_allowed and source.access_mode in {
        BrokerAccessMode.PUBLIC_SUMMARY,
        BrokerAccessMode.PUBLIC_FILE,
    }


def independence_key(claim: BrokerClaim) -> tuple[str, ...]:
    """Use underlying datasets before broker brand to avoid Street echo double counting."""
    if claim.underlying_data_families:
        return tuple(sorted(set(claim.underlying_data_families)))
    return (f"BROKER:{claim.broker_family}",)


def canonical_rule_eligible(
    broker_claims: tuple[BrokerClaim, ...],
    non_broker_source_families: tuple[str, ...],
) -> bool:
    """Broker research can discover/corroborate, but cannot canonize an industry rule alone."""
    if not broker_claims or not non_broker_source_families:
        return False
    if any(not pre_freeze_allowed(c) for c in broker_claims):
        return False
    broker_keys = {k for claim in broker_claims for k in independence_key(claim)}
    total = broker_keys | {f"NONBROKER:{x}" for x in non_broker_source_families}
    return len(total) >= 2


class IndicatorRepresentativeness(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IndicatorRepresentativenessAssessment:
    indicator_id: str
    economic_metric: str
    coverage_share: float | None
    transaction_channel: str
    representativeness: IndicatorRepresentativeness
    corroborating_indicators: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.coverage_share is not None and not 0.0 <= self.coverage_share <= 1.0:
            raise ValueError("coverage_share must be within [0, 1]")
        if self.representativeness is IndicatorRepresentativeness.HIGH:
            if self.coverage_share is not None and self.coverage_share < 0.5:
                raise ValueError("high representativeness requires at least 50% measured coverage when coverage is known")


class ProjectRealizationStage(str, Enum):
    """Legacy broker adapter state; canonical project reasoning uses ProjectGateSet."""

    ANNOUNCED = "announced"
    FUNDED = "funded"
    LAND_ASSET_SECURED = "land_asset_secured"
    PERMITTED = "permitted"
    UTILITIES_SECURED = "utilities_secured"
    UNDER_CONSTRUCTION = "under_construction"
    COMMISSIONED_ENERGIZED = "commissioned_energized"
    REVENUE = "revenue"


_PROJECT_STAGE_ORDER = {
    ProjectRealizationStage.ANNOUNCED: 0,
    ProjectRealizationStage.FUNDED: 1,
    ProjectRealizationStage.LAND_ASSET_SECURED: 2,
    ProjectRealizationStage.PERMITTED: 3,
    ProjectRealizationStage.UTILITIES_SECURED: 4,
    ProjectRealizationStage.UNDER_CONSTRUCTION: 5,
    ProjectRealizationStage.COMMISSIONED_ENERGIZED: 6,
    ProjectRealizationStage.REVENUE: 7,
}

_BROKER_STAGE_TO_GATE = {
    ProjectRealizationStage.ANNOUNCED: ProjectGate.ANNOUNCEMENT,
    ProjectRealizationStage.FUNDED: ProjectGate.FINANCING,
    ProjectRealizationStage.LAND_ASSET_SECURED: ProjectGate.LAND_CONTROL,
    ProjectRealizationStage.PERMITTED: ProjectGate.PERMIT_APPROVAL,
    ProjectRealizationStage.UTILITIES_SECURED: ProjectGate.GRID_UTILITIES,
    ProjectRealizationStage.UNDER_CONSTRUCTION: ProjectGate.CONSTRUCTION,
    ProjectRealizationStage.COMMISSIONED_ENERGIZED: ProjectGate.COMMISSIONING,
    ProjectRealizationStage.REVENUE: ProjectGate.REVENUE,
}


@dataclass(frozen=True)
class ProjectRealizationRecord:
    project_id: str
    stage: ProjectRealizationStage
    evidence_ids: tuple[str, ...]
    expected_revenue_date: str | None = None
    execution_probability: float | None = None

    def validate(self) -> None:
        if not self.project_id or not self.evidence_ids:
            raise ValueError("project stage requires project_id and evidence")
        if self.execution_probability is not None and not 0.0 <= self.execution_probability <= 1.0:
            raise ValueError("execution_probability must be within [0, 1]")

    def to_gate_evidence(self) -> ProjectGateEvidence:
        """Convert broker state evidence to the canonical independent gate representation."""
        self.validate()
        return ProjectGateEvidence(
            gate=_BROKER_STAGE_TO_GATE[self.stage],
            verified=True,
            evidence_ids=self.evidence_ids,
            note="adapted from legacy broker project-realization stage",
        )


def project_stage_can_advance(previous: ProjectRealizationStage, new: ProjectRealizationStage) -> bool:
    """Legacy regression helper only; live reasoning must not assume this universal order."""
    return _PROJECT_STAGE_ORDER[new] >= _PROJECT_STAGE_ORDER[previous]


@dataclass(frozen=True)
class AnalystForecastObservation:
    broker_family: str
    analyst_id: str
    metric: str
    forecast_value: float
    actual_value: float
    forecast_date: str
    actual_date: str
    underlying_data_families: tuple[str, ...] = ()

    @property
    def signed_error(self) -> float:
        return self.forecast_value - self.actual_value

    @property
    def absolute_percentage_error(self) -> float | None:
        if self.actual_value == 0:
            return None
        return abs(self.signed_error / self.actual_value)


def forecast_calibration_score(observations: tuple[AnalystForecastObservation, ...]) -> dict[str, float | int | None]:
    """Descriptive calibration only. It changes Street weight, never evidence truth status."""
    if not observations:
        return {"n": 0, "mean_signed_error": None, "mean_ape": None}
    errors = [x.signed_error for x in observations]
    apes = [x.absolute_percentage_error for x in observations if x.absolute_percentage_error is not None]
    return {
        "n": len(observations),
        "mean_signed_error": sum(errors) / len(errors),
        "mean_ape": (sum(apes) / len(apes)) if apes else None,
    }


@dataclass(frozen=True)
class InvestorDebate:
    debate_id: str
    industry_node: str
    question: str
    supporting_claim_ids: tuple[str, ...] = ()
    opposing_claim_ids: tuple[str, ...] = ()
    resolution_evidence_needed: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.debate_id or not self.industry_node or not self.question:
            raise ValueError("debate_id, industry_node and question are required")
        if not self.resolution_evidence_needed:
            raise ValueError("investor debate requires a resolution evidence plan")


@dataclass(frozen=True)
class AlternativeDataCandidate:
    dataset_id: str
    provider_family: str
    industry_nodes: tuple[str, ...]
    methodology: str
    coverage: str
    update_lag: str
    license_posture: str
    known_biases: tuple[str, ...] = ()
    validation_status: str = "candidate"

    def validate(self) -> None:
        if not self.dataset_id or not self.provider_family or not self.industry_nodes:
            raise ValueError("alternative-data candidate requires identity and industry coverage")
        if not self.methodology or not self.coverage or not self.license_posture:
            raise ValueError("alternative-data candidate requires methodology, coverage and license posture")
