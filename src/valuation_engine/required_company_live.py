from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .collection_plan import CollectorCapability
from .context_strength_linkage import ContextStrengthLinkageDecision
from .control_plane import StageStatus
from .equity_evaluators import (
    LiveEquityMethodRegistration,
    live_equity_evaluator_registry_loader,
)
from .evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
)
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
from .method_capabilities import load_default_method_capability_registry
from .module_plan import build_module_requirement_plan as build_runtime_module_requirement_plan
from .module_requirements import build_module_requirement_plan_from_repo
from .per import EconomicAssumptionFingerprint
from .per_adapters import LivePERInputs, PERApplicability
from .risk import BETA_LEVEL_ORDER
from .risk_adapters import (
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
)
from .records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
    MarketObservation,
)
from .scanner_runtime import ScannerFinding, ScannerFindingStatus
from .scenario_binding import ScenarioBindingSpec
from .source_watch import WatchFinding, WatchStatus
from .street import StreetResearchReport
from .valuation_execution import default_evaluator_registry
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = _REPO_ROOT / "config" / "live_company_fixture_specs.yaml"
ASSUMPTION_METRICS = (
    "normalized_ebitda",
    "normalized_ebitda_multiple",
    "normalized_multiple",
    "ownership",
    "ev_adjustment",
    "diluted_shares",
)


@dataclass(frozen=True)
class AcceptanceCompanySpec:
    company_id: str
    payload: Mapping[str, Any]
    official_document_hash: str
    underwriting_document_hash: str
    market_price: float
    market_as_of: str

    @property
    def legal_name(self) -> str:
        return str(self.payload["legal_name"])

    @property
    def ticker(self) -> str:
        return str(self.payload["ticker"])

    @property
    def jurisdiction(self) -> str:
        return str(self.payload["jurisdiction"])

    @property
    def target_id(self) -> str:
        return str(self.payload["target_id"])

    @property
    def official_source_id(self) -> str:
        return str(self.payload["official_source_id"])

    @property
    def official_source_ref(self) -> str:
        return str(self.payload["official_source_ref"])

    @property
    def underwriting_source_ref(self) -> str:
        return str(self.payload["underwriting_source_ref"])

    @property
    def official_document_id(self) -> str:
        return str(self.payload["official_document_id"])

    @property
    def as_of(self) -> str:
        return str(self.payload["as_of"])

    @property
    def segment_id(self) -> str:
        return "core"

    @property
    def archetype(self) -> EconomicArchetype:
        return EconomicArchetype(str(self.payload["archetype"]))

    @property
    def method(self) -> str:
        return str(self.payload["method"])

    @property
    def reporting_unit(self) -> str:
        return str(self.payload["currency"])

    @property
    def external_ids(self) -> tuple[tuple[str, str], ...]:
        raw = self.payload["external_ids"]
        return tuple((str(key), str(value)) for key, value in raw.items())


@dataclass(frozen=True)
class SourceHashBundle:
    official_document_hash: str
    underwriting_document_hash: str


def load_acceptance_specs(
    path: str | Path = DEFAULT_SPEC_PATH,
) -> dict[str, Mapping[str, Any]]:
    source_path = Path(path)
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("companies"), dict):
        raise ValueError("live company fixture specs require a companies mapping")
    common = {
        "as_of": str(payload["as_of"]),
        "underwriting_source_ref": str(payload["underwriting_source_ref"]),
    }
    return {
        str(company_id): {**common, **row}
        for company_id, row in payload["companies"].items()
    }


def build_acceptance_spec(
    company_id: str,
    *,
    official_document_hash: str,
    underwriting_document_hash: str,
    market_price: float,
    market_as_of: str,
    path: str | Path = DEFAULT_SPEC_PATH,
) -> AcceptanceCompanySpec:
    specs = load_acceptance_specs(path)
    try:
        payload = specs[company_id]
    except KeyError as exc:
        raise KeyError(f"unknown live company acceptance ID: {company_id}") from exc
    for label, value in (
        ("official_document_hash", official_document_hash),
        ("underwriting_document_hash", underwriting_document_hash),
    ):
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    if market_price <= 0:
        raise ValueError("market price must be positive")
    return AcceptanceCompanySpec(
        company_id=company_id,
        payload=payload,
        official_document_hash=official_document_hash,
        underwriting_document_hash=underwriting_document_hash,
        market_price=float(market_price),
        market_as_of=market_as_of,
    )


def _identity(spec: AcceptanceCompanySpec) -> ResolvedCompanyIdentity:
    return ResolvedCompanyIdentity(
        target_id=spec.target_id,
        legal_name=spec.legal_name,
        ticker=spec.ticker,
        jurisdiction=spec.jurisdiction,
        external_ids=spec.external_ids,
        source_refs=(spec.official_source_ref,),
    )


def _lineage(
    spec: AcceptanceCompanySpec,
    *,
    evidence_id: str,
    document_hash: str,
    published_at: str,
    revision_id: str,
) -> AuthoritativeEvidenceLineage:
    return AuthoritativeEvidenceLineage(
        evidence_id=evidence_id,
        target_id=spec.target_id,
        source_id=spec.official_source_id,
        observed_date=spec.as_of,
        content_hash=document_hash,
        event_date=spec.as_of,
        effective_date=spec.as_of,
        published_at=published_at,
        first_seen_at=str(spec.payload["first_seen_at"]),
        revision_id=revision_id,
        revision_at=published_at,
    )


def _industry_snapshot(spec: AcceptanceCompanySpec) -> IndustryKnowledgeSnapshot:
    official_evidence = f"E:{spec.company_id}:INDUSTRY"
    segment_evidence = f"E:{spec.company_id}:SEGMENT"
    underwriting_evidence = f"E:{spec.company_id}:UNDERWRITING_CONTRACT"
    return IndustryKnowledgeSnapshot.build(
        as_of=spec.as_of,
        source_ids=(spec.official_source_id,),
        document_ids=(
            spec.official_document_id,
            f"{spec.company_id}_UNDERWRITING_SPEC",
        ),
        evidence_ids=(official_evidence, segment_evidence, underwriting_evidence),
        content_hashes=(
            spec.official_document_hash,
            spec.underwriting_document_hash,
        ),
        evidence_lineage=(
            _lineage(
                spec,
                evidence_id=official_evidence,
                document_hash=spec.official_document_hash,
                published_at=str(spec.payload["published_at"]),
                revision_id=spec.official_document_id,
            ),
            _lineage(
                spec,
                evidence_id=segment_evidence,
                document_hash=spec.official_document_hash,
                published_at=str(spec.payload["published_at"]),
                revision_id=f"{spec.official_document_id}:segment",
            ),
            _lineage(
                spec,
                evidence_id=underwriting_evidence,
                document_hash=spec.underwriting_document_hash,
                published_at=str(spec.payload["first_seen_at"]),
                revision_id=f"{spec.company_id}:underwriting:v1",
            ),
        ),
    )


def _profile(spec: AcceptanceCompanySpec) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id=spec.segment_id,
        sector_adapter=str(spec.payload["sector_adapter"]),
        archetypes=(spec.archetype,),
        revenue_recognition="filing-defined delivery or service recognition",
        price_formation="contract, subscription or commodity economics by selected route",
        asset_ownership="issuer-owned operating assets and contractual rights",
        capital_intensity="issuer-specific",
        regulation_intensity="issuer-specific",
        customer_structure="disclosed customer and contract structure",
        reinvestment_model="evidence-backed maintenance and growth reinvestment",
        cashflow_duration="normalized through the selected exact method",
        evidence_keys=(
            f"E:{spec.company_id}:INDUSTRY",
            f"E:{spec.company_id}:SEGMENT",
        ),
    )


def _segment_descriptor(spec: AcceptanceCompanySpec) -> SegmentDescriptor:
    return SegmentDescriptor(
        segment_id=spec.segment_id,
        name=f"{spec.legal_name} consolidated operations",
        revenue_recognition="official filing basis",
        price_formation="selected Industry DNA route",
        asset_ownership="issuer consolidated operations",
        capital_intensity="issuer-specific",
        regulation_intensity="issuer-specific",
        customer_structure="official filing and underwriting contract",
        reinvestment_model="normalized reinvestment",
        cashflow_duration="normalized operating cycle",
        evidence_ids=(f"E:{spec.company_id}:SEGMENT",),
    )


def _evidence_id(spec: AcceptanceCompanySpec, metric: str) -> str:
    return f"E:{spec.company_id}:{metric}"


def _metric_unit(metric: str, spec: AcceptanceCompanySpec) -> str:
    if metric in {"normalized_ebitda", "ev_adjustment"}:
        return spec.reporting_unit
    if metric == "normalized_ebitda_multiple" or metric == "normalized_multiple":
        return "multiple"
    if metric == "ownership":
        return "ratio"
    if metric == "diluted_shares":
        return "shares"
    lowered = metric.lower()
    if any(
        token in lowered
        for token in (
            "margin",
            "utilization",
            "retention",
            "churn",
            "conversion",
            "yield",
            "mix",
            "concentration",
            "efficiency",
            "cancellation",
            "book_to_bill",
        )
    ):
        return "ratio"
    if "lead_time" in lowered or lowered.endswith("duration"):
        return "years"
    if any(
        token in lowered
        for token in (
            "revenue",
            "backlog",
            "order",
            "liabilit",
            "capex",
            "cost",
            "cash",
            "price",
            "arr",
            "rpo",
            "service",
            "asp",
            "ffo",
            "ebitda",
        )
    ):
        return spec.reporting_unit
    return "count"


def _underwriting_observation(metric: str, spec: AcceptanceCompanySpec) -> tuple[Any, str]:
    explicit = {
        "normalized_ebitda": spec.payload["normalized_ebitda"],
        "normalized_ebitda_multiple": spec.payload["normalized_multiple"],
        "normalized_multiple": spec.payload["normalized_multiple"],
        "ownership": spec.payload["ownership"],
        "ev_adjustment": spec.payload["ev_adjustment"],
        "diluted_shares": spec.payload["diluted_shares"],
    }
    if metric in explicit:
        return explicit[metric], _metric_unit(metric, spec)
    declared = dict(spec.payload.get("underwriting_metrics", {}))
    if metric not in declared:
        raise ValueError(
            f"{spec.company_id} requires an explicit underwriting observation for {metric}; "
            "implicit placeholder values are forbidden"
        )
    value, unit = declared[metric]
    return value, str(unit)


def _record(
    spec: AcceptanceCompanySpec,
    *,
    metric: str,
    value: Any,
    unit: str,
    layer: EvidenceSourceLayer,
    source_ref: str,
    source_name: str,
    confidence: float,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=_evidence_id(spec, metric),
        target=spec.target_id,
        metric=metric,
        value=value,
        unit=unit,
        source_layer=layer,
        effective_date=spec.as_of,
        observed_date=spec.as_of,
        source_name=source_name,
        source_ref=source_ref,
        source_grade="A" if layer is not EvidenceSourceLayer.ANALYST_UNDERWRITING else "B",
        confidence=confidence,
        segment=spec.segment_id,
        notes=(
            "official source-backed observed input"
            if layer is not EvidenceSourceLayer.ANALYST_UNDERWRITING
            else "explicit QA-underwriting input; not represented as issuer guidance"
        ),
    )


def _official_collector(spec: AcceptanceCompanySpec):
    raw_metrics = dict(spec.payload.get("official_metrics", {}))

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        rows = []
        for metric in request.required_metrics:
            value, unit = raw_metrics[metric]
            rows.append(
                _record(
                    spec,
                    metric=metric,
                    value=value,
                    unit=str(unit),
                    layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                    source_ref=spec.official_source_ref,
                    source_name=spec.official_document_id,
                    confidence=1.0,
                )
            )
        return EvidenceCollectionBatch(
            source_id=spec.official_source_id,
            checked_at=spec.as_of,
            records=tuple(rows),
            source_fingerprint=spec.official_document_hash,
            document_ids=(spec.official_document_id,),
        )

    return collect


def _underwriting_collector(spec: AcceptanceCompanySpec):
    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        rows = []
        for metric in request.required_metrics:
            value, unit = _underwriting_observation(metric, spec)
            rows.append(
                _record(
                    spec,
                    metric=metric,
                    value=value,
                    unit=unit,
                    layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    source_ref=spec.underwriting_source_ref,
                    source_name=f"{spec.company_id} acceptance underwriting contract",
                    confidence=0.6,
                )
            )
        return EvidenceCollectionBatch(
            source_id=spec.official_source_id,
            checked_at=spec.as_of,
            records=tuple(rows),
            source_fingerprint=spec.underwriting_document_hash,
            document_ids=(f"{spec.company_id}_UNDERWRITING_SPEC",),
        )

    return collect


def _scanner_runner(spec: AcceptanceCompanySpec):
    # This metric is explicitly added to the company-specific collection contract.
    preferred = "normalized_ebitda"

    def run(context) -> ScannerFinding:
        return ScannerFinding(
            scanner_id=context.scanner_id,
            status=ScannerFindingStatus.PASS,
            summary=(
                f"{context.scanner_id} recorded as a context-only acceptance finding "
                f"for {spec.legal_name}"
            ),
            evidence_ids=(_evidence_id(spec, preferred),),
            context_only=True,
        )

    return run


def _funding_scanner(spec: AcceptanceCompanySpec):
    # Use an always-collected, explicitly labelled underwriting record.
    preferred = "normalized_ebitda"
    evidence_id = _evidence_id(spec, preferred)

    def scan(context) -> FundingScanResult:
        return FundingScanResult(
            state=FundedDemandState.FUNDED,
            summary=(
                "acceptance fixture preserves the route-required funding scan without "
                "changing discount rates or intrinsic value"
            ),
            ladder=FundingLadder(
                (
                    FundingLink(
                        FundingLayer.PRODUCT_OR_PROJECT,
                        FundingLayer.BUYER_CASH_FLOW,
                        "reported revenue or contract evidence confirms an operating buyer-cash-flow path",
                        ClaimStage.CONFIRMED_FACT,
                        1.0,
                        (evidence_id,),
                    ),
                )
            ),
            evidence_ids=(evidence_id,),
            economic_path_ids=(f"funding:{spec.company_id}:reported_operations",),
        )

    return scan


def _intelligence_officer(spec: AcceptanceCompanySpec):
    assumption_metrics = (
        "normalized_ebitda",
        (
            "normalized_multiple"
            if spec.method == "normalized_multiple"
            else "normalized_ebitda_multiple"
        ),
        "ownership",
        "ev_adjustment",
        "diluted_shares",
    )

    def run(context) -> IntelligenceProposal:
        hypotheses = tuple(
            HypothesisRecord(
                id=f"H:{spec.company_id}:{metric}",
                statement=(
                    f"{metric} is an explicit, source-traceable acceptance underwrite "
                    "and not an issuer forecast"
                ),
                causal_chain=(
                    f"official filing plus declared {metric} underwrite",
                    metric,
                    "fixture-only deterministic valuation",
                ),
                supporting_evidence_ids=(_evidence_id(spec, metric),),
                kill_conditions=(f"{metric} source or definition is invalidated",),
            )
            for metric in assumption_metrics
        )
        return IntelligenceProposal(
            hypotheses=hypotheses,
            rationale=(
                "real-company acceptance run separates official facts from explicit "
                "analyst-underwriting inputs and does not claim investment readiness"
            ),
            context_strength_linkage_decision=ContextStrengthLinkageDecision(
                not_applicable_reason=(
                    "This artifact proves live orchestration, lineage, audit and blocking; "
                    "it is not an initiating-coverage investment thesis."
                )
            ),
        )

    return run


def _red_team_officer(spec: AcceptanceCompanySpec):
    def run(context, hypotheses) -> RedTeamProposal:
        return RedTeamProposal(
            issues=(),
            counter_thesis=(
                f"{spec.legal_name} fixture valuation is acceptance-only; investment use "
                "requires a separate calibrated and fully underwritten research run"
            ),
        )

    return run


def _bridge_analyst(spec: AcceptanceCompanySpec):
    multiple_metric = (
        "normalized_multiple"
        if spec.method == "normalized_multiple"
        else "normalized_ebitda_multiple"
    )
    mapping = {
        "normalized_ebitda": (
            "normalized_ebitda",
            AffectedVariable.MARGIN,
        ),
        multiple_metric: (multiple_metric, AffectedVariable.MULTIPLE),
        "ownership": ("ownership", AffectedVariable.SEGMENT_VALUE),
        "ev_adjustment": ("ev_adjustment", AffectedVariable.NET_DEBT),
        "diluted_shares": ("diluted_shares", AffectedVariable.SHARE_COUNT),
    }

    def run(context, hypotheses, red_team) -> BridgeProposalBundle:
        drafts = []
        for assumption_key, (metric, variable) in mapping.items():
            evidence = context.ledger.get(_evidence_id(spec, metric))
            value = float(evidence.value)
            drafts.append(
                BridgeDraft(
                    assumption_key=assumption_key,
                    scenario_id="Base",
                    bridge=BridgeRecord(
                        id=f"B:{spec.company_id}:{assumption_key}",
                        evidence_ids=(evidence.id,),
                        hypothesis_id=f"H:{spec.company_id}:{metric}",
                        affected_variable=variable,
                        direction=Direction.UNCHANGED,
                        old_value=value,
                        new_value=value,
                        unit=evidence.unit,
                        rationale=(
                            "identity transform from an explicitly labelled acceptance "
                            "underwriting record"
                        ),
                        confidence=evidence.confidence,
                        kill_condition=f"{metric} evidence is superseded",
                        verification_event="next source refresh",
                        economic_path_id=f"acceptance:{spec.company_id}:{assumption_key}",
                    ),
                    canonical_unit=evidence.unit,
                    transform_id="identity_observation",
                    input_evidence_ids=(evidence.id,),
                    min_value=(
                        "0"
                        if assumption_key
                        in {
                            "normalized_ebitda",
                            multiple_metric,
                            "ownership",
                            "diluted_shares",
                        }
                        else None
                    ),
                    max_value="1" if assumption_key == "ownership" else None,
                )
            )
        return BridgeProposalBundle(
            drafts=tuple(drafts),
            rationale=(
                "all valuation inputs remain explicit compiler proposals with source and "
                "economic-path identities"
            ),
        )

    return run


def _risk_structure(spec: AcceptanceCompanySpec) -> LiveCapitalStructureObservation:
    tax_rate = 0.21 if spec.jurisdiction == "US" else 0.24
    return LiveCapitalStructureObservation(
        equity_weight=0.90,
        debt_weight=0.10,
        tax_rate=tax_rate,
        method=TargetCapitalStructureMethod.LONG_RUN_POLICY,
        as_of=spec.as_of,
        source_refs=(spec.underwriting_source_ref,),
        rationale=(
            "explicit acceptance-underwriting capital structure; not issuer guidance or "
            "an investment recommendation"
        ),
    )


def _beta_loader(spec: AcceptanceCompanySpec):
    def load(context) -> LiveBetaUniverse:
        selection_evidence_id = _evidence_id(spec, "normalized_ebitda")
        structure = _risk_structure(spec)
        beta_by_level = (0.85, 0.95, 1.05, 1.10)
        levels = []
        for index, (level, beta) in enumerate(
            zip(BETA_LEVEL_ORDER, beta_by_level, strict=True),
            start=1,
        ):
            levels.append(
                LiveBetaLevelObservation(
                    level=level,
                    peers=(
                        LivePeerBetaObservation(
                            peer_id=f"{spec.company_id}:QA_PEER_L{index}",
                            levered_beta=beta,
                            debt=10.0,
                            equity=90.0,
                            tax_rate=structure.tax_rate,
                            benchmark_id="QA_GLOBAL_EQUITY_BENCHMARK",
                            return_frequency="weekly",
                            estimation_window_months=60,
                            as_of=spec.as_of,
                            source_ref=spec.underwriting_source_ref,
                            beta_standard_error=0.15,
                            estimation_method=(
                                "explicit acceptance-underwriting Beta observation"
                            ),
                        ),
                    ),
                    selection_rationale=(
                        "deterministic acceptance hierarchy used only to prove the typed "
                        "Beta/WACC execution boundary"
                    ),
                    selection_evidence_ids=(selection_evidence_id,),
                    risk_driver_features=(
                        "operating leverage",
                        "contract duration",
                        "capital intensity",
                    ),
                )
            )
        return LiveBetaUniverse(
            levels=tuple(levels),
            target_capital_structure=structure,
            universe_rationale=(
                "explicit source-traceable acceptance universe; production investment "
                "research must replace it with issuer-specific economic twins"
            ),
            source_refs=(spec.underwriting_source_ref,),
        )

    return load


def _wacc_loader(spec: AcceptanceCompanySpec):
    currency = str(spec.payload["market_currency"])
    risk_free = 0.040 if currency == "USD" else 0.035
    erp = 0.045 if currency == "USD" else 0.050
    debt_cost = 0.050 if currency == "USD" else 0.045

    def load(context) -> LiveWACCInputs:
        source = spec.underwriting_source_ref
        return LiveWACCInputs(
            cash_flow_currency=currency,
            risk_free_rate=RateObservation(
                value=risk_free,
                currency=currency,
                as_of=spec.as_of,
                source_ref=source,
                methodology="explicit acceptance-underwriting risk-free observation",
            ),
            equity_risk_premium=RateObservation(
                value=erp,
                currency=currency,
                as_of=spec.as_of,
                source_ref=source,
                methodology="explicit acceptance-underwriting market ERP",
            ),
            marginal_pre_tax_cost_of_debt=RateObservation(
                value=debt_cost,
                currency=currency,
                as_of=spec.as_of,
                source_ref=source,
                methodology="explicit acceptance-underwriting marginal debt cost",
            ),
            target_capital_structure=_risk_structure(spec),
            funding_credit_evidence_ids=(
                _evidence_id(spec, "normalized_ebitda"),
            ),
        )

    return load


def _valuation_registry_loader(spec: AcceptanceCompanySpec):
    if spec.method == "normalized_multiple":
        return lambda _: default_evaluator_registry()
    return live_equity_evaluator_registry_loader(
        registrations=(
            LiveEquityMethodRegistration(
                archetype=spec.archetype.value,
                method=spec.method,
                version="1",
            ),
        ),
        capability_registry=load_default_method_capability_registry(),
    )


def _dcf_fingerprint_loader(context) -> EconomicAssumptionFingerprint:
    """Represent the exact absence of DCF drivers in a multiple-only fixture."""
    return EconomicAssumptionFingerprint(
        growth_rates=(),
        margin_path=(),
        reinvestment_path=(),
        growth_duration_years=0,
    )


def _per_loader(spec: AcceptanceCompanySpec):
    def load(context) -> LivePERInputs:
        return LivePERInputs(
            target_id=spec.target_id,
            applicability=PERApplicability.NOT_APPLICABLE,
            applicability_rationale=(
                "No authorized same-as-of Economic-Twin residual PER pack is included; "
                "PER is withheld rather than approximated."
            ),
        )

    return load


def _valuation_inputs(spec: AcceptanceCompanySpec):
    def load(context) -> CompanyValuationPlanInputs:
        return CompanyValuationPlanInputs(
            reporting_unit=spec.reporting_unit,
            diluted_shares_key="diluted_shares",
            segment_bindings=(
                SegmentValueBinding(
                    segment_id=spec.segment_id,
                    asset_id=spec.segment_id,
                    ownership_key="ownership",
                    ev_to_equity_adjustment_key="ev_adjustment",
                ),
            ),
        )

    return load


def build_real_company_runtime(
    spec: AcceptanceCompanySpec,
    *,
    state_root: str | Path,
    blocked_post_freeze: bool = False,
) -> LivePrimaryRuntimeConfig:
    identity = _identity(spec)
    snapshot = _industry_snapshot(spec)
    profile = _profile(spec)
    segment = _segment_descriptor(spec)
    plan = build_module_requirement_plan_from_repo(
        profile,
        repo_root=_REPO_ROOT,
    )
    runtime_plan = build_runtime_module_requirement_plan(
        (profile,),
        registry_path=_REPO_ROOT / "config" / "archetype_module_registry.yaml",
        control_requirements_path=(
            _REPO_ROOT / "config" / "archetype_control_requirements.yaml"
        ),
    )
    official_metrics = tuple(str(key) for key in spec.payload.get("official_metrics", {}))
    planned_metrics = tuple(
        dict.fromkeys(
            (
                *plan.required_evidence,
                *plan.required_kpis,
                *ASSUMPTION_METRICS,
            )
        )
    )
    official_supported = tuple(
        metric for metric in planned_metrics if metric in official_metrics
    )
    underwriting_supported = tuple(
        metric for metric in planned_metrics if metric not in set(official_supported)
    )
    collectors = []
    if official_supported:
        collectors.append(
            LiveCollectorProvider(
                CollectorCapability(
                    collector_id=f"{spec.company_id.lower()}-official",
                    source_id=spec.official_source_id,
                    supported_metrics=official_supported,
                    jurisdictions=(spec.jurisdiction,),
                    implementation_ref=(
                        "valuation_engine.required_company_live._official_collector"
                    ),
                ),
                _official_collector(spec),
            )
        )
    if underwriting_supported:
        collectors.append(
            LiveCollectorProvider(
                CollectorCapability(
                    collector_id=f"{spec.company_id.lower()}-underwriting",
                    source_id=spec.official_source_id,
                    supported_metrics=underwriting_supported,
                    jurisdictions=(spec.jurisdiction,),
                    implementation_ref=(
                        "valuation_engine.required_company_live._underwriting_collector"
                    ),
                ),
                _underwriting_collector(spec),
            )
        )

    def resolver(request: CompanyResolutionRequest) -> ResolvedCompanyIdentity:
        return identity

    def snapshot_loader(resolved: ResolvedCompanyIdentity) -> IndustryKnowledgeSnapshot:
        return snapshot

    def freshness_loader(
        resolved: ResolvedCompanyIdentity,
        loaded: IndustryKnowledgeSnapshot,
    ) -> LiveFreshnessAssessment:
        return LiveFreshnessAssessment(
            checked_at=spec.as_of,
            findings=(
                WatchFinding(
                    WatchStatus.CLEAN,
                    spec.official_source_id,
                    "official document and declared underwriting snapshot were hash-frozen",
                    (),
                    False,
                ),
            ),
            source_snapshot_hash=loaded.snapshot_hash,
        )

    def segment_decomposer(resolved, loaded):
        return (segment,)

    def industry_router(resolved, segments, loaded):
        return (profile,)

    scanners = {
        scanner_id: _scanner_runner(spec)
        for scanner_id in runtime_plan.mandatory_scanners
    }
    street_loader = (
        None
        if blocked_post_freeze
        else lambda: (
            StreetResearchReport(
                broker="Acceptance Underwriting",
                analyst="Deterministic QA",
                published_date=spec.as_of,
                target_price=Decimal(str(spec.market_price * 1.05)),
                target_price_currency=str(spec.payload["market_currency"]),
                valuation_method="acceptance-only normalized method",
                base_year="2026",
                estimates=(),
                source_ref=spec.underwriting_source_ref,
            ),
        )
    )
    providers = LivePrimaryProviders(
        company_resolver=resolver,
        industry_snapshot_loader=snapshot_loader,
        freshness_loader=freshness_loader,
        segment_decomposer=segment_decomposer,
        industry_dna_router=industry_router,
        collectors=tuple(collectors),
        scanner_runners=scanners,
        intelligence_officer=_intelligence_officer(spec),
        red_team_officer=_red_team_officer(spec),
        bridge_analyst=_bridge_analyst(spec),
        evaluator_registry_loader=_valuation_registry_loader(spec),
        valuation_plan_inputs_loader=_valuation_inputs(spec),
        funding_scanner=_funding_scanner(spec),
        beta_loader=_beta_loader(spec),
        wacc_loader=_wacc_loader(spec),
        dcf_fingerprint_loader=_dcf_fingerprint_loader,
        per_loader=_per_loader(spec),
        street_loader=street_loader,
        market_loader=lambda: MarketObservation(
            spec.market_price,
            spec.market_as_of,
            str(spec.payload["market_source_ref"]),
        ),
    )
    return LivePrimaryRuntimeConfig(
        run_id=(
            f"{spec.company_id}-ACCEPTANCE-BLOCKED-20260827"
            if blocked_post_freeze
            else f"{spec.company_id}-ACCEPTANCE-SUCCESS-20260827"
        ),
        state_root=state_root,
        company_request=CompanyResolutionRequest(spec.ticker, spec.jurisdiction),
        scenario_binding_spec=ScenarioBindingSpec(
            scenario_ids=("Base",),
            required_keys=(
                "normalized_ebitda",
                (
                    "normalized_multiple"
                    if spec.method == "normalized_multiple"
                    else "normalized_ebitda_multiple"
                ),
                "ownership",
                "ev_adjustment",
                "diluted_shares",
            ),
        ),
        providers=providers,
        additional_required_evidence={
            spec.segment_id: ASSUMPTION_METRICS,
        },
        method_choices=(
            SegmentMethodChoice(
                segment_id=spec.segment_id,
                archetype=spec.archetype.value,
                method=spec.method,
                version="1",
            ),
        ),
        market_currency=str(spec.payload["market_currency"]),
        initial_data={
            "acceptance_fixture": True,
            "fixture_company_id": spec.company_id,
            "evidence_confidence": (
                "official source lineage verified; valuation assumptions are explicit "
                "acceptance underwriting and not investment recommendations"
            ),
        },
    )


def spec_file_hash(path: str | Path = DEFAULT_SPEC_PATH) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()
