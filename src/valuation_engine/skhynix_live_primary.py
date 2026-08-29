from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from .collection_plan import CollectorCapability
from .context_strength_linkage import ContextStrengthLinkage, ContextStrengthLinkageDecision
from .dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from .industry_dna import EconomicArchetype, IndustryDNAProfile
from .live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    CompanyResolutionRequest,
    IndustryKnowledgeSnapshot,
    LiveFreshnessAssessment,
    ResolvedCompanyIdentity,
    SegmentDescriptor,
)
from .live_runtime import LiveCollectorProvider, LivePrimaryProviders, LivePrimaryRuntimeConfig
from .llm_staff import BridgeDraft, BridgeProposalBundle, IntelligenceProposal, RedTeamProposal
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
from .risk_adapters import (
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
)
from .scenario_binding import ScenarioBindingSpec
from .scanner_runtime import ScannerFinding, ScannerFindingStatus
from .source_watch import WatchFinding, WatchStatus
from .street import StreetResearchReport
from .valuation_execution import ParentAdjustmentPlan
from .valuation_plan_compiler import CompanyValuationPlanInputs, SegmentMethodChoice, SegmentValueBinding
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_PATH = _REPO_ROOT / "config" / "skhynix_live_snapshot.yaml"
TICKER = "000660"
TARGET_ID = "KR:DART:00164779"
SEGMENT_ID = "memory"
SCENARIOS = ("Down", "Core", "Bull")
FORECAST_YEARS = 9
MANDATORY_SCANNERS = (
    "CYCLE_NORMALIZATION",
    "COST_CURVE",
    "INVENTORY",
    "TRADE_FLOW",
)


@dataclass(frozen=True)
class SKHynixSnapshot:
    payload: dict[str, Any]
    raw_hash: str

    @property
    def as_of(self) -> str:
        return str(self.payload["as_of"])

    @property
    def cutoff(self) -> str:
        return str(self.payload["cutoff"])

    @property
    def identity(self) -> dict[str, Any]:
        return dict(self.payload["identity"])

    @property
    def sources(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.payload["sources"].items()}

    @property
    def official_facts(self) -> dict[str, list[Any]]:
        return dict(self.payload["official_facts"])

    @property
    def scenarios(self) -> dict[str, dict[str, Any]]:
        return dict(self.payload["scenarios"])

    @property
    def adjustments(self) -> dict[str, list[Any]]:
        return dict(self.payload["valuation_adjustments"])

    @property
    def risk(self) -> dict[str, Any]:
        return dict(self.payload["risk"])

    @property
    def market(self) -> dict[str, Any]:
        return dict(self.payload["market"])

    @property
    def street(self) -> dict[str, Any]:
        return dict(self.payload["street"])


def load_skhynix_snapshot(path: str | Path | None = None) -> SKHynixSnapshot:
    resolved = Path(path or DEFAULT_SNAPSHOT_PATH)
    raw = resolved.read_bytes()
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("SK hynix snapshot must be a mapping")
    snapshot = SKHynixSnapshot(payload=payload, raw_hash=sha256(raw).hexdigest())
    if snapshot.identity.get("ticker") != TICKER or snapshot.identity.get("target_id") != TARGET_ID:
        raise ValueError("SK hynix snapshot identity mismatch")
    if tuple(snapshot.scenarios) != SCENARIOS:
        raise ValueError("SK hynix snapshot scenarios must be Down/Core/Bull")
    if any(len(snapshot.scenarios[name]["fcff_krw_billion"]) != FORECAST_YEARS for name in SCENARIOS):
        raise ValueError("SK hynix FCFF paths must contain nine forecast years")
    return snapshot


def _eid(metric: str) -> str:
    return f"E:SKHYNIX:{metric}"


def _industry_snapshot(snapshot: SKHynixSnapshot) -> IndustryKnowledgeSnapshot:
    source_id = "KR_OPENDART"
    ids = ("E:SKHYNIX:SEGMENT", "E:SKHYNIX:INDUSTRY")
    lineages = tuple(
        AuthoritativeEvidenceLineage(
            evidence_id=eid,
            target_id=TARGET_ID,
            source_id=source_id,
            observed_date=snapshot.as_of,
            content_hash=snapshot.raw_hash,
            event_date=snapshot.as_of,
            effective_date=snapshot.as_of,
            published_at="2026-08-28T00:00:00+00:00",
            first_seen_at=snapshot.cutoff,
            revision_id=f"{eid}:v1",
            revision_at="2026-08-28T00:00:00+00:00",
        )
        for eid in ids
    )
    return IndustryKnowledgeSnapshot.build(
        as_of=snapshot.cutoff,
        source_ids=(source_id,),
        document_ids=("SKHYNIX_2026H1_SOURCE_PACK",),
        evidence_ids=ids,
        content_hashes=(snapshot.raw_hash,),
        evidence_lineage=lineages,
    )


def _identity(snapshot: SKHynixSnapshot) -> ResolvedCompanyIdentity:
    identity = snapshot.identity
    return ResolvedCompanyIdentity(
        target_id=TARGET_ID,
        legal_name=str(identity["legal_name"]),
        ticker=TICKER,
        jurisdiction="KR",
        external_ids=(
            ("krx_stock_code", TICKER),
            ("opendart_corp_code", str(identity["dart_corp_code"])),
            ("sec_cik", str(identity["sec_cik"])),
        ),
        source_refs=(str(identity["source_ref"]),),
    )


def _record(
    snapshot: SKHynixSnapshot,
    *,
    metric: str,
    value: Any,
    unit: str,
    source_layer: EvidenceSourceLayer,
    source_ref: str,
    notes: str,
    confidence: float,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=_eid(metric),
        target=TARGET_ID,
        metric=metric,
        value=value,
        unit=unit,
        source_layer=source_layer,
        effective_date=snapshot.as_of,
        observed_date=snapshot.as_of,
        source_name="SK hynix frozen LIVE source pack",
        source_ref=source_ref,
        source_grade="A" if source_layer is not EvidenceSourceLayer.ANALYST_UNDERWRITING else "B",
        confidence=confidence,
        segment=SEGMENT_ID,
        notes=notes,
    )


def _all_records(snapshot: SKHynixSnapshot) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    official_ref = snapshot.sources["half_year_filing"]
    q2_ref = snapshot.sources["q2_results"]
    pnt7_ref = snapshot.sources["pnt7_filing"]
    treasury_ref = snapshot.sources["treasury_filing"]
    underwrite_ref = snapshot.sources["underwriting"]

    q2_metrics = {"hbm4_mass_shipments_started", "long_term_agreements_customer_count_approx"}
    pnt7_metrics = {"pnt7_board_approved_investment"}
    treasury_metrics = {"issued_common_shares_pre_buyback", "planned_buyback_shares", "planned_buyback_cash"}
    for metric, pair in snapshot.official_facts.items():
        value, unit = pair
        source_ref = q2_ref if metric in q2_metrics else pnt7_ref if metric in pnt7_metrics else treasury_ref if metric in treasury_metrics else official_ref
        records.append(
            _record(
                snapshot,
                metric=metric,
                value=value,
                unit=str(unit),
                source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                source_ref=source_ref,
                notes="official company filing/result observation; NOT_DISCLOSED remains an explicit status rather than an invented value",
                confidence=1.0,
            )
        )

    for scenario in SCENARIOS:
        row = snapshot.scenarios[scenario]
        for year, value in enumerate(row["fcff_krw_billion"], start=1):
            records.append(
                _record(
                    snapshot,
                    metric=f"model_{scenario.lower()}_fcff_year_{year}",
                    value=value,
                    unit="KRW_billion",
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    source_ref=underwrite_ref,
                    notes="pre-existing analyst FCFF path quarantined from market price; deterministic compiler must independently bind it",
                    confidence=0.60,
                )
            )
        records.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_terminal_growth",
                value=row["terminal_growth"],
                unit="ratio",
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                source_ref=underwrite_ref,
                notes="analyst terminal-growth proposal; deterministic terminal consistency gate applies",
                confidence=0.55,
            )
        )
        records.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_terminal_roic",
                value=snapshot.payload["terminal_roic"],
                unit="ratio",
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                source_ref=underwrite_ref,
                notes="common terminal ROIC proposal used only for reinvestment consistency validation",
                confidence=0.50,
            )
        )
        records.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_ownership",
                value=1.0,
                unit="ratio",
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                source_ref=underwrite_ref,
                notes="100% parent ownership of the consolidated operating segment",
                confidence=0.95,
            )
        )
        for metric, pair in snapshot.adjustments.items():
            value, unit = pair
            layer = EvidenceSourceLayer.REALIZED_OR_FILING if metric in {"broad_cash_q2_2026", "borrowings_q2_2026", "ads_issue_proceeds", "diluted_shares"} else EvidenceSourceLayer.ANALYST_UNDERWRITING
            records.append(
                _record(
                    snapshot,
                    metric=f"model_{scenario.lower()}_{metric}",
                    value=value,
                    unit=str(unit),
                    source_layer=layer,
                    source_ref=(official_ref if metric in {"broad_cash_q2_2026", "borrowings_q2_2026"} else official_ref if metric == "ads_issue_proceeds" else treasury_ref if metric == "diluted_shares" else underwrite_ref),
                    notes=(
                        "official/derived balance-sheet or financing observation" if layer is EvidenceSourceLayer.REALIZED_OR_FILING
                        else "analyst underwriting adjustment; planned buyback is intentionally excluded until settlement evidence exists"
                    ),
                    confidence=0.95 if layer is EvidenceSourceLayer.REALIZED_OR_FILING else 0.60,
                )
            )

    for level_name, peer in snapshot.risk["beta_levels"].items():
        records.append(
            _record(
                snapshot,
                metric=f"beta_selection_{level_name}",
                value=str(peer["peer_id"]),
                unit="identifier",
                source_layer=EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
                source_ref=snapshot.sources[str(peer["source_key"])],
                notes="public 5Y beta and debt/equity peer observation used only by the hierarchical Beta stage",
                confidence=0.75,
            )
        )
    return tuple(records)


def _primary_collector(snapshot: SKHynixSnapshot):
    by_metric = {record.metric: record for record in _all_records(snapshot)}

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        rows = tuple(by_metric[metric] for metric in request.required_metrics)
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at=snapshot.as_of,
            records=rows,
            source_fingerprint=snapshot.raw_hash,
            document_ids=("SKHYNIX_2026H1_SOURCE_PACK",),
        )

    return collect


def _scanner_runner(context) -> ScannerFinding:
    evidence_by_scanner = {
        "CYCLE_NORMALIZATION": _eid("realized_price"),
        "COST_CURVE": _eid("cost_curve_position"),
        "INVENTORY": _eid("inventory"),
        "TRADE_FLOW": _eid("production"),
    }
    evidence_id = evidence_by_scanner[context.scanner_id]
    return ScannerFinding(
        scanner_id=context.scanner_id,
        status=ScannerFindingStatus.WARNING,
        summary=(
            f"{context.scanner_id} completed as a context-only check; unavailable cycle fields remain explicit NOT_DISCLOSED statuses and are not imputed"
        ),
        evidence_ids=(evidence_id,),
        verification_requests=(f"refresh {context.ledger.get(evidence_id).metric} when a primary source discloses it",),
        context_only=True,
    )


def _hypothesis(snapshot: SKHynixSnapshot, scenario: str) -> HypothesisRecord:
    support = tuple(_eid(f"model_{scenario.lower()}_fcff_year_{year}") for year in range(1, FORECAST_YEARS + 1))
    if scenario == "Down":
        statement = "Memory-cycle normalization can compress SK hynix FCFF materially from the current HBM-led peak state."
        kill = "sustained HBM pricing, mix and cash conversion remain above the down-cycle path across subsequent filings"
    elif scenario == "Core":
        statement = "HBM leadership persists while medium-term memory economics normalize toward a lower but still high cash-flow plateau."
        kill = "HBM qualification, pricing or utilization deteriorates enough to break the compiled medium-term FCFF path"
    else:
        statement = "A prolonged AI-memory shortage and execution of advanced-memory capacity can sustain exceptional FCFF through the forecast horizon."
        kill = "supply additions, qualification losses or pricing normalization invalidate the prolonged shortage path"
    return HypothesisRecord(
        id=f"H:SKHYNIX:{scenario}",
        statement=statement,
        causal_chain=(
            "AI-memory demand and memory-cycle conditions",
            "HBM mix, utilization and realized memory economics",
            "operating cash generation and reinvestment burden",
        ),
        supporting_evidence_ids=support,
        kill_conditions=(kill,),
        next_checks=("HBM4/HBM4E qualification and shipment mix", "DRAM/NAND pricing and inventory", "FCFF conversion and capex"),
    )


def _intelligence_officer(context, snapshot: SKHynixSnapshot) -> IntelligenceProposal:
    hypotheses = tuple(_hypothesis(snapshot, scenario) for scenario in SCENARIOS)
    linkage = ContextStrengthLinkage(
        id="CSL:SKHYNIX:AI_MEMORY_CAPACITY",
        external_change="AI accelerator deployments continue to raise demand for high-bandwidth memory while advanced-memory qualification and packaging remain supply constraints.",
        emergent_need="Customers need qualified high-bandwidth memory suppliers that can ship advanced generations at high utilization without losing yield or cash conversion.",
        company_strength="SK hynix has begun HBM4 mass shipments, reports full average utilization on its disclosed production-cost basis, and describes long-term agreements with roughly ten customers.",
        linkage_thesis="The demand bottleneck can reprice existing HBM qualification, customer access and operating capacity only to the extent that those strengths convert into durable FCFF rather than temporary cycle rents.",
        market_blind_spot="A single memory-cycle label can obscure the distinction between structurally constrained HBM economics and ordinary DRAM/NAND normalization.",
        value_capture_path="qualified HBM demand → utilization and product mix → operating margin and cash conversion → FCFF after reinvestment",
        causal_chain=(
            "AI-memory demand increases",
            "qualified HBM supply becomes the scarce capability",
            "SK hynix qualification and operating capacity absorb demand",
            "shipments and mix affect margin and cash conversion",
            "FCFF determines intrinsic enterprise value",
        ),
        supporting_evidence_ids=(
            _eid("hbm4_mass_shipments_started"),
            _eid("long_term_agreements_customer_count_approx"),
            _eid("utilization"),
            _eid("operating_cash_flow_h1_2026"),
        ),
        hypothesis_ids=tuple(item.id for item in hypotheses),
        recognition_triggers=("HBM4/HBM4E shipment ramp", "sustained high utilization with cash conversion", "customer agreement conversion into shipments"),
        kill_conditions=("HBM qualification or yield misses", "memory pricing and inventory normalize faster than the FCFF path", "capex burden absorbs incremental operating cash"),
        next_checks=("next quarterly HBM shipment disclosure", "inventory and pricing disclosure", "capex and free-cash-flow conversion"),
        confidence=0.70,
    )
    return IntelligenceProposal(
        hypotheses=hypotheses,
        requested_evidence=("future HBM ASP/mix", "inventory", "cash cost"),
        rationale="Evidence supports distinct Down/Core/Bull operating paths, while missing cycle variables remain explicit rather than imputed; numeric probability authority is withheld.",
        context_strength_linkage_decision=ContextStrengthLinkageDecision(linkages=(linkage,)),
    )


def _red_team_officer(context, hypotheses) -> RedTeamProposal:
    return RedTeamProposal(
        issues=(),
        counter_thesis=(
            "Current profitability may be an extreme peak-state observation; missing ASP, inventory and cash-cost disclosure prevents treating HBM strength as a calibrated long-run probability."
        ),
        requested_evidence=("memory ASP and inventory", "HBM qualification/ramp", "capex-to-FCFF conversion"),
    )


def _bridge_record(*, scenario: str, key: str, evidence_ids: tuple[str, ...], hypothesis_id: str, variable: AffectedVariable, direction: Direction, old_value: float, new_value: float, unit: str) -> BridgeRecord:
    return BridgeRecord(
        id=f"B:SKHYNIX:{scenario}:{key}",
        evidence_ids=evidence_ids,
        hypothesis_id=hypothesis_id,
        affected_variable=variable,
        direction=direction,
        old_value=old_value,
        new_value=new_value,
        unit=unit,
        rationale="proposal is source-labelled and must be recomputed by the deterministic Assumption Compiler before use",
        confidence=0.60,
        kill_condition="source revision or next primary filing invalidates the input",
        verification_event="next quarterly/annual filing or explicit source refresh",
        economic_path_id=f"skhynix:{scenario.lower()}:{key}",
    )


def _bridge_analyst(context, hypotheses, red_team) -> BridgeProposalBundle:
    drafts: list[BridgeDraft] = []
    for scenario in SCENARIOS:
        hid = f"H:SKHYNIX:{scenario}"
        for year in range(1, FORECAST_YEARS + 1):
            metric = f"model_{scenario.lower()}_fcff_year_{year}"
            value = float(context.ledger.get(_eid(metric)).value)
            key = f"fcff_year_{year}"
            drafts.append(
                BridgeDraft(
                    assumption_key=key,
                    scenario_id=scenario,
                    bridge=_bridge_record(
                        scenario=scenario,
                        key=key,
                        evidence_ids=(_eid(metric),),
                        hypothesis_id=hid,
                        variable=AffectedVariable.MARGIN,
                        direction=Direction.UNCHANGED,
                        old_value=value,
                        new_value=value,
                        unit="KRW_billion",
                    ),
                    canonical_unit="KRW_billion",
                    transform_id="identity_observation",
                    input_evidence_ids=(_eid(metric),),
                    min_value="0",
                )
            )
        for key, metric_suffix, unit, variable in (
            ("terminal_growth", "terminal_growth", "ratio", AffectedVariable.MARGIN),
            ("terminal_roic", "terminal_roic", "ratio", AffectedVariable.MARGIN),
            ("ownership", "ownership", "ratio", AffectedVariable.SEGMENT_VALUE),
            ("broad_cash_q2_2026", "broad_cash_q2_2026", "KRW_billion", AffectedVariable.NET_DEBT),
            ("h2_2026_fcff_underwrite", "h2_2026_fcff_underwrite", "KRW_billion", AffectedVariable.NET_DEBT),
            ("ads_issue_proceeds", "ads_issue_proceeds", "KRW_billion", AffectedVariable.NET_DEBT),
            ("kioxia_remaining_stake_underwrite", "kioxia_remaining_stake_underwrite", "KRW_billion", AffectedVariable.NET_DEBT),
            ("diluted_shares", "diluted_shares", "shares", AffectedVariable.SHARE_COUNT),
        ):
            metric = f"model_{scenario.lower()}_{metric_suffix}"
            value = float(context.ledger.get(_eid(metric)).value)
            drafts.append(
                BridgeDraft(
                    assumption_key=key,
                    scenario_id=scenario,
                    bridge=_bridge_record(
                        scenario=scenario,
                        key=key,
                        evidence_ids=(_eid(metric),),
                        hypothesis_id=hid,
                        variable=variable,
                        direction=Direction.UNCHANGED,
                        old_value=value,
                        new_value=value,
                        unit=unit,
                    ),
                    canonical_unit=unit,
                    transform_id="identity_observation",
                    input_evidence_ids=(_eid(metric),),
                    min_value="0" if key not in {"terminal_growth"} else None,
                    max_value="1" if key == "ownership" else None,
                )
            )

        debt_metric = f"model_{scenario.lower()}_borrowings_q2_2026"
        sign_metric = f"model_{scenario.lower()}_negative_one"
        debt_value = float(context.ledger.get(_eid(debt_metric)).value)
        drafts.append(
            BridgeDraft(
                assumption_key="borrowings_adjustment",
                scenario_id=scenario,
                bridge=_bridge_record(
                    scenario=scenario,
                    key="borrowings_adjustment",
                    evidence_ids=(_eid(debt_metric), _eid(sign_metric)),
                    hypothesis_id=hid,
                    variable=AffectedVariable.NET_DEBT,
                    direction=Direction.DOWN,
                    old_value=0.0,
                    new_value=-debt_value,
                    unit="KRW_billion",
                ),
                canonical_unit="KRW_billion",
                transform_id="product",
                input_evidence_ids=(_eid(debt_metric), _eid(sign_metric)),
                max_value="0",
            )
        )
    return BridgeProposalBundle(
        drafts=tuple(drafts),
        rationale=(
            "LLM-stage output is proposal-only. FCFF, terminal, capital structure and equity-adjustment proposals are all recompiled from Evidence; the announced but unsettled buyback is not committed."
        ),
    )


def _target_structure(snapshot: SKHynixSnapshot) -> LiveCapitalStructureObservation:
    risk = snapshot.risk
    return LiveCapitalStructureObservation(
        equity_weight=float(risk["target_equity_weight"]),
        debt_weight=float(risk["target_debt_weight"]),
        tax_rate=float(risk["tax_rate"]),
        method=TargetCapitalStructureMethod.LONG_RUN_POLICY,
        as_of=str(risk["as_of"]),
        source_refs=(snapshot.sources["underwriting"],),
        rationale="explicit long-run capital-structure underwrite retained as a risk-stage input; it is not inferred from target market value",
    )


def _beta_loader(snapshot: SKHynixSnapshot):
    def load(context) -> LiveBetaUniverse:
        levels: list[LiveBetaLevelObservation] = []
        for level in BetaLevelName:
            row = snapshot.risk["beta_levels"][level.value]
            source_ref = snapshot.sources[str(row["source_key"])]
            levels.append(
                LiveBetaLevelObservation(
                    level=level,
                    peers=(
                        LivePeerBetaObservation(
                            peer_id=str(row["peer_id"]),
                            levered_beta=float(row["levered_beta"]),
                            debt=float(row["debt_to_equity"]),
                            equity=1.0,
                            tax_rate=0.21,
                            benchmark_id="STOCKANALYSIS_US_MARKET_BETA_5Y",
                            return_frequency="vendor_5y_beta",
                            estimation_window_months=60,
                            as_of=str(snapshot.risk["as_of"]),
                            source_ref=source_ref,
                            estimation_method="StockAnalysis Beta (5Y), public vendor observation",
                        ),
                    ),
                    selection_rationale="peer selected for progressively closer semiconductor and memory-cycle systematic-risk exposure rather than valuation similarity",
                    selection_evidence_ids=(_eid(f"beta_selection_{level.value}"),),
                    risk_driver_features=(
                        ("memory pricing cycle", "capital intensity", "inventory cycle", "AI data-center demand")
                        if level is BetaLevelName.L4_ECONOMIC_TWINS
                        else ()
                    ),
                )
            )
        return LiveBetaUniverse(
            levels=tuple(levels),
            target_capital_structure=_target_structure(snapshot),
            universe_rationale="L1→L4 hierarchy narrows from broad semiconductor exposure to a memory economic twin while preserving one normalized public 5Y Beta convention",
            source_refs=tuple(snapshot.sources[str(snapshot.risk["beta_levels"][level.value]["source_key"])] for level in BetaLevelName),
        )
    return load


def _wacc_loader(snapshot: SKHynixSnapshot):
    def load(context) -> LiveWACCInputs:
        risk = snapshot.risk
        source_ref = snapshot.sources["underwriting"]
        return LiveWACCInputs(
            cash_flow_currency="KRW",
            risk_free_rate=RateObservation(float(risk["risk_free_rate"]), "KRW", str(risk["as_of"]), source_ref, "explicit KRW risk-free underwrite"),
            equity_risk_premium=RateObservation(float(risk["equity_risk_premium"]), "KRW", str(risk["as_of"]), source_ref, "explicit equity-risk-premium underwrite"),
            marginal_pre_tax_cost_of_debt=RateObservation(float(risk["pre_tax_cost_of_debt"]), "KRW", str(risk["as_of"]), source_ref, "explicit marginal KRW debt-cost underwrite"),
            target_capital_structure=_target_structure(snapshot),
            terminal_growth=float(snapshot.scenarios["Core"]["terminal_growth"]),
            terminal_roic=float(snapshot.payload["terminal_roic"]),
        )
    return load


def _valuation_plan_inputs(context) -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="diluted_shares",
        segment_bindings=(
            SegmentValueBinding(
                segment_id=SEGMENT_ID,
                asset_id="memory_operations",
                ownership_key="ownership",
                ev_to_equity_adjustment_key="broad_cash_q2_2026",
            ),
        ),
        parent_adjustments=(
            ParentAdjustmentPlan("q2_borrowings", "borrowings_adjustment"),
            ParentAdjustmentPlan("h2_2026_fcff", "h2_2026_fcff_underwrite"),
            ParentAdjustmentPlan("ads_issue_proceeds", "ads_issue_proceeds"),
            ParentAdjustmentPlan("kioxia_stake", "kioxia_remaining_stake_underwrite"),
        ),
    )


def _street_reports(snapshot: SKHynixSnapshot) -> tuple[StreetResearchReport, ...]:
    street = snapshot.street
    return (
        StreetResearchReport(
            broker="Investing.com consensus",
            analyst="consensus aggregate",
            published_date=str(street["as_of"]),
            target_price=float(street["consensus_target_price"]),
            target_price_currency="KRW",
            valuation_method="post-freeze consensus reference only",
            base_year="2026",
            estimates=(),
            source_ref=snapshot.sources["street"],
        ),
    )


def build_skhynix_live_primary_config(
    state_root: str | Path,
    *,
    run_id: str = "SKHYNIX-000660-20260829-CANONICAL",
    snapshot_path: str | Path | None = None,
) -> LivePrimaryRuntimeConfig:
    snapshot = load_skhynix_snapshot(snapshot_path)
    records = _all_records(snapshot)
    metrics = tuple(dict.fromkeys(record.metric for record in records))
    collector = LiveCollectorProvider(
        CollectorCapability(
            collector_id="skhynix-frozen-live-source-pack",
            source_id="KR_OPENDART",
            supported_metrics=metrics,
            jurisdictions=("KR",),
            implementation_ref="valuation_engine.skhynix_live_primary._primary_collector",
        ),
        _primary_collector(snapshot),
    )

    def resolver(request: CompanyResolutionRequest) -> ResolvedCompanyIdentity:
        if request.query not in {TICKER, "SK하이닉스", "SK hynix", TARGET_ID}:
            raise ValueError("SK hynix provider accepts only the SK hynix identity")
        return _identity(snapshot)

    def snapshot_loader(_: ResolvedCompanyIdentity) -> IndustryKnowledgeSnapshot:
        return _industry_snapshot(snapshot)

    def freshness_loader(_: ResolvedCompanyIdentity, industry: IndustryKnowledgeSnapshot) -> LiveFreshnessAssessment:
        return LiveFreshnessAssessment(
            checked_at=snapshot.as_of,
            findings=(WatchFinding(WatchStatus.CLEAN, "SKHYNIX_FROZEN_SOURCES", "2026H1 filing, Q2 results and declared underwriting snapshot are frozen at the run cutoff", (), False),),
            source_snapshot_hash=industry.snapshot_hash,
        )

    def decomposer(_: ResolvedCompanyIdentity, __: IndustryKnowledgeSnapshot) -> tuple[SegmentDescriptor, ...]:
        return (
            SegmentDescriptor(
                segment_id=SEGMENT_ID,
                name="Consolidated memory semiconductor operations",
                revenue_recognition="shipment and customer acceptance",
                price_formation="memory ASP, HBM qualification/mix and contract terms",
                asset_ownership="owned fabs, packaging and memory production assets",
                capital_intensity="high",
                regulation_intensity="medium",
                customer_structure="global AI accelerator, server, mobile and storage customers",
                reinvestment_model="high recurring process-node, fab and advanced-packaging capex",
                cashflow_duration="multi-year memory cycle with HBM structural overlays",
                evidence_ids=("E:SKHYNIX:SEGMENT",),
            ),
        )

    def router(_: ResolvedCompanyIdentity, segments: tuple[SegmentDescriptor, ...], __: IndustryKnowledgeSnapshot) -> tuple[IndustryDNAProfile, ...]:
        segment = segments[0]
        return (
            IndustryDNAProfile(
                segment_id=segment.segment_id,
                sector_adapter="semiconductor.memory",
                archetypes=(EconomicArchetype.COMMODITY_PRICE_TAKER,),
                revenue_recognition=segment.revenue_recognition,
                price_formation=segment.price_formation,
                asset_ownership=segment.asset_ownership,
                capital_intensity=segment.capital_intensity,
                regulation_intensity=segment.regulation_intensity,
                customer_structure=segment.customer_structure,
                reinvestment_model=segment.reinvestment_model,
                cashflow_duration=segment.cashflow_duration,
                evidence_keys=("E:SKHYNIX:SEGMENT", "E:SKHYNIX:INDUSTRY"),
            ),
        )

    providers = LivePrimaryProviders(
        company_resolver=resolver,
        industry_snapshot_loader=snapshot_loader,
        freshness_loader=freshness_loader,
        segment_decomposer=decomposer,
        industry_dna_router=router,
        collectors=(collector,),
        scanner_runners={scanner_id: _scanner_runner for scanner_id in MANDATORY_SCANNERS},
        intelligence_officer=lambda context: _intelligence_officer(context, snapshot),
        red_team_officer=_red_team_officer,
        bridge_analyst=_bridge_analyst,
        evaluator_registry_loader=live_fcff_dcf_registry_loader(
            registrations=(
                LiveDCFRegistration(
                    "commodity_price_taker",
                    "midcycle_price_volume_dcf",
                    "1",
                    FORECAST_YEARS,
                ),
            ),
            include_default_normalized_multiples=True,
        ),
        valuation_plan_inputs_loader=_valuation_plan_inputs,
        beta_loader=_beta_loader(snapshot),
        wacc_loader=_wacc_loader(snapshot),
        street_loader=lambda: _street_reports(snapshot),
        market_loader=lambda: MarketObservation(float(snapshot.market["price"]), str(snapshot.market["as_of"]), snapshot.sources["market"]),
    )
    required_keys = tuple(
        dict.fromkeys(
            (
                *(f"fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),
                "terminal_growth",
                "terminal_roic",
                "ownership",
                "broad_cash_q2_2026",
                "borrowings_adjustment",
                "h2_2026_fcff_underwrite",
                "ads_issue_proceeds",
                "kioxia_remaining_stake_underwrite",
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
        additional_required_evidence={SEGMENT_ID: metrics},
        method_choices=(SegmentMethodChoice(SEGMENT_ID, "commodity_price_taker", "midcycle_price_volume_dcf", "1"),),
        market_currency="KRW",
        initial_data={
            "data_cutoff": snapshot.cutoff,
            "underwriting_status": "SOURCE_BACKED_UNCALIBRATED_SCENARIOS",
            "probability_authority": "WITHHELD_PENDING_PRODUCTION_CALIBRATION_CERTIFICATE",
            "buyback_treatment": "ANNOUNCED_NOT_SETTLED_EXCLUDED_FROM_INTRINSIC_INPUTS",
        },
    )


def run_skhynix_live_primary(
    state_root: str | Path,
    *,
    run_id: str = "SKHYNIX-000660-20260829-CANONICAL",
    snapshot_path: str | Path | None = None,
):
    from .strict_live_runtime import run_prism

    return run_prism(
        build_skhynix_live_primary_config(
            state_root,
            run_id=run_id,
            snapshot_path=snapshot_path,
        )
    )
