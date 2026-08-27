from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .broker_research import (
    BrokerClaim,
    BrokerFieldClass,
    BrokerReportType,
)
from .broker_runtime import (
    BrokerResearchBatch,
    BrokerResearchObservation,
)
from .capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentInput,
    CapacityProjectBinding,
    CapacitySegmentCommitmentInput,
)
from .capacity_consumption import (
    CapacityBridgeBinding,
    CapacityBridgeConsumptionContract,
    CapacityBridgeRole,
)
from .collection_plan import CollectorCapability
from .context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
)
from .control_plane import ExecutionMode
from .dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .funding import ClaimStage, FundingLadder, FundingLayer, FundingLink
from .funding_adapter import FundedDemandState, FundingScanResult
from .industry_dna import EconomicArchetype, IndustryDNAProfile
from .live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    CompanyResolutionRequest,
    IndustryKnowledgeSnapshot,
    LiveFreshnessAssessment,
    ResolvedCompanyIdentity,
    SegmentDescriptor,
)
from .live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
)
from .llm_staff import (
    BridgeDraft,
    BridgeProposalBundle,
    IntelligenceProposal,
    RedTeamProposal,
)
from .orchestrator import OrchestratorContext
from .per import EconomicAssumptionFingerprint
from .per_adapters import LivePERInputs, PERApplicability
from .records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
    MarketObservation,
)
from .risk import BetaLevelName
from .runtime_resources import runtime_registry_path
from .risk_adapters import (
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
)
from .scanner_runtime import ScannerFinding, ScannerFindingStatus
from .scenario_binding import ScenarioBindingSpec
from .signal_intelligence import ProjectGate, ProjectGateEvidence, ProjectGateSet
from .source_watch import WatchFinding, WatchStatus
from .street import StreetResearchReport
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
)


_DEFAULT_SNAPSHOT_FILENAME = "sanil_live_snapshot.yaml"
_DEFAULT_MARKET_SNAPSHOT_FILENAME = "sanil_market_snapshot.yaml"
_MIRAE_2Q26_REPORT_URL = (
    "https://securities.miraeasset.com/bbs/board/message/view.do"
    "?categoryId=1800&messageId=2341906"
)
_MIRAE_POWER_SOLUTION_REPORT_URL = (
    "https://securities.miraeasset.com/bbs/board/message/list.do"
    "?categoryId=1800&searchStartYear=2026&searchStartMonth=07&searchStartDay=16"
    "&searchEndYear=2026&searchEndMonth=07&searchEndDay=16"
)
_IBK_2Q26_REPORT_URL = "https://www.yna.co.kr/view/AKR20260810017900008"
_SHINHAN_2Q26_REPORT_URL = "https://www.yna.co.kr/amp/view/AKR20260811028700008"

TARGET_ID = "KR:DART:00366438"
TICKER = "062040"
SEGMENT_ID = "power_transformers"
CAPACITY_PROJECT_ID = "SANIL_SECOND_FACTORY_RAMP"
CAPACITY_PATH_ROOT = f"capacity_project:{CAPACITY_PROJECT_ID}"
UHV_CAPACITY_PROJECT_ID = "SANIL_UHV_PROPERTY_ACQUISITION_20260826"
UHV_CAPACITY_PATH_ROOT = f"capacity_project:{UHV_CAPACITY_PROJECT_ID}"
SCENARIOS = ("Down", "Core", "Bull")
FORECAST_YEARS = 5

MANDATORY_SCANNERS = (
    "BACKLOG_QUALITY",
    "CANCELLATION_TERMS",
    "CUSTOMER_ADVANCE_FUNDING",
    "CAPACITY_RAMP",
    "QUALIFICATION",
    "UTILIZATION",
    "CAPEX_EXECUTION",
)

MODULE_METRICS = (
    "orders",
    "backlog",
    "revenue_recognition",
    "cancellation_terms",
    "contract_liabilities",
    "lead_time",
    "book_to_bill",
    "backlog_conversion",
    "cancellation_rate",
    "nameplate_capacity",
    "effective_capacity",
    "utilization",
    "yield",
    "asp",
    "mix",
    "unit_cost",
    "expansion_capex",
    "expansion_land_control",
    "expansion_capacity_committed",
    "expansion_site_area",
    "expansion_capex_committed",
    "expansion_ramp_date",
    "expansion_equipment_commitment",
    "expansion_baseline_inclusion",
    "expansion_cancelled",
    "no_active_capacity_expansion",
)


@dataclass(frozen=True)
class SanilSnapshot:
    payload: Mapping[str, Any]
    path: Path

    @property
    def cutoff(self) -> str:
        return str(self.payload["cutoff"])

    @property
    def company(self) -> Mapping[str, Any]:
        return self.payload["company"]

    @property
    def facts(self) -> Mapping[str, Any]:
        return self.payload["facts"]

    @property
    def sources(self) -> Mapping[str, Mapping[str, Any]]:
        return self.payload["sources"]

    @property
    def scenarios(self) -> Mapping[str, Mapping[str, Any]]:
        return self.payload["scenarios"]

    @property
    def risk(self) -> Mapping[str, Any]:
        return self.payload["risk"]

    @property
    def capacity_project(self) -> Mapping[str, Any]:
        return self.payload["capacity_project"]

    @property
    def uhv_capacity_project(self) -> Mapping[str, Any]:
        return self.payload["uhv_capacity_project"]

    def validate(self) -> None:
        if int(self.payload.get("version", 0)) != 1:
            raise ValueError("Sanil snapshot version must be 1")
        if "market" in self.payload:
            raise ValueError(
                "pre-Freeze Sanil snapshot cannot contain target market price"
            )
        if self.company.get("target_id") != TARGET_ID or self.company.get("ticker") != TICKER:
            raise ValueError("Sanil snapshot company identity drifted")
        if tuple(self.scenarios) != SCENARIOS:
            raise ValueError("Sanil scenarios must be ordered Down→Core→Bull")
        for name, row in self.scenarios.items():
            fcff = tuple(row.get("fcff_krw_billion", ()))
            if len(fcff) != FORECAST_YEARS or any(float(value) <= 0 for value in fcff):
                raise ValueError(f"{name} requires five positive FCFF values")
            growth = float(row["terminal_growth"])
            roic = float(row["terminal_roic"])
            if not 0 <= growth < roic:
                raise ValueError(f"{name} terminal growth/ROIC is invalid")
            uhv_fcff = tuple(row.get("uhv_incremental_fcff_krw_billion", ()))
            if len(uhv_fcff) != FORECAST_YEARS or any(
                float(value) < 0 for value in uhv_fcff
            ):
                raise ValueError(
                    f"{name} requires five non-negative UHV incremental FCFF values"
                )
            if float(row.get("uhv_property_capex_krw_billion", 0)) <= 0:
                raise ValueError(f"{name} requires positive UHV property CAPEX")
            if float(row.get("uhv_ramp_years", 0)) <= 0:
                raise ValueError(f"{name} requires positive UHV ramp duration")
        if self.uhv_capacity_project.get("project_id") != UHV_CAPACITY_PROJECT_ID:
            raise ValueError("Sanil UHV capacity project identity drifted")
        for source in self.sources.values():
            document_hash = str(source.get("document_hash", ""))
            if len(document_hash) != 64 or any(ch not in "0123456789abcdef" for ch in document_hash):
                raise ValueError("Sanil source document_hash must be lowercase sha256")
            source_ref = str(source.get("source_ref", ""))
            if not source_ref.startswith("https://"):
                raise ValueError("Sanil source_ref must be public HTTPS")
        weights = float(self.risk["target_equity_weight"]) + float(
            self.risk["target_debt_weight"]
        )
        if abs(weights - 1.0) > 1e-12:
            raise ValueError("Sanil target capital weights must sum to one")


def load_sanil_snapshot(path: str | Path | None = None) -> SanilSnapshot:
    target = (
        Path(path)
        if path is not None
        else runtime_registry_path(_DEFAULT_SNAPSHOT_FILENAME)
    )
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Sanil snapshot root must be a mapping")
    snapshot = SanilSnapshot(payload, target)
    snapshot.validate()
    return snapshot


@dataclass(frozen=True)
class SanilMarketSnapshot:
    target_id: str
    ticker: str
    price: float
    currency: str
    as_of: str
    source_ref: str
    path: Path

    def validate(self) -> None:
        if self.target_id != TARGET_ID or self.ticker != TICKER:
            raise ValueError("Sanil market snapshot identity drifted")
        if not self.price > 0 or self.currency != "KRW":
            raise ValueError("Sanil market snapshot price/currency is invalid")
        if not self.as_of or not self.source_ref.startswith("https://"):
            raise ValueError("Sanil market snapshot requires date and public source")


def load_sanil_market_snapshot(
    path: str | Path | None = None,
) -> SanilMarketSnapshot:
    target = (
        Path(path)
        if path is not None
        else runtime_registry_path(_DEFAULT_MARKET_SNAPSHOT_FILENAME)
    )
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or int(payload.get("version", 0)) != 1:
        raise ValueError("Sanil market snapshot root/version is invalid")
    snapshot = SanilMarketSnapshot(
        target_id=str(payload["target_id"]),
        ticker=str(payload["ticker"]),
        price=float(payload["price"]),
        currency=str(payload["currency"]),
        as_of=str(payload["as_of"]),
        source_ref=str(payload["source_ref"]),
        path=target,
    )
    snapshot.validate()
    return snapshot


def _source(snapshot: SanilSnapshot, key: str) -> Mapping[str, Any]:
    return snapshot.sources[key]


def _evidence_id(metric: str) -> str:
    return f"E:SANIL:{metric}"


def _uhv_evidence_id(role: str) -> str:
    return f"E:SANIL:UHV:{role}"


def _record(
    snapshot: SanilSnapshot,
    *,
    metric: str,
    value: Any,
    unit: str,
    source_key: str,
    source_layer: EvidenceSourceLayer,
    effective_date: str,
    confidence: float = 1.0,
    notes: str = "",
    evidence_id: str | None = None,
) -> EvidenceRecord:
    source = _source(snapshot, source_key)
    return EvidenceRecord(
        id=(evidence_id or _evidence_id(metric)),
        target=TARGET_ID,
        metric=metric,
        value=value,
        unit=unit,
        source_layer=source_layer,
        effective_date=effective_date,
        observed_date=snapshot.cutoff,
        source_name=str(source["document_id"]),
        source_ref=str(source["source_ref"]),
        source_grade=(
            "A"
            if source_layer
            in {
                EvidenceSourceLayer.REALIZED_OR_FILING,
                EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
            }
            else (
                "B"
                if source_layer is EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN
                else "C"
            )
        ),
        confidence=confidence,
        segment=SEGMENT_ID,
        notes=notes,
    )


def _official_records(snapshot: SanilSnapshot) -> tuple[EvidenceRecord, ...]:
    f = snapshot.facts
    annual = "annual_report"
    q2 = "q2_ir"
    rows = [
        _record(snapshot, metric="revenue", value=f["revenue_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="operating_profit", value=f["operating_profit_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="net_income", value=f["net_income_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="cash", value=f["cash_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="debt", value=f["short_term_debt_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="orders", value=f["new_orders_2025_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="backlog", value=f["backlog_q2_2026_krw_billion"], unit="KRW_billion", source_key=q2, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2026-06-30"),
        _record(snapshot, metric="revenue_recognition", value="delivery", unit="dimensionless", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="cancellation_terms", value="contract_specific", unit="dimensionless", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.75),
        _record(snapshot, metric="contract_liabilities", value=0, unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.60, notes="No separate normalized contract-liability figure was used in the frozen underwrite."),
        _record(snapshot, metric="lead_time", value=f["lead_time_years"], unit="years", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="book_to_bill", value=float(f["new_orders_2025_krw_billion"]) / float(f["revenue_2025_krw_billion"]), unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="backlog_conversion", value=float(f["revenue_2025_krw_billion"]) / float(f["backlog_2025_krw_billion"]), unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="cancellation_rate", value=0, unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.55, notes="No separately disclosed normalized cancellation rate; zero is not used in valuation math."),
        _record(snapshot, metric="nameplate_capacity", value=f["nameplate_capacity_2025_count"], unit="count", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="effective_capacity", value=f["effective_capacity_2025_count"], unit="count", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="utilization", value=f["utilization_2025_ratio"], unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31"),
        _record(snapshot, metric="yield", value=1.0, unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.50, notes="No separate yield KPI disclosed; placeholder is coverage-only and excluded from DCF math."),
        _record(snapshot, metric="asp", value=float(f["revenue_2025_krw_billion"]) * 1000 / float(f["nameplate_capacity_2025_count"]), unit="KRW_million", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.65, notes="Revenue per nameplate unit; product-mix normalized ASP is not claimed."),
        _record(snapshot, metric="mix", value=0.64, unit="ratio", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.90, notes="Renewable/specialty transformer share of revenue."),
        _record(snapshot, metric="unit_cost", value=(float(f["revenue_2025_krw_billion"]) - float(f["operating_profit_2025_krw_billion"])) * 1000 / float(f["nameplate_capacity_2025_count"]), unit="KRW_million", source_key=annual, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2025-12-31", confidence=0.60, notes="Operating-cost proxy per nameplate unit; not a disclosed manufacturing unit cost."),
        _record(snapshot, metric="expansion_capex", value=f["expansion_capex_committed_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2025-12-31"),
        _record(snapshot, metric="expansion_land_control", value=True, unit="dimensionless", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["second_factory_start"]), notes="Second factory site control evidenced by company establishment disclosure."),
        _record(snapshot, metric="expansion_capacity_committed", value=True, unit="dimensionless", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2025-12-31", notes="Land control and committed investment establish an incremental capacity program; the undisclosed exact capacity is bounded in the Core underwrite."),
        _record(snapshot, metric="expansion_site_area", value=f["second_factory_site_pyeong"], unit="pyeong", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2025-12-31"),
        _record(snapshot, metric="expansion_capex_committed", value=f["expansion_capex_committed_krw_billion"], unit="KRW_billion", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2025-12-31"),
        _record(snapshot, metric="expansion_ramp_date", value=str(snapshot.capacity_project["ramp_date"]), unit="dimensionless", source_key=q2, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2026-06-30", confidence=0.75),
        _record(snapshot, metric="expansion_equipment_commitment", value=True, unit="dimensionless", source_key=annual, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2025-12-31", confidence=0.80),
        _record(snapshot, metric="expansion_baseline_inclusion", value=str(snapshot.capacity_project["baseline_inclusion"]), unit="dimensionless", source_key=q2, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2026-06-30", confidence=0.75, notes=str(snapshot.capacity_project["rationale"])),
        _record(snapshot, metric="expansion_cancelled", value=False, unit="dimensionless", source_key=q2, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2026-06-30"),
        _record(snapshot, metric="no_active_capacity_expansion", value=False, unit="dimensionless", source_key=q2, source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date="2026-06-30"),
        _record(snapshot, metric="expansion_land_control", value=True, unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes="Signed official property-acquisition contract establishes LAND_CONTROL for the separate UHV project.", evidence_id=_uhv_evidence_id("land_control")),
        _record(snapshot, metric="expansion_capex_committed", value=f["uhv_property_amount_krw_billion"], unit="KRW_billion", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes="Full disclosed property acquisition consideration; exact production capacity is not disclosed.", evidence_id=_uhv_evidence_id("capex_committed")),
        _record(snapshot, metric="expansion_ramp_date", value=str(f["uhv_property_closing_date"]), unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), confidence=0.80, notes="Closing/registration date is the earliest asset-control boundary, not a claimed production start.", evidence_id=_uhv_evidence_id("ramp_boundary")),
        _record(snapshot, metric="expansion_baseline_inclusion", value="not_in_baseline", unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes=str(snapshot.uhv_capacity_project["rationale"]), evidence_id=_uhv_evidence_id("baseline_inclusion")),
        _record(snapshot, metric="uhv_property_contract_amount", value=f["uhv_property_amount_krw_billion"], unit="KRW_billion", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("contract_amount")),
        _record(snapshot, metric="uhv_property_asset_ratio", value=f["uhv_property_asset_ratio"], unit="ratio", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("asset_ratio")),
        _record(snapshot, metric="uhv_property_self_funded", value=f["uhv_property_self_funded"], unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("self_funded")),
        _record(snapshot, metric="revenue_h1_2026", value=f["revenue_h1_2026_krw_billion"], unit="KRW_billion", source_key=q2, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2026-06-30"),
        _record(snapshot, metric="operating_profit_h1_2026", value=f["operating_profit_h1_2026_krw_billion"], unit="KRW_billion", source_key=q2, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2026-06-30"),
        _record(snapshot, metric="net_income_h1_2026", value=f["net_income_h1_2026_krw_billion"], unit="KRW_billion", source_key=q2, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2026-06-30"),
    ]
    return tuple(rows)


def _underwriting_records(snapshot: SanilSnapshot) -> tuple[EvidenceRecord, ...]:
    source = "underwriting"
    rows: list[EvidenceRecord] = []
    for scenario, inputs in snapshot.scenarios.items():
        for year, value in enumerate(inputs["fcff_krw_billion"], start=1):
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{scenario.lower()}_fcff_year_{year}",
                    value=value,
                    unit="KRW_billion",
                    source_key=source,
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=snapshot.cutoff,
                    confidence=(0.60 if scenario != "Core" else 0.70),
                    notes="PRISM bounded underwriting input derived from official facts; not company guidance.",
                )
            )
        for year, value in enumerate(
            inputs["uhv_incremental_fcff_krw_billion"], start=1
        ):
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{scenario.lower()}_uhv_fcff_year_{year}",
                    value=value,
                    unit="KRW_billion",
                    source_key=source,
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=snapshot.cutoff,
                    confidence=(0.45 if scenario != "Core" else 0.55),
                    notes=(
                        "Bounded incremental UHV-property FCFF cohort; official filing "
                        "establishes land control and purpose, not exact capacity or earnings."
                    ),
                )
            )
        rows.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_uhv_property_capex",
                value=inputs["uhv_property_capex_krw_billion"],
                unit="KRW_billion",
                source_key=source,
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                effective_date=snapshot.cutoff,
                confidence=0.90,
                notes=(
                    "DCF cash-outflow input equals the full disclosed property "
                    "consideration and is deducted separately from incremental FCFF."
                ),
            )
        )
        rows.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_uhv_ramp_years",
                value=inputs["uhv_ramp_years"],
                unit="years",
                source_key=source,
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                effective_date=snapshot.cutoff,
                confidence=(0.45 if scenario != "Core" else 0.55),
                notes=(
                    "Bounded duration from property closing to a stabilized UHV capacity "
                    "contribution; not company guidance."
                ),
            )
        )
        for key in ("terminal_growth", "terminal_roic"):
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{scenario.lower()}_{key}",
                    value=inputs[key],
                    unit="ratio",
                    source_key=source,
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=snapshot.cutoff,
                    confidence=(0.60 if scenario != "Core" else 0.70),
                    notes="PRISM bounded underwriting input; not company guidance.",
                )
            )
        rows.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_expansion_capex",
                value=inputs["expansion_capex_krw_billion"],
                unit="KRW_billion",
                source_key=source,
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                effective_date=snapshot.cutoff,
                confidence=(0.65 if scenario != "Core" else 0.75),
                notes=(
                    "Scenario cash-outflow input anchored to committed company CAPEX; "
                    "the DCF deducts it in the explicit forecast."
                ),
            )
        )
        rows.extend(
            (
                _record(snapshot, metric=f"model_{scenario.lower()}_ownership", value=1.0, unit="ratio", source_key=source, source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING, effective_date=snapshot.cutoff, notes="Mechanical ownership input."),
                _record(snapshot, metric=f"model_{scenario.lower()}_ev_adjustment", value=float(snapshot.facts["cash_2025_krw_billion"]) - float(snapshot.facts["short_term_debt_2025_krw_billion"]), unit="KRW_billion", source_key=source, source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING, effective_date=snapshot.cutoff, notes="Cash less disclosed short-term debt; conservative EV-to-equity bridge."),
                _record(snapshot, metric=f"model_{scenario.lower()}_diluted_shares", value=snapshot.facts["diluted_shares"], unit="shares", source_key=source, source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING, effective_date=snapshot.cutoff, notes="Issued-share base used as the frozen diluted-share input."),
            )
        )
    for level in BetaLevelName:
        rows.append(
            _record(
                snapshot,
                metric=f"beta_selection_{level.value}",
                value=1,
                unit="count",
                source_key="risk_snapshot",
                source_layer=EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
                effective_date=snapshot.cutoff,
                confidence=0.65,
                notes="Evidence ID binds the disclosed L1→L4 selection rationale; Beta observations are frozen in the risk snapshot.",
            )
        )
    return tuple(rows)


def _all_records(snapshot: SanilSnapshot) -> tuple[EvidenceRecord, ...]:
    records = _official_records(snapshot) + _underwriting_records(snapshot)
    ids = tuple(item.id for item in records)
    if len(ids) != len(set(ids)):
        raise ValueError("Sanil evidence IDs must be unique")
    return records


def _identity(snapshot: SanilSnapshot) -> ResolvedCompanyIdentity:
    annual = _source(snapshot, "annual_report")
    return ResolvedCompanyIdentity(
        target_id=TARGET_ID,
        legal_name=str(snapshot.company["legal_name"]),
        ticker=TICKER,
        jurisdiction="KR",
        external_ids=(("opendart_corp_code", str(snapshot.company["corp_code"])), ("krx_stock_code", TICKER)),
        source_refs=(str(annual["source_ref"]),),
    )


def _industry_snapshot(snapshot: SanilSnapshot) -> IndustryKnowledgeSnapshot:
    annual = _source(snapshot, "annual_report")
    q2 = _source(snapshot, "q2_ir")
    lineages = (
        AuthoritativeEvidenceLineage(
            evidence_id="E:SANIL:INDUSTRY",
            target_id=TARGET_ID,
            source_id=str(annual["source_id"]),
            observed_date=snapshot.cutoff,
            content_hash=str(annual["document_hash"]),
            event_date="2025-12-31",
            effective_date="2025-12-31",
            published_at=str(annual["published_at"]),
            first_seen_at=str(annual["published_at"]),
            revision_id="annual-2025",
            revision_at=str(annual["published_at"]),
        ),
        AuthoritativeEvidenceLineage(
            evidence_id="E:SANIL:SEGMENT",
            target_id=TARGET_ID,
            source_id=str(q2["source_id"]),
            observed_date=snapshot.cutoff,
            content_hash=str(q2["document_hash"]),
            event_date="2026-06-30",
            effective_date="2026-06-30",
            published_at=str(q2["published_at"]),
            first_seen_at=str(q2["published_at"]),
            revision_id="q2-2026",
            revision_at=str(q2["published_at"]),
        ),
    )
    return IndustryKnowledgeSnapshot.build(
        as_of=snapshot.cutoff,
        source_ids=(str(annual["source_id"]), str(q2["source_id"])),
        document_ids=(str(annual["document_id"]), str(q2["document_id"])),
        evidence_ids=("E:SANIL:INDUSTRY", "E:SANIL:SEGMENT"),
        content_hashes=(str(annual["document_hash"]), str(q2["document_hash"])),
        evidence_lineage=lineages,
    )


def _primary_collector(snapshot: SanilSnapshot):
    records = _all_records(snapshot)
    fingerprint = sha256(
        "|".join(str(source["document_hash"]) for source in snapshot.sources.values()).encode("utf-8")
    ).hexdigest()

    records_by_metric: dict[str, list[EvidenceRecord]] = {}
    for item in records:
        records_by_metric.setdefault(item.metric, []).append(item)

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        unknown = tuple(
            metric
            for metric in request.required_metrics
            if metric not in records_by_metric
        )
        if unknown:
            raise ValueError(
                "Sanil collector received unsupported requested metrics: "
                + ", ".join(unknown)
            )
        selected = tuple(
            item
            for metric in dict.fromkeys(request.required_metrics)
            for item in records_by_metric[metric]
        )
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at=snapshot.cutoff,
            records=selected,
            source_fingerprint=fingerprint,
            document_ids=tuple(
                str(item["document_id"]) for item in snapshot.sources.values()
            ),
        )

    return collect


def _scanner_runner(context) -> ScannerFinding:
    ledger = context.ledger
    scanner_id = context.scanner_id

    def value(metric: str):
        return ledger.get(_evidence_id(metric)).value

    if scanner_id == "BACKLOG_QUALITY":
        book_to_bill = float(value("book_to_bill"))
        backlog = float(value("backlog"))
        status = (
            ScannerFindingStatus.PASS
            if backlog > 0 and book_to_bill >= 1.0
            else ScannerFindingStatus.WARNING
        )
        return ScannerFinding(
            scanner_id=scanner_id,
            status=status,
            summary=(
                f"backlog={backlog:.1f} KRWbn and book-to-bill={book_to_bill:.2f}; "
                "conversion remains the operating hinge"
            ),
            evidence_ids=(
                _evidence_id("orders"),
                _evidence_id("backlog"),
                _evidence_id("book_to_bill"),
                _evidence_id("backlog_conversion"),
            ),
            verification_requests=("next filing backlog conversion and ageing",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "CANCELLATION_TERMS":
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                "contract-specific cancellation terms are identified, but no normalized "
                "company cancellation-rate series is disclosed"
            ),
            evidence_ids=(
                _evidence_id("cancellation_terms"),
                _evidence_id("cancellation_rate"),
            ),
            verification_requests=("obtain order cancellation and backlog ageing disclosure",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "CUSTOMER_ADVANCE_FUNDING":
        liabilities = float(value("contract_liabilities"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                f"normalized contract-liability evidence is {liabilities:.1f} KRWbn in the "
                "frozen pack; backlog is not treated as automatic WACC relief"
            ),
            evidence_ids=(
                _evidence_id("contract_liabilities"),
                _evidence_id("backlog"),
            ),
            verification_requests=("reconcile customer advances and contract liabilities",),
            economic_path_ids=("funding:backlog_to_buyer_cash_flow",),
        )

    if scanner_id == "CAPACITY_RAMP":
        active = bool(value("expansion_land_control")) and bool(
            value("expansion_capacity_committed")
        ) and not bool(value("expansion_cancelled"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.PASS
                if active
                else ScannerFindingStatus.WARNING
            ),
            summary=(
                "land control, committed expansion and a dated ramp are present"
                if active
                else "capacity-ramp commitment is incomplete or cancelled"
            ),
            evidence_ids=(
                _evidence_id("expansion_land_control"),
                _evidence_id("expansion_capacity_committed"),
                _evidence_id("expansion_ramp_date"),
                _evidence_id("expansion_cancelled"),
            ),
            verification_requests=("next official factory-ramp milestone",),
            economic_path_ids=(CAPACITY_PATH_ROOT,),
            final_output_refs=("capacity_commitment_assessment",),
        )

    if scanner_id == "QUALIFICATION":
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                "orders and backlog evidence buyer acceptance, but customer-by-customer "
                "qualification status is not separately disclosed"
            ),
            evidence_ids=(_evidence_id("orders"), _evidence_id("backlog")),
            verification_requests=("customer qualification and concentration update",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "UTILIZATION":
        utilization = float(value("utilization"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.WARNING
                if utilization >= 0.85
                else ScannerFindingStatus.PASS
            ),
            summary=(
                f"reported utilization is {utilization:.1%}; production capacity, not demand, "
                "is the near-term conversion bottleneck"
            ),
            evidence_ids=(
                _evidence_id("utilization"),
                _evidence_id("effective_capacity"),
            ),
            verification_requests=("effective capacity and utilization after ramp",),
            economic_path_ids=(CAPACITY_PATH_ROOT,),
        )

    if scanner_id == "CAPEX_EXECUTION":
        capex = float(value("expansion_capex_committed"))
        equipment = bool(value("expansion_equipment_commitment"))
        passed = capex > 0 and equipment
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.PASS
                if passed
                else ScannerFindingStatus.WARNING
            ),
            summary=(
                f"committed expansion CAPEX is {capex:.1f} KRWbn and equipment commitment "
                f"is {'confirmed' if equipment else 'unconfirmed'}"
            ),
            evidence_ids=(
                _evidence_id("expansion_capex_committed"),
                _evidence_id("expansion_equipment_commitment"),
                _evidence_id("expansion_ramp_date"),
            ),
            verification_requests=("CAPEX spend-to-date and commissioning evidence",),
            economic_path_ids=(f"{CAPACITY_PATH_ROOT}:capex",),
        )

    raise ValueError(f"unsupported Sanil scanner: {scanner_id}")


def _funding_scanner(context) -> FundingScanResult:
    evidence_id = _evidence_id("backlog")
    ladder = FundingLadder(
        (
            FundingLink(
                FundingLayer.PRODUCT_OR_PROJECT,
                FundingLayer.BUYER_CASH_FLOW,
                "reported backlog evidences buyer-backed demand, but does not by itself lower WACC",
                ClaimStage.CONFIRMED_FACT,
                0.85,
                (evidence_id,),
            ),
        )
    )
    return FundingScanResult(
        state=FundedDemandState.FUNDED,
        summary="Backlog and orders support funded demand; financing transmission is kept separate from intrinsic risk inputs.",
        ladder=ladder,
        evidence_ids=(evidence_id,),
        economic_path_ids=("funding:backlog_to_buyer_cash_flow",),
    )


def _hypothesis(
    hypothesis_id: str,
    statement: str,
    evidence_ids: tuple[str, ...],
    *,
    kill: str,
) -> HypothesisRecord:
    return HypothesisRecord(
        id=hypothesis_id,
        statement=statement,
        causal_chain=("source-backed operating evidence", "compiled cash-flow path", "intrinsic value"),
        supporting_evidence_ids=evidence_ids,
        kill_conditions=(kill,),
        next_checks=("next company filing and capacity-ramp disclosure",),
    )


def _intelligence_officer(context) -> IntelligenceProposal:
    broker_context = context.broker_research_context
    if broker_context is None:
        raise ValueError(
            "Sanil LIVE_PRIMARY requires the pre-freeze Broker Research context"
        )
    hypotheses = tuple(
        _hypothesis(
            f"H:SANIL:{scenario}",
            f"{scenario} FCFF path is a bounded underwrite anchored to official 2025 and 1H26 performance",
            tuple(_evidence_id(f"model_{scenario.lower()}_fcff_year_{year}") for year in range(1, FORECAST_YEARS + 1)),
            kill="orders, backlog conversion, margin or cash conversion breaks the bounded path",
        )
        for scenario in SCENARIOS
    ) + (
        _hypothesis(
            "H:SANIL:CAPACITY",
            "land-controlled incremental capacity must enter Core together with CAPEX and ramp timing",
            (_evidence_id("expansion_land_control"), _evidence_id("expansion_site_area"), _evidence_id("expansion_capex_committed"), _evidence_id("expansion_ramp_date")),
            kill="official disclosure shows the program is cancelled or already fully embedded in baseline",
        ),
        _hypothesis(
            "H:SANIL:UHV_CAPACITY",
            "the signed UHV property contract must enter Core as a separate bounded capacity cohort with its full disclosed cash outflow",
            (
                _uhv_evidence_id("land_control"),
                _uhv_evidence_id("capex_committed"),
                _uhv_evidence_id("ramp_boundary"),
                _uhv_evidence_id("baseline_inclusion"),
            ),
            kill="the acquisition is cancelled, fails to close or is proven fully embedded in the prior baseline",
        ),
        _hypothesis(
            "H:SANIL:CAPITAL",
            "net cash and a low long-run debt weight support the EV-to-equity bridge without lowering operating-risk Beta",
            (_evidence_id("cash"), _evidence_id("debt")),
            kill="new leverage or expansion funding materially changes the capital structure",
        ),
    )
    linkage = ContextStrengthLinkage(
        id="CSL:SANIL:POWER_BOTTLENECK_CAPACITY",
        external_change=(
            "Grid replacement, renewable interconnection and data-center power demand "
            "are increasing the scarcity of qualified transformer delivery slots."
        ),
        emergent_need=(
            "Buyers need proven manufacturers with customer qualification, backlog "
            "visibility and physically controllable expansion capacity."
        ),
        company_strength=(
            "Sanil already has export customer access, a high-value specialty-transformer "
            "mix, an 88.9% utilized production base, reported backlog and a controlled "
            "second-factory site with committed CAPEX and a separate signed UHV "
            "property-acquisition contract."
        ),
        linkage_thesis=(
            "The external power-equipment bottleneck specifically revalues Sanil's "
            "existing customer relationships and pre-invested site because those assets "
            "can convert scarce delivery slots into backlog conversion and FCFF."
        ),
        market_blind_spot=(
            "A generic small-transformer framing can separate current earnings from the "
            "option value of land-controlled capacity and overlook that the site, customer "
            "access and production know-how already exist."
        ),
        value_capture_path=(
            "land control and committed CAPEX → equipment/ramp execution → effective "
            "capacity → backlog conversion → revenue, margin and free cash flow"
        ),
        causal_chain=(
            "power-infrastructure demand and transformer-slot scarcity rise",
            "qualified delivery capacity becomes the binding buyer constraint",
            "Sanil's existing customer access, operating base and controlled site absorb the need",
            "capacity, CAPEX and ramp are consumed together in the Core scenario",
            "incremental shipments convert backlog into revenue and FCFF",
        ),
        supporting_evidence_ids=(
            _evidence_id("orders"),
            _evidence_id("backlog"),
            _evidence_id("utilization"),
            _evidence_id("expansion_land_control"),
            _evidence_id("expansion_site_area"),
            _evidence_id("expansion_capex_committed"),
            _uhv_evidence_id("land_control"),
            _uhv_evidence_id("capex_committed"),
        ),
        hypothesis_ids=(
            "H:SANIL:CAPACITY",
            "H:SANIL:UHV_CAPACITY",
            "H:SANIL:Core",
        ),
        recognition_triggers=(
            "official second-factory equipment or production ramp disclosure",
            "effective-capacity growth with backlog conversion",
            "high-value product mix and margin retention after ramp",
        ),
        kill_conditions=(
            "the company cancels the program or confirms it is fully included in the frozen baseline",
            "backlog or orders decline before capacity converts to shipments",
            "ramp costs and margin normalization offset the added production ceiling",
        ),
        next_checks=(
            "next quarterly filing for factory ramp, CAPEX and utilization",
            "orders-to-revenue conversion and customer concentration",
            "cash conversion after expansion spending",
        ),
        confidence=0.78,
    )
    return IntelligenceProposal(
        hypotheses=hypotheses,
        requested_evidence=broker_context.verification_requests,
        rationale=(
            "Broker Research factual leads were converted to primary-source verification "
            "and target forecasts/targets were quarantined before intrinsic valuation. "
            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "
            "the declared land-controlled second-factory project must be classified "
            "by the typed Capacity Gate and, when confirmed incremental, consumed "
            "as one Core capacity, CAPEX and ramp path."
        ),
        context_strength_linkage_decision=ContextStrengthLinkageDecision(
            linkages=(linkage,),
        ),
    )


def _red_team_officer(context, hypotheses) -> RedTeamProposal:
    return RedTeamProposal(
        issues=(),
        counter_thesis=(
            "Current margins and transformer scarcity may normalize faster than backlog converts; "
            "the report therefore keeps probabilities uncalibrated and exposes Down/Core/Bull separately."
        ),
        requested_evidence=("future capacity disclosure", "customer concentration update", "cash-flow conversion"),
    )


def _bridge(
    *,
    bridge_id: str,
    evidence_ids: tuple[str, ...],
    hypothesis_id: str,
    affected_variable: AffectedVariable,
    direction: Direction,
    old_value: float,
    new_value: float,
    unit: str,
    economic_path_id: str,
    rationale: str,
) -> BridgeRecord:
    return BridgeRecord(
        id=bridge_id,
        evidence_ids=evidence_ids,
        hypothesis_id=hypothesis_id,
        affected_variable=affected_variable,
        direction=direction,
        old_value=old_value,
        new_value=new_value,
        unit=unit,
        rationale=rationale,
        confidence=0.70,
        kill_condition="source revision or next filing invalidates the input",
        verification_event="next audited/quarterly filing",
        economic_path_id=economic_path_id,
    )


def _bridge_analyst(context, hypotheses, red_team) -> BridgeProposalBundle:
    drafts: list[BridgeDraft] = []
    for scenario in SCENARIOS:
        hypothesis_id = f"H:SANIL:{scenario}"
        for year in range(1, FORECAST_YEARS + 1):
            metric = f"model_{scenario.lower()}_fcff_year_{year}"
            value = float(context.ledger.get(_evidence_id(metric)).value)
            bridge_id = f"B:SANIL:{scenario}:fcff_year_{year}"
            economic_path = f"sanil:{scenario.lower()}:fcff_year_{year}"
            evidence_ids = (_evidence_id(metric),)
            direction = Direction.UNCHANGED
            old_value = value
            if scenario == "Core" and year == 1:
                bridge_id = "B:SANIL:CAPACITY"
                economic_path = f"{CAPACITY_PATH_ROOT}:capacity"
                evidence_ids = (_evidence_id("expansion_land_control"), _evidence_id("expansion_site_area"), _evidence_id(metric))
                direction = Direction.UP
                old_value = 0.0
            drafts.append(
                BridgeDraft(
                    assumption_key=f"fcff_year_{year}",
                    scenario_id=scenario,
                    bridge=_bridge(
                        bridge_id=bridge_id,
                        evidence_ids=evidence_ids,
                        hypothesis_id=("H:SANIL:CAPACITY" if scenario == "Core" and year <= 2 else hypothesis_id),
                        affected_variable=AffectedVariable.MARGIN,
                        direction=direction,
                        old_value=old_value,
                        new_value=value,
                        unit="KRW_billion",
                        economic_path_id=economic_path,
                        rationale="bounded FCFF input compiled from the frozen Sanil underwrite",
                    ),
                    canonical_unit="KRW_billion",
                    transform_id="identity_observation",
                    input_evidence_ids=(_evidence_id(metric),),
                    min_value="0",
                )
            )

        capex_metric = f"model_{scenario.lower()}_expansion_capex"
        capex_value = float(context.ledger.get(_evidence_id(capex_metric)).value)
        capex_bridge_id = f"B:SANIL:{scenario}:expansion_capex"
        capex_path = f"sanil:{scenario.lower()}:expansion_capex"
        capex_evidence_ids = (_evidence_id(capex_metric),)
        capex_hypothesis = hypothesis_id
        if scenario == "Core":
            capex_bridge_id = "B:SANIL:CAPEX"
            capex_path = f"{CAPACITY_PATH_ROOT}:capex"
            capex_evidence_ids = (
                _evidence_id("expansion_capex_committed"),
                _evidence_id(capex_metric),
            )
            capex_hypothesis = "H:SANIL:CAPACITY"
        drafts.append(
            BridgeDraft(
                assumption_key="expansion_capex",
                scenario_id=scenario,
                bridge=_bridge(
                    bridge_id=capex_bridge_id,
                    evidence_ids=capex_evidence_ids,
                    hypothesis_id=capex_hypothesis,
                    affected_variable=AffectedVariable.QUANTITY,
                    direction=Direction.UP,
                    old_value=0.0,
                    new_value=capex_value,
                    unit="KRW_billion",
                    economic_path_id=capex_path,
                    rationale=(
                        "committed expansion CAPEX is a separate explicit cash outflow, "
                        "not a label attached to an FCFF assumption"
                    ),
                ),
                canonical_unit="KRW_billion",
                transform_id="identity_observation",
                input_evidence_ids=(_evidence_id(capex_metric),),
                min_value="0",
            )
        )

        for year in range(1, FORECAST_YEARS + 1):
            metric = f"model_{scenario.lower()}_uhv_fcff_year_{year}"
            value = float(context.ledger.get(_evidence_id(metric)).value)
            bridge_id = f"B:SANIL:{scenario}:uhv_fcff_year_{year}"
            path = f"sanil:{scenario.lower()}:uhv_fcff_year_{year}"
            evidence_ids = (_evidence_id(metric),)
            hypothesis = f"H:SANIL:{scenario}"
            direction = Direction.UP if value > 0 else Direction.UNCHANGED
            if scenario == "Core" and year == FORECAST_YEARS:
                bridge_id = "B:SANIL:UHV:CAPACITY"
                path = f"{UHV_CAPACITY_PATH_ROOT}:capacity"
                evidence_ids = (
                    _uhv_evidence_id("land_control"),
                    _uhv_evidence_id("capex_committed"),
                    _evidence_id(metric),
                )
                hypothesis = "H:SANIL:UHV_CAPACITY"
            drafts.append(
                BridgeDraft(
                    assumption_key=f"uhv_fcff_year_{year}",
                    scenario_id=scenario,
                    bridge=_bridge(
                        bridge_id=bridge_id,
                        evidence_ids=evidence_ids,
                        hypothesis_id=hypothesis,
                        affected_variable=AffectedVariable.QUANTITY,
                        direction=direction,
                        old_value=0.0,
                        new_value=value,
                        unit="KRW_billion",
                        economic_path_id=path,
                        rationale=(
                            "bounded incremental FCFF cohort for the separately "
                            "land-controlled UHV property project"
                        ),
                    ),
                    canonical_unit="KRW_billion",
                    transform_id="identity_observation",
                    input_evidence_ids=(_evidence_id(metric),),
                    min_value="0",
                )
            )

        uhv_ramp_metric = f"model_{scenario.lower()}_uhv_ramp_years"
        uhv_ramp_value = float(
            context.ledger.get(_evidence_id(uhv_ramp_metric)).value
        )
        uhv_ramp_bridge_id = f"B:SANIL:{scenario}:uhv_ramp_years"
        uhv_ramp_path = f"sanil:{scenario.lower()}:uhv_ramp_years"
        uhv_ramp_evidence_ids = (_evidence_id(uhv_ramp_metric),)
        uhv_ramp_hypothesis = f"H:SANIL:{scenario}"
        if scenario == "Core":
            uhv_ramp_bridge_id = "B:SANIL:UHV:RAMP"
            uhv_ramp_path = f"{UHV_CAPACITY_PATH_ROOT}:ramp"
            uhv_ramp_evidence_ids = (
                _uhv_evidence_id("ramp_boundary"),
                _evidence_id(uhv_ramp_metric),
            )
            uhv_ramp_hypothesis = "H:SANIL:UHV_CAPACITY"
        drafts.append(
            BridgeDraft(
                assumption_key="uhv_ramp_years",
                scenario_id=scenario,
                bridge=_bridge(
                    bridge_id=uhv_ramp_bridge_id,
                    evidence_ids=uhv_ramp_evidence_ids,
                    hypothesis_id=uhv_ramp_hypothesis,
                    affected_variable=AffectedVariable.QUANTITY,
                    direction=Direction.UP,
                    old_value=0.0,
                    new_value=uhv_ramp_value,
                    unit="years",
                    economic_path_id=uhv_ramp_path,
                    rationale=(
                        "separate time-domain ramp assumption prevents FCFF money from "
                        "masquerading as a Capacity ramp input"
                    ),
                ),
                canonical_unit="years",
                transform_id="identity_observation",
                input_evidence_ids=(_evidence_id(uhv_ramp_metric),),
                min_value="0",
            )
        )

        uhv_capex_metric = f"model_{scenario.lower()}_uhv_property_capex"
        uhv_capex_value = float(
            context.ledger.get(_evidence_id(uhv_capex_metric)).value
        )
        uhv_capex_bridge_id = f"B:SANIL:{scenario}:uhv_property_capex"
        uhv_capex_path = f"sanil:{scenario.lower()}:uhv_property_capex"
        uhv_capex_evidence_ids = (_evidence_id(uhv_capex_metric),)
        uhv_capex_hypothesis = f"H:SANIL:{scenario}"
        if scenario == "Core":
            uhv_capex_bridge_id = "B:SANIL:UHV:CAPEX"
            uhv_capex_path = f"{UHV_CAPACITY_PATH_ROOT}:capex"
            uhv_capex_evidence_ids = (
                _uhv_evidence_id("capex_committed"),
                _evidence_id(uhv_capex_metric),
            )
            uhv_capex_hypothesis = "H:SANIL:UHV_CAPACITY"
        drafts.append(
            BridgeDraft(
                assumption_key="uhv_property_capex",
                scenario_id=scenario,
                bridge=_bridge(
                    bridge_id=uhv_capex_bridge_id,
                    evidence_ids=uhv_capex_evidence_ids,
                    hypothesis_id=uhv_capex_hypothesis,
                    affected_variable=AffectedVariable.QUANTITY,
                    direction=Direction.UP,
                    old_value=0.0,
                    new_value=uhv_capex_value,
                    unit="KRW_billion",
                    economic_path_id=uhv_capex_path,
                    rationale=(
                        "full disclosed UHV property consideration is deducted "
                        "as a separate explicit cash outflow"
                    ),
                ),
                canonical_unit="KRW_billion",
                transform_id="identity_observation",
                input_evidence_ids=(_evidence_id(uhv_capex_metric),),
                min_value="0",
            )
        )

        for key in ("terminal_growth", "terminal_roic"):
            metric = f"model_{scenario.lower()}_{key}"
            value = float(context.ledger.get(_evidence_id(metric)).value)
            bridge_id = f"B:SANIL:{scenario}:{key}"
            path = f"sanil:{scenario.lower()}:{key}"
            evidence_ids = (_evidence_id(metric),)
            hypothesis = hypothesis_id
            direction = Direction.UNCHANGED
            old_value = value
            if scenario == "Core" and key == "terminal_growth":
                bridge_id = "B:SANIL:RAMP"
                path = f"{CAPACITY_PATH_ROOT}:ramp"
                evidence_ids = (_evidence_id("expansion_ramp_date"), _evidence_id(metric))
                hypothesis = "H:SANIL:CAPACITY"
                direction = Direction.UP
                old_value = 0.0
            drafts.append(
                BridgeDraft(
                    assumption_key=key,
                    scenario_id=scenario,
                    bridge=_bridge(
                        bridge_id=bridge_id,
                        evidence_ids=evidence_ids,
                        hypothesis_id=hypothesis,
                        affected_variable=(AffectedVariable.QUANTITY if key == "terminal_growth" else AffectedVariable.MARGIN),
                        direction=direction,
                        old_value=old_value,
                        new_value=value,
                        unit="ratio",
                        economic_path_id=path,
                        rationale="terminal driver bound to the scenario and reinvestment discipline",
                    ),
                    canonical_unit="ratio",
                    transform_id="identity_observation",
                    input_evidence_ids=(_evidence_id(metric),),
                    min_value="0",
                    max_value="1",
                )
            )
        for key, unit, variable in (
            ("ownership", "ratio", AffectedVariable.SEGMENT_VALUE),
            ("ev_adjustment", "KRW_billion", AffectedVariable.NET_DEBT),
            ("diluted_shares", "shares", AffectedVariable.SHARE_COUNT),
        ):
            metric = f"model_{scenario.lower()}_{key}"
            value = float(context.ledger.get(_evidence_id(metric)).value)
            drafts.append(
                BridgeDraft(
                    assumption_key=key,
                    scenario_id=scenario,
                    bridge=_bridge(
                        bridge_id=f"B:SANIL:{scenario}:{key}",
                        evidence_ids=(_evidence_id(metric),),
                        hypothesis_id="H:SANIL:CAPITAL",
                        affected_variable=variable,
                        direction=Direction.UNCHANGED,
                        old_value=value,
                        new_value=value,
                        unit=unit,
                        economic_path_id=f"sanil:{scenario.lower()}:{key}",
                        rationale="mechanical ownership, EV bridge or share-count input",
                    ),
                    canonical_unit=unit,
                    transform_id="identity_observation",
                    input_evidence_ids=(_evidence_id(metric),),
                    min_value=("0" if key != "ev_adjustment" else None),
                    max_value=("1" if key == "ownership" else None),
                )
            )
    return BridgeProposalBundle(
        drafts=tuple(drafts),
        rationale="LLM proposes typed assumptions only; the compiler and DCF engine reproduce all arithmetic.",
    )


def _capacity_loader(context: OrchestratorContext) -> CapacityCommitmentInput:
    second_factory_gate = ProjectGateSet(
        project_id=CAPACITY_PROJECT_ID,
        required_gates=(ProjectGate.LAND_CONTROL,),
        observations=(
            ProjectGateEvidence(
                ProjectGate.LAND_CONTROL,
                True,
                (_evidence_id("expansion_land_control"),),
                effective_at="2024-01-01",
                note="company-controlled second-factory site",
            ),
        ),
    )
    second_factory = CapacityProjectBinding(
        project_id=CAPACITY_PROJECT_ID,
        segment_id=SEGMENT_ID,
        gate_set=second_factory_gate,
        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,
        baseline_inclusion_evidence_ids=(
            _evidence_id("expansion_baseline_inclusion"),
        ),
        site_area_evidence_ids=(_evidence_id("expansion_site_area"),),
        committed_capex_evidence_ids=(
            _evidence_id("expansion_capex_committed"),
        ),
        ramp_date_evidence_ids=(_evidence_id("expansion_ramp_date"),),
        equipment_commitment_evidence_ids=(
            _evidence_id("expansion_equipment_commitment"),
        ),
    )
    uhv_gate = ProjectGateSet(
        project_id=UHV_CAPACITY_PROJECT_ID,
        required_gates=(ProjectGate.LAND_CONTROL,),
        observations=(
            ProjectGateEvidence(
                ProjectGate.LAND_CONTROL,
                True,
                (_uhv_evidence_id("land_control"),),
                effective_at="2026-08-26",
                note="signed official UHV property-acquisition contract",
            ),
        ),
    )
    uhv_property = CapacityProjectBinding(
        project_id=UHV_CAPACITY_PROJECT_ID,
        segment_id=SEGMENT_ID,
        gate_set=uhv_gate,
        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,
        baseline_inclusion_evidence_ids=(
            _uhv_evidence_id("baseline_inclusion"),
        ),
        committed_capex_evidence_ids=(
            _uhv_evidence_id("capex_committed"),
        ),
        ramp_date_evidence_ids=(_uhv_evidence_id("ramp_boundary"),),
    )
    return CapacityCommitmentInput(
        (
            CapacitySegmentCommitmentInput(
                SEGMENT_ID,
                (second_factory, uhv_property),
                (),
            ),
        )
    )


def _capacity_consumption_loader(
    context: OrchestratorContext,
) -> CapacityBridgeConsumptionContract:
    assessment = context.data["capacity_commitment_assessment"]
    return CapacityBridgeConsumptionContract(
        assessment.assessment_hash,
        (
            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPACITY, "B:SANIL:CAPACITY", (_evidence_id("expansion_land_control"), _evidence_id("expansion_site_area")), CAPACITY_PATH_ROOT),
            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPEX, "B:SANIL:CAPEX", (_evidence_id("expansion_capex_committed"),), CAPACITY_PATH_ROOT),
            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.RAMP, "B:SANIL:RAMP", (_evidence_id("expansion_ramp_date"),), CAPACITY_PATH_ROOT),
            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPACITY, "B:SANIL:UHV:CAPACITY", (_uhv_evidence_id("land_control"), _uhv_evidence_id("capex_committed")), UHV_CAPACITY_PATH_ROOT),
            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPEX, "B:SANIL:UHV:CAPEX", (_uhv_evidence_id("capex_committed"),), UHV_CAPACITY_PATH_ROOT),
            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.RAMP, "B:SANIL:UHV:RAMP", (_uhv_evidence_id("ramp_boundary"),), UHV_CAPACITY_PATH_ROOT),
        ),
    )


def _target_structure(snapshot: SanilSnapshot) -> LiveCapitalStructureObservation:
    r = snapshot.risk
    return LiveCapitalStructureObservation(
        equity_weight=float(r["target_equity_weight"]),
        debt_weight=float(r["target_debt_weight"]),
        tax_rate=float(r["tax_rate"]),
        method=TargetCapitalStructureMethod.LONG_RUN_POLICY,
        as_of=str(r["as_of"]),
        source_refs=(str(_source(snapshot, "annual_report")["source_ref"]), str(_source(snapshot, "risk_snapshot")["source_ref"])),
        rationale="low-debt long-run structure anchored to the filed net-cash balance sheet; peer debt is not imposed on Sanil",
    )


def _beta_loader(snapshot: SanilSnapshot):
    def load(context: OrchestratorContext) -> LiveBetaUniverse:
        r = snapshot.risk
        levels = []
        for level in BetaLevelName:
            peers = []
            for row in r["peers"][level.value]:
                debt_to_equity = float(row["debt_to_equity"])
                peers.append(
                    LivePeerBetaObservation(
                        peer_id=str(row["peer_id"]),
                        levered_beta=float(row["levered_beta"]),
                        debt=debt_to_equity,
                        equity=1.0,
                        tax_rate=float(r["tax_rate"]),
                        benchmark_id=str(r["benchmark_id"]),
                        return_frequency=str(r["return_frequency"]),
                        estimation_window_months=int(r["estimation_window_months"]),
                        as_of=str(r["as_of"]),
                        source_ref=str(
                            row.get("source_ref")
                            or _source(snapshot, "risk_snapshot")["source_ref"]
                        ),
                        beta_standard_error=(
                            float(row["beta_standard_error"])
                            if row.get("beta_standard_error") is not None
                            else None
                        ),
                        estimation_method=str(
                            row.get(
                                "estimation_method",
                                "external provider Beta snapshot",
                            )
                        ),
                    )
                )
            levels.append(
                LiveBetaLevelObservation(
                    level=level,
                    peers=tuple(peers),
                    selection_rationale=f"{level.value} selected by systematic-risk proximity, not valuation similarity",
                    selection_evidence_ids=(_evidence_id(f"beta_selection_{level.value}"),),
                    risk_driver_features=(("fixed_cost_intensity", "backlog_duration", "customer_concentration", "export_mix") if level is BetaLevelName.L4_ECONOMIC_TWINS else ()),
                )
            )
        return LiveBetaUniverse(
            levels=tuple(levels),
            target_capital_structure=_target_structure(snapshot),
            universe_rationale="L1→L4 partial pooling separates broad industrial risk from transformer Economic Twins",
            source_refs=(str(_source(snapshot, "risk_snapshot")["source_ref"]),),
        )

    return load


def _wacc_loader(snapshot: SanilSnapshot):
    def load(context: OrchestratorContext) -> LiveWACCInputs:
        r = snapshot.risk
        source_ref = str(_source(snapshot, "risk_snapshot")["source_ref"])
        structure = _target_structure(snapshot)
        return LiveWACCInputs(
            cash_flow_currency="KRW",
            risk_free_rate=RateObservation(float(r["risk_free_rate"]), "KRW", str(r["as_of"]), source_ref, "Korean 10-year government yield snapshot"),
            equity_risk_premium=RateObservation(float(r["mature_market_erp"]), "KRW", str(r["as_of"]), source_ref, "mature-market ERP snapshot"),
            marginal_pre_tax_cost_of_debt=RateObservation(float(r["pre_tax_cost_of_debt"]), "KRW", str(r["as_of"]), source_ref, "current marginal KRW borrowing benchmark"),
            target_capital_structure=structure,
            country_risk_premium=RateObservation(float(r["country_risk_premium"]), "KRW", str(r["as_of"]), source_ref, "country risk premium separated from mature ERP"),
            country_risk_lambda=float(r["country_risk_lambda"]),
            country_risk_source_ref=str(_source(snapshot, "annual_report")["source_ref"]),
            terminal_growth=float(snapshot.scenarios["Core"]["terminal_growth"]),
            terminal_roic=float(snapshot.scenarios["Core"]["terminal_roic"]),
        )

    return load


def _dcf_fingerprint_loader(context: OrchestratorContext) -> EconomicAssumptionFingerprint:
    core = context.data["compiled_assumption_set"]
    fcff = [float(core.get(f"fcff_year_{year}", "Core").measure.amount) for year in range(1, FORECAST_YEARS + 1)]
    growth = tuple(fcff[index] / fcff[index - 1] - 1.0 for index in range(1, len(fcff)))
    return EconomicAssumptionFingerprint(
        growth_rates=growth,
        margin_path=(0.37, 0.35, 0.34, 0.32, 0.30),
        reinvestment_path=(0.12, 0.15, 0.17, 0.16, 0.15),
        growth_duration_years=FORECAST_YEARS,
    )


def _per_loader(context: OrchestratorContext) -> LivePERInputs:
    return LivePERInputs(
        target_id=TARGET_ID,
        applicability=PERApplicability.NOT_APPLICABLE,
        applicability_rationale="No authorized same-as-of Economic-Twin residual PER pack is included; PER is withheld rather than approximated.",
    )


def _broker_research_loader(snapshot: SanilSnapshot):
    def load(_context: OrchestratorContext) -> BrokerResearchBatch:
        return BrokerResearchBatch(
            checked_at=snapshot.cutoff,
            observations=(
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:POWER_SOLUTION_CONTEXT",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.INDUSTRY_DEEP_DIVE,
                        field_class=BrokerFieldClass.MECHANISM_CANDIDATE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae's same-date power-solution coverage frames qualified "
                            "transformer capacity expansion and delivery-slot scarcity as "
                            "sector mechanisms; Sanil-specific facts remain primary-verified."
                        ),
                        target_company_specific=False,
                        underlying_data_families=("company_filing", "company_ir"),
                        report_date="2026-07-16",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_POWER_SOLUTION_REPORT_URL,
                    verification_metrics=("effective_capacity", "utilization", "lead_time"),
                    verification_requests=(
                        "verify capacity, utilization and delivery lead time in company primary sources",
                    ),
                    primary_source_hints=("2025 annual report", "2Q26 company IR"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.EARNINGS_REVIEW,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae identifies order/backlog, specialty-transformer mix "
                            "and capacity utilization as key Sanil operating signals; "
                            "the runtime must verify them in company primary sources."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                    verification_metrics=("orders", "backlog", "mix", "utilization"),
                    verification_requests=(
                        "verify orders, backlog, mix and utilization in official filing/IR",
                    ),
                    primary_source_hints=("2025 annual report", "2Q26 company IR"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.COMPANY_UPDATE,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae flags a separate UHV expansion path; exact future "
                            "capacity and timing are not accepted until company primary "
                            "evidence establishes land control, committed spend and ramp boundaries."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_filing",),
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                    verification_metrics=(
                        "expansion_land_control",
                        "expansion_site_area",
                        "expansion_capex_committed",
                        "expansion_ramp_date",
                    ),
                    verification_requests=(
                        "verify UHV land control, disclosed consideration and ramp boundary in company filing",
                    ),
                    primary_source_hints=("company property-acquisition filing",),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:IBK:2Q26_PRIMARY_LEADS",
                        source_id="KR_IBK_RESEARCH",
                        broker_family="IBKSecurities",
                        report_type=BrokerReportType.EARNINGS_REVIEW,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "IBK highlights record quarterly revenue, specialty-transformer "
                            "order/backlog mix and follow-on data-center orders; all company "
                            "facts are verification leads until matched to official filing/IR."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-10",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_IBK_2Q26_REPORT_URL,
                    verification_metrics=(
                        "revenue_h1_2026",
                        "operating_profit_h1_2026",
                        "orders",
                        "backlog",
                        "mix",
                    ),
                    verification_requests=(
                        "verify H1 revenue/profit, orders, backlog and specialty mix in company primary sources",
                    ),
                    primary_source_hints=("2Q26 company IR", "2025 annual report"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:SHINHAN:ORDER_PRIMARY_LEADS",
                        source_id="KR_SHINHAN_RESEARCH",
                        broker_family="ShinhanSecurities",
                        report_type=BrokerReportType.COMPANY_UPDATE,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Shinhan cites strong results and order acceleration as the "
                            "operating catalyst; orders and backlog must be re-verified "
                            "from company primary sources before intrinsic use."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-11",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_SHINHAN_2Q26_REPORT_URL,
                    verification_metrics=("orders", "backlog"),
                    verification_requests=(
                        "verify order acceleration and backlog in company filing/IR",
                    ),
                    primary_source_hints=("2Q26 company IR", "2025 annual report"),
                ),
            ),
            source_refs=(
                _MIRAE_POWER_SOLUTION_REPORT_URL,
                _MIRAE_2Q26_REPORT_URL,
                _IBK_2Q26_REPORT_URL,
                _SHINHAN_2Q26_REPORT_URL,
            ),
        )

    return load


def _street_reports() -> tuple[StreetResearchReport, ...]:
    return (
        StreetResearchReport(
            broker="Mirae Asset Securities",
            analyst="Kim Tae-hyung",
            published_date="2026-08-07",
            target_price=250000.0,
            target_price_currency="KRW",
            valuation_method="PER-based target framework",
            base_year="2028",
            estimates=(),
            source_ref=_MIRAE_2Q26_REPORT_URL,
        ),
        StreetResearchReport(
            broker="IBK Securities",
            analyst="Kim Tae-hyun",
            published_date="2026-08-10",
            target_price=220000.0,
            target_price_currency="KRW",
            valuation_method="broker target-price framework",
            base_year="2027",
            estimates=(),
            source_ref=_IBK_2Q26_REPORT_URL,
        ),
        StreetResearchReport(
            broker="Shinhan Securities",
            analyst="Choi Seung-hwan / Lee Byung-hwa",
            published_date="2026-08-11",
            target_price=310000.0,
            target_price_currency="KRW",
            valuation_method="2027E PER 35x",
            base_year="2027",
            estimates=(),
            source_ref=_SHINHAN_2Q26_REPORT_URL,
        ),
    )


def _valuation_plan_inputs(context: OrchestratorContext) -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW",
        diluted_shares_key="diluted_shares",
        segment_bindings=(
            SegmentValueBinding(
                segment_id=SEGMENT_ID,
                asset_id=SEGMENT_ID,
                ownership_key="ownership",
                ev_to_equity_adjustment_key="ev_adjustment",
            ),
        ),
    )


def build_sanil_live_primary_config(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260826",
    snapshot_path: str | Path | None = None,
    market_snapshot_path: str | Path | None = None,
) -> LivePrimaryRuntimeConfig:
    snapshot = load_sanil_snapshot(snapshot_path)
    records = _all_records(snapshot)
    supported_metrics = tuple(dict.fromkeys((*MODULE_METRICS, *(item.metric for item in records))))
    collector = LiveCollectorProvider(
        CollectorCapability(
            collector_id="sanil-source-backed-snapshot",
            source_id="KR_OPENDART",
            supported_metrics=supported_metrics,
            jurisdictions=("KR",),
            implementation_ref="valuation_engine.sanil_live_primary._primary_collector",
        ),
        _primary_collector(snapshot),
    )

    def resolver(request: CompanyResolutionRequest) -> ResolvedCompanyIdentity:
        if request.query not in {TICKER, "산일전기", "Sanil Electric", TARGET_ID}:
            raise ValueError("Sanil provider accepts only the Sanil identity")
        return _identity(snapshot)

    def snapshot_loader(_: ResolvedCompanyIdentity) -> IndustryKnowledgeSnapshot:
        return _industry_snapshot(snapshot)

    def freshness_loader(_: ResolvedCompanyIdentity, industry: IndustryKnowledgeSnapshot) -> LiveFreshnessAssessment:
        return LiveFreshnessAssessment(
            checked_at=snapshot.cutoff,
            findings=(WatchFinding(WatchStatus.CLEAN, "SANIL_OFFICIAL_SOURCES", "annual report and 2Q26 IR snapshot frozen at the declared cutoff", (), False),),
            source_snapshot_hash=industry.snapshot_hash,
        )

    def decomposer(_: ResolvedCompanyIdentity, __: IndustryKnowledgeSnapshot) -> tuple[SegmentDescriptor, ...]:
        return (
            SegmentDescriptor(
                segment_id=SEGMENT_ID,
                name="Power and specialty transformers",
                revenue_recognition="delivery",
                price_formation="contracted ASP and product mix",
                asset_ownership="owned manufacturing",
                capital_intensity="medium-high",
                regulation_intensity="medium",
                customer_structure="export utilities, renewable and data-center buyers",
                reinvestment_model="land-controlled factory and equipment ramp",
                cashflow_duration="backlog-conversion cycle",
                evidence_ids=("E:SANIL:SEGMENT",),
            ),
        )

    def router(_: ResolvedCompanyIdentity, segments: tuple[SegmentDescriptor, ...], __: IndustryKnowledgeSnapshot) -> tuple[IndustryDNAProfile, ...]:
        segment = segments[0]
        return (
            IndustryDNAProfile(
                segment_id=segment.segment_id,
                sector_adapter="power.transformer_switchgear",
                archetypes=(EconomicArchetype.CONTRACTED_BACKLOG, EconomicArchetype.CAPACITY_MANUFACTURING),
                revenue_recognition=segment.revenue_recognition,
                price_formation=segment.price_formation,
                asset_ownership=segment.asset_ownership,
                capital_intensity=segment.capital_intensity,
                regulation_intensity=segment.regulation_intensity,
                customer_structure=segment.customer_structure,
                reinvestment_model=segment.reinvestment_model,
                cashflow_duration=segment.cashflow_duration,
                evidence_keys=("E:SANIL:SEGMENT", "E:SANIL:INDUSTRY"),
            ),
        )

    def market_loader() -> MarketObservation:
        market = load_sanil_market_snapshot(market_snapshot_path)
        return MarketObservation(
            market.price,
            market.as_of,
            market.source_ref,
        )

    providers = LivePrimaryProviders(
        company_resolver=resolver,
        industry_snapshot_loader=snapshot_loader,
        freshness_loader=freshness_loader,
        segment_decomposer=decomposer,
        industry_dna_router=router,
        collectors=(collector,),
        scanner_runners={scanner_id: _scanner_runner for scanner_id in MANDATORY_SCANNERS},
        intelligence_officer=_intelligence_officer,
        red_team_officer=_red_team_officer,
        bridge_analyst=_bridge_analyst,
        evaluator_registry_loader=live_fcff_dcf_registry_loader(
            registrations=(
                LiveDCFRegistration(
                    "capacity_manufacturing",
                    "driver_dcf",
                    "1",
                    FORECAST_YEARS,
                    expansion_capex_key="expansion_capex",
                    expansion_capex_year=2,
                    additive_fcff_prefixes=("uhv_",),
                    additional_expansion_capex=(("uhv_property_capex", 2),),
                    trace_assumption_keys=("uhv_ramp_years",),
                ),
            ),
            include_default_normalized_multiples=True,
        ),
        valuation_plan_inputs_loader=_valuation_plan_inputs,
        broker_research_loader=_broker_research_loader(snapshot),
        capacity_commitment_loader=_capacity_loader,
        capacity_bridge_consumption_loader=_capacity_consumption_loader,
        funding_scanner=_funding_scanner,
        beta_loader=_beta_loader(snapshot),
        wacc_loader=_wacc_loader(snapshot),
        dcf_fingerprint_loader=_dcf_fingerprint_loader,
        per_loader=_per_loader,
        street_loader=_street_reports,
        market_loader=market_loader,
    )
    required_keys = tuple(
        dict.fromkeys(
            (
                *(f"fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),
                "expansion_capex",
                *(f"uhv_fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),
                "uhv_property_capex",
                "uhv_ramp_years",
                "terminal_growth",
                "terminal_roic",
                "ownership",
                "ev_adjustment",
                "diluted_shares",
            )
        )
    )
    return LivePrimaryRuntimeConfig(
        run_id=run_id,
        state_root=state_root,
        company_request=CompanyResolutionRequest(TICKER, "KR"),
        scenario_binding_spec=ScenarioBindingSpec(SCENARIOS, required_keys),
        providers=providers,
        additional_required_evidence={
            SEGMENT_ID: tuple(item.metric for item in records)
        },
        require_broker_research=True,
        method_choices=(SegmentMethodChoice(SEGMENT_ID, "capacity_manufacturing", "driver_dcf", "1"),),
        capacity_core_scenario_id="Core",
        market_currency="KRW",
        initial_data={
            "data_cutoff": snapshot.cutoff,
            "underwriting_status": "PRELIMINARY_SOURCE_BACKED_UNDERWRITE",
            "evidence_confidence": "official company facts and signed UHV land contract high; common-source regression Beta moderate; forward FCFF assumptions moderate",
        },
    )


def run_sanil_live_primary(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260826",
    snapshot_path: str | Path | None = None,
    market_snapshot_path: str | Path | None = None,
):
    from .live_runtime import run_prism

    return run_prism(
        build_sanil_live_primary_config(
            state_root,
            run_id=run_id,
            snapshot_path=snapshot_path,
            market_snapshot_path=market_snapshot_path,
        )
    )
