from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import re
from statistics import fmean
from typing import Any

import yaml

from .collection_plan import CollectorCapability
from .context_strength_linkage import ContextStrengthLinkage, ContextStrengthLinkageDecision
from .dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
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
from .scanner_runtime import ScannerFinding, ScannerFindingStatus
from .scenario_binding import ScenarioBindingSpec
from .skhynix_beta_snapshot import (
    PEER_IDS,
    SKHynixBetaSnapshot,
    load_skhynix_beta_snapshot,
)
from .source_watch import WatchFinding, WatchStatus
from .street import StreetResearchReport
from .valuation_execution import ParentAdjustmentPlan
from .valuation_plan_compiler import CompanyValuationPlanInputs, SegmentMethodChoice, SegmentValueBinding


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_PATH = _REPO_ROOT / "config" / "skhynix_live_snapshot.yaml"
TICKER = "000660"
TARGET_ID = "KR:DART:00164779"
SEGMENT_ID = "memory"
SCENARIOS = ("Down", "Core", "Bull")
FORECAST_YEARS = 9
MANDATORY_SCANNERS = ("CYCLE_NORMALIZATION", "COST_CURVE", "INVENTORY", "TRADE_FLOW")
_REGISTERED_MARKET_SNAPSHOT_SHA256 = (
    "f6bcc877a33984cfe192967f5f426a5f7e2f93a5ac2dd4c1dadde6b44cb7f09d"
)
_REGISTERED_STREET_SOURCE_SHA256 = (
    "621f75776bf9c44f06f28884e473683ac2dbfe5b3fcd8afd73f2c158fa8f53f8"
)
_REGISTERED_STREET_RECORD_SHA256 = (
    "da275819b336fa27392ca6fdf40fefeb24d1bcb53fd70489ca737e97d9005eb9"
)
PEER_FILED_SHARE_COUNT_SHA256 = {
    "INTC": "4c7d76c3248b0122090ba718306ecc7e248c1d23f5e85fd70be2167a89245106",
    "AVGO": "9651d88f4da975242261e16bc2b90b8353891ad0f45fff6919ab1708433ee7f3",
    "MRVL": "316c3d17ec15dbf1ba51cfa0d9c578414324c82590611f4eb1403710b1aae264",
    "MU": "a95ee3f2b2c7597cf428d1cc77e6f6420f96b1783d8bbd9f524e43cb8b4f4650",
}


def _filed_share_count(text: str, *, peer_id: str) -> tuple[int, date]:
    count_match = re.search(
        r"(?:outstanding|was|were)\s+([0-9,]+(?:\.[0-9]+)?)\b",
        text,
        re.IGNORECASE,
    )
    date_match = re.search(
        r"as of ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})",
        text,
        re.IGNORECASE,
    )
    preamble_has_share_subject = bool(
        count_match is not None
        and re.search(
            r"\bnumber of\b.*\bshares\b",
            text[: count_match.start()],
            re.IGNORECASE,
        )
    )
    count_suffix = text[count_match.end():] if count_match is not None else ""
    suffix_names_shares = bool(
        count_match is not None
        and re.match(r"\s*(?:million\s+)?shares\b", count_suffix, re.IGNORECASE)
    )
    if (
        count_match is None
        or date_match is None
        or not (preamble_has_share_subject or suffix_names_shares)
    ):
        raise ValueError(f"SK hynix {peer_id} filed share-count text is malformed")
    count = Decimal(count_match.group(1).replace(",", ""))
    if re.match(r"\s*million\b", count_suffix, re.IGNORECASE):
        count *= Decimal("1000000")
    if count != count.to_integral_value():
        raise ValueError(f"SK hynix {peer_id} filed share count is not integral")
    return int(count), datetime.strptime(date_match.group(1), "%B %d, %Y").date()


def _peer_market_structure(
    risk: dict[str, Any],
    beta_snapshot: SKHynixBetaSnapshot,
) -> tuple[dict[str, float], float, float, tuple[str, ...]]:
    peer_rows = risk.get("peer_market_capital")
    if not isinstance(peer_rows, dict) or tuple(peer_rows) != PEER_IDS:
        raise ValueError("SK hynix peer market-capital set must be INTC/AVGO/MRVL/MU")
    ratios: dict[str, float] = {}
    debt_weights: list[float] = []
    source_refs: set[str] = set()
    for peer_id in PEER_IDS:
        row = peer_rows[peer_id]
        if not isinstance(row, dict):
            raise ValueError(f"SK hynix {peer_id} market-capital row is malformed")
        filed_text = str(row.get("filed_share_count_text", ""))
        payload_hash = sha256(filed_text.encode("utf-8")).hexdigest()
        if (
            payload_hash != row.get("filed_share_count_text_sha256")
            or payload_hash != PEER_FILED_SHARE_COUNT_SHA256[peer_id]
        ):
            raise ValueError(
                f"SK hynix {peer_id} filed share-count payload hash mismatch"
            )
        shares, shares_as_of = _filed_share_count(filed_text, peer_id=peer_id)
        estimate = beta_snapshot.estimate(peer_id)
        if (
            shares <= 0
            or shares_as_of > datetime.strptime(estimate.end_date, "%Y-%m-%d").date()
            or row.get("filing_source_ref") != estimate.capital_source_ref
        ):
            raise ValueError(f"SK hynix {peer_id} peer market-capital binding mismatch")
        market_equity = shares * estimate.ending_price
        debt_to_equity = estimate.debt / market_equity
        ratios[peer_id] = debt_to_equity
        debt_weights.append(estimate.debt / (estimate.debt + market_equity))
        source_refs.update((estimate.price_source_ref, estimate.capital_source_ref))
    debt_weight = fmean(debt_weights)
    return ratios, 1.0 - debt_weight, debt_weight, tuple(sorted(source_refs))


@dataclass(frozen=True)
class SKHynixSnapshot:
    payload: dict[str, Any]
    raw_hash: str
    beta_snapshot: SKHynixBetaSnapshot

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
    risk = payload.get("risk")
    if not isinstance(risk, dict):
        raise ValueError("SK hynix snapshot risk block must be a mapping")
    beta_path = (_REPO_ROOT / str(risk.get("beta_snapshot_path", ""))).resolve()
    if _REPO_ROOT.resolve() not in beta_path.parents:
        raise ValueError("SK hynix Beta snapshot must remain inside the repository")
    beta_snapshot = load_skhynix_beta_snapshot(beta_path)
    if beta_snapshot.raw_hash != risk.get("beta_snapshot_sha256"):
        raise ValueError("SK hynix Beta snapshot hash mismatch")
    if beta_snapshot.as_of != str(risk.get("as_of")):
        raise ValueError("SK hynix Beta snapshot as-of mismatch")

    market = payload.get("market")
    if not isinstance(market, dict):
        raise ValueError("SK hynix market block must be a mapping")
    market_path = (_REPO_ROOT / str(market.get("snapshot_path", ""))).resolve()
    if _REPO_ROOT.resolve() not in market_path.parents:
        raise ValueError("SK hynix market snapshot must remain inside the repository")
    market_raw = market_path.read_bytes()
    market_snapshot_hash = sha256(market_raw).hexdigest()
    if market_snapshot_hash != market.get("snapshot_sha256"):
        raise ValueError("SK hynix market snapshot hash mismatch")
    if market_snapshot_hash != _REGISTERED_MARKET_SNAPSHOT_SHA256:
        raise ValueError("SK hynix market snapshot is not independently registered")
    market_snapshot = json.loads(market_raw)
    frozen_response = str(market_snapshot.get("raw_response", ""))
    if sha256(frozen_response.encode("utf-8")).hexdigest() != market_snapshot.get(
        "raw_response_sha256"
    ):
        raise ValueError("SK hynix market raw-response hash mismatch")
    response = json.loads(frozen_response)
    price_rows = tuple(
        item
        for item in response.get("Values", ())
        if isinstance(item, dict) and item.get("name") == "last"
    )
    if len(price_rows) != 1:
        raise ValueError("SK hynix issuer market snapshot has no unique last price")
    formats = price_rows[0].get("Formats", ())
    if not formats or float(formats[0]["rawValue"]) != float(market.get("price")):
        raise ValueError("SK hynix market price does not match the frozen issuer feed")
    dated_record = str(market_snapshot.get("dated_source_record", ""))
    if sha256(dated_record.encode("utf-8")).hexdigest() != market_snapshot.get(
        "dated_source_record_sha256"
    ):
        raise ValueError("SK hynix dated market-source record hash mismatch")
    dated_match = re.fullmatch(
        r"SK hynix 000660 \| ([0-9,]+) KRW \| As of "
        r"([0-9]{2}:[0-9]{2}) ([A-Za-z]+ [0-9]{2}, [0-9]{4})",
        dated_record,
    )
    if dated_match is None:
        raise ValueError("SK hynix dated market-source record is malformed")
    dated_price = int(dated_match.group(1).replace(",", ""))
    dated_as_of = datetime.strptime(
        dated_match.group(3), "%B %d, %Y"
    ).date().isoformat()
    if (
        dated_as_of != market.get("as_of")
        or dated_price != market.get("price")
        or market_snapshot.get("price") != market.get("price")
        or market_snapshot.get("issuer_source_ref")
        != payload.get("sources", {}).get("market")
        or market_snapshot.get("dated_source_ref")
        != payload.get("sources", {}).get("market")
    ):
        raise ValueError("SK hynix market observation binding mismatch")

    street = payload.get("street")
    source_ref = payload.get("sources", {}).get("street")
    if not isinstance(street, dict) or not isinstance(source_ref, str):
        raise ValueError("SK hynix Street structured record is missing")
    source_hash = str(street.get("source_sha256", ""))
    structured_street_record = {
        "broker": "Samsung Securities",
        "published_date": str(street.get("as_of", "")),
        "target_price": int(street.get("consensus_target_price", 0)),
        "target_price_currency": "KRW",
        "report_count": int(street.get("report_count", 0)),
        "median_target_price": int(street.get("median_target_price", 0)),
        "min_target_price": int(street.get("min_target_price", 0)),
        "max_target_price": int(street.get("max_target_price", 0)),
        "source_ref": source_ref,
        "source_sha256": source_hash,
    }
    structured_street_hash = sha256(
        json.dumps(
            structured_street_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        source_hash != _REGISTERED_STREET_SOURCE_SHA256
        or structured_street_hash != _REGISTERED_STREET_RECORD_SHA256
    ):
        raise ValueError("SK hynix Street record is not independently registered")

    official_facts = payload.get("official_facts", {})
    income_tax = float(official_facts["income_tax_expense_h1_2026"][0])
    pre_tax_income = float(official_facts["profit_before_income_tax_h1_2026"][0])
    _, peer_equity_weight, peer_debt_weight, _ = _peer_market_structure(
        risk,
        beta_snapshot,
    )
    expected_risk = (
        peer_equity_weight,
        peer_debt_weight,
        income_tax / pre_tax_income,
    )
    recorded_risk = (
        float(risk.get("target_equity_weight", -1)),
        float(risk.get("target_debt_weight", -1)),
        float(risk.get("tax_rate", -1)),
    )
    if any(
        not isclose(actual, expected, rel_tol=0.0, abs_tol=1e-15)
        for actual, expected in zip(recorded_risk, expected_risk)
    ):
        raise ValueError("SK hynix filed capital-structure or tax binding mismatch")

    snapshot = SKHynixSnapshot(
        payload=payload,
        raw_hash=sha256(raw).hexdigest(),
        beta_snapshot=beta_snapshot,
    )
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
    ids = ("E:SKHYNIX:SEGMENT", "E:SKHYNIX:INDUSTRY")
    lineages = tuple(
        AuthoritativeEvidenceLineage(
            evidence_id=eid,
            target_id=TARGET_ID,
            source_id="KR_OPENDART",
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
        source_ids=("KR_OPENDART",),
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
    layer: EvidenceSourceLayer,
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
        source_layer=layer,
        effective_date=snapshot.as_of,
        observed_date=snapshot.as_of,
        source_name="SK hynix frozen LIVE source pack",
        source_ref=source_ref,
        source_grade="B" if layer is EvidenceSourceLayer.ANALYST_UNDERWRITING else "A",
        confidence=confidence,
        segment=SEGMENT_ID,
        notes=notes,
    )


def _all_records(snapshot: SKHynixSnapshot) -> tuple[EvidenceRecord, ...]:
    sources = snapshot.sources
    rows: list[EvidenceRecord] = []
    q2_metrics = {"hbm4_mass_shipments_started", "long_term_agreements_customer_count_approx"}
    pnt7_metrics = {"pnt7_board_approved_investment"}
    treasury_metrics = {"issued_common_shares_pre_buyback", "planned_buyback_shares", "planned_buyback_cash"}
    for metric, (value, unit) in snapshot.official_facts.items():
        source_ref = (
            sources["q2_results"] if metric in q2_metrics
            else sources["pnt7_filing"] if metric in pnt7_metrics
            else sources["treasury_filing"] if metric in treasury_metrics
            else sources["half_year_filing"]
        )
        rows.append(
            _record(
                snapshot,
                metric=metric,
                value=value,
                unit=str(unit),
                layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                source_ref=source_ref,
                notes="official filing/result observation; NOT_DISCLOSED remains an explicit status and is never imputed",
                confidence=1.0,
            )
        )

    official_adjustments = {"broad_cash_q2_2026", "borrowings_q2_2026", "ads_issue_proceeds", "diluted_shares"}
    for scenario in SCENARIOS:
        lower = scenario.lower()
        scenario_row = snapshot.scenarios[scenario]
        for year, value in enumerate(scenario_row["fcff_krw_billion"], start=1):
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{lower}_fcff_year_{year}",
                    value=value,
                    unit="KRW_billion",
                    layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    source_ref=sources["q2_results"],
                    notes=(
                        "analyst FCFF judgment anchored to, but not stated by, the Q2 results; "
                        "deterministic Assumption Compiler must rebind it"
                    ),
                    confidence=0.60,
                )
            )
        for metric, value, unit, confidence in (
            ("terminal_growth", scenario_row["terminal_growth"], "ratio", 0.55),
            ("terminal_roic", snapshot.payload["terminal_roic"], "ratio", 0.50),
            ("ownership", 1.0, "ratio", 0.95),
        ):
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{lower}_{metric}",
                    value=value,
                    unit=unit,
                    layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    source_ref=(
                        sources["q2_results"]
                        if metric == "terminal_growth"
                        else sources["half_year_filing"]
                    ),
                    notes=(
                        "analyst judgment anchored to, but not stated by, the linked issuer "
                        "source; subject to deterministic scenario/terminal consistency checks"
                    ),
                    confidence=confidence,
                )
            )
        for metric, (value, unit) in snapshot.adjustments.items():
            layer = EvidenceSourceLayer.REALIZED_OR_FILING if metric in official_adjustments else EvidenceSourceLayer.ANALYST_UNDERWRITING
            if metric in {"broad_cash_q2_2026", "borrowings_q2_2026", "ads_issue_proceeds"}:
                source_ref = sources["half_year_filing"]
            elif metric == "diluted_shares":
                source_ref = sources["treasury_filing"]
            elif metric == "h2_2026_fcff_underwrite":
                source_ref = sources["q2_results"]
            else:
                source_ref = sources["half_year_filing"]
            rows.append(
                _record(
                    snapshot,
                    metric=f"model_{lower}_{metric}",
                    value=value,
                    unit=str(unit),
                    layer=layer,
                    source_ref=source_ref,
                    notes=(
                        "official/derived balance-sheet or financing observation"
                        if layer is EvidenceSourceLayer.REALIZED_OR_FILING
                        else (
                            "analyst underwriting judgment anchored to, but not stated by, "
                            "the linked issuer source; unsettled announced buyback is excluded"
                        )
                    ),
                    confidence=0.95 if layer is EvidenceSourceLayer.REALIZED_OR_FILING else 0.60,
                )
            )

    for level_name, peer in snapshot.risk["beta_levels"].items():
        estimate = snapshot.beta_snapshot.estimate(str(peer["peer_id"]))
        rows.append(
            _record(
                snapshot,
                metric=f"beta_selection_{level_name}",
                value=str(peer["peer_id"]),
                unit="identifier",
                layer=EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
                source_ref=estimate.price_source_ref,
                notes=(
                    "five-year weekly-return Beta replayed from a frozen Nasdaq "
                    f"series ({estimate.observations} observations; {estimate.series_hash}); "
                    "debt-equity replayed from the linked SEC filing"
                ),
                confidence=0.90,
            )
        )
    return tuple(rows)


def _primary_collector(snapshot: SKHynixSnapshot):
    by_metric = {record.metric: record for record in _all_records(snapshot)}

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at=snapshot.as_of,
            records=tuple(by_metric[metric] for metric in request.required_metrics),
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
        summary=f"{context.scanner_id}: missing cycle variables stay explicit NOT_DISCLOSED and are not imputed",
        evidence_ids=(evidence_id,),
        verification_requests=(f"refresh {context.ledger.get(evidence_id).metric} when primary disclosure appears",),
        context_only=True,
    )


def _hypothesis(scenario: str) -> HypothesisRecord:
    lower = scenario.lower()
    support = tuple(_eid(f"model_{lower}_fcff_year_{year}") for year in range(1, FORECAST_YEARS + 1))
    statement, kill = {
        "Down": (
            "Memory-cycle normalization can compress SK hynix FCFF materially from the current HBM-led peak state.",
            "sustained HBM pricing, mix and cash conversion remain above the down-cycle path",
        ),
        "Core": (
            "HBM leadership persists while medium-term memory economics normalize toward a lower but still high cash-flow plateau.",
            "HBM qualification, pricing or utilization deteriorates enough to break the compiled path",
        ),
        "Bull": (
            "A prolonged AI-memory shortage and advanced-memory execution can sustain exceptional FCFF through the forecast horizon.",
            "supply additions, qualification losses or pricing normalization invalidate the prolonged shortage path",
        ),
    }[scenario]
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
    hypotheses = tuple(_hypothesis(scenario) for scenario in SCENARIOS)
    linkage = ContextStrengthLinkage(
        id="CSL:SKHYNIX:AI_MEMORY_CAPACITY",
        external_change="AI accelerator deployments continue to raise demand for high-bandwidth memory while advanced-memory qualification and packaging remain constrained.",
        emergent_need="Customers need qualified HBM suppliers that can ship advanced generations at high utilization without losing yield or cash conversion.",
        company_strength="SK hynix has begun HBM4 mass shipments, reports full average utilization on its disclosed production-cost basis, and describes long-term agreements with roughly ten customers.",
        linkage_thesis="Demand bottlenecks can reprice existing HBM qualification, customer access and capacity only when those strengths convert into durable FCFF rather than temporary cycle rents.",
        market_blind_spot="A single memory-cycle label can obscure the difference between structurally constrained HBM economics and ordinary DRAM/NAND normalization.",
        value_capture_path="qualified HBM demand → utilization/product mix → margin/cash conversion → FCFF after reinvestment",
        causal_chain=(
            "AI-memory demand increases",
            "qualified HBM supply becomes scarce",
            "SK hynix qualification/capacity absorb demand",
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
        recognition_triggers=("HBM4/HBM4E shipment ramp", "sustained high utilization with cash conversion", "agreement conversion into shipments"),
        kill_conditions=("HBM qualification/yield misses", "pricing and inventory normalize faster than FCFF", "capex absorbs incremental cash"),
        next_checks=("next HBM shipment disclosure", "inventory/pricing disclosure", "capex and FCFF conversion"),
        confidence=0.70,
    )
    return IntelligenceProposal(
        hypotheses=hypotheses,
        requested_evidence=("future HBM ASP/mix", "inventory", "cash cost"),
        rationale="Evidence supports distinct scenario proposals while missing cycle variables remain explicit; numeric probability authority is withheld.",
        context_strength_linkage_decision=ContextStrengthLinkageDecision(linkages=(linkage,)),
    )


def _red_team_officer(context, hypotheses) -> RedTeamProposal:
    return RedTeamProposal(
        issues=(),
        counter_thesis="Current profitability may be an extreme peak-state observation; missing ASP, inventory and cash-cost disclosure prevents calibrated long-run probability claims.",
        requested_evidence=("memory ASP and inventory", "HBM qualification/ramp", "capex-to-FCFF conversion"),
    )


def _bridge_record(
    *,
    scenario: str,
    key: str,
    evidence_ids: tuple[str, ...],
    variable: AffectedVariable,
    direction: Direction,
    old_value: float,
    new_value: float,
    unit: str,
) -> BridgeRecord:
    return BridgeRecord(
        id=f"B:SKHYNIX:{scenario}:{key}",
        evidence_ids=evidence_ids,
        hypothesis_id=f"H:SKHYNIX:{scenario}",
        affected_variable=variable,
        direction=direction,
        old_value=old_value,
        new_value=new_value,
        unit=unit,
        rationale="proposal-only input; deterministic Assumption Compiler recomputes before commitment",
        confidence=0.60,
        kill_condition="source revision or next primary filing invalidates the input",
        verification_event="next quarterly/annual filing or explicit source refresh",
        economic_path_id=f"skhynix:{scenario.lower()}:{key}",
    )


def _identity_draft(context, *, scenario: str, key: str, metric: str, unit: str, variable: AffectedVariable, min_value: str | None = None, max_value: str | None = None) -> BridgeDraft:
    evidence_id = _eid(f"model_{scenario.lower()}_{metric}")
    value = float(context.ledger.get(evidence_id).value)
    return BridgeDraft(
        assumption_key=key,
        scenario_id=scenario,
        bridge=_bridge_record(
            scenario=scenario,
            key=key,
            evidence_ids=(evidence_id,),
            variable=variable,
            direction=Direction.UNCHANGED,
            old_value=value,
            new_value=value,
            unit=unit,
        ),
        canonical_unit=unit,
        transform_id="identity_observation",
        input_evidence_ids=(evidence_id,),
        min_value=min_value,
        max_value=max_value,
    )


def _bridge_analyst(context, hypotheses, red_team) -> BridgeProposalBundle:
    drafts: list[BridgeDraft] = []
    common = (
        ("terminal_growth", "terminal_growth", "ratio", AffectedVariable.MARGIN, None, None),
        ("terminal_roic", "terminal_roic", "ratio", AffectedVariable.MARGIN, "0", None),
        ("ownership", "ownership", "ratio", AffectedVariable.SEGMENT_VALUE, "0", "1"),
        ("broad_cash_q2_2026", "broad_cash_q2_2026", "KRW_billion", AffectedVariable.NET_DEBT, "0", None),
        ("h2_2026_fcff_underwrite", "h2_2026_fcff_underwrite", "KRW_billion", AffectedVariable.NET_DEBT, "0", None),
        ("ads_issue_proceeds", "ads_issue_proceeds", "KRW_billion", AffectedVariable.NET_DEBT, "0", None),
        ("kioxia_remaining_stake_underwrite", "kioxia_remaining_stake_underwrite", "KRW_billion", AffectedVariable.NET_DEBT, "0", None),
        ("diluted_shares", "diluted_shares", "shares", AffectedVariable.SHARE_COUNT, "0", None),
    )
    for scenario in SCENARIOS:
        for year in range(1, FORECAST_YEARS + 1):
            drafts.append(
                _identity_draft(
                    context,
                    scenario=scenario,
                    key=f"fcff_year_{year}",
                    metric=f"fcff_year_{year}",
                    unit="KRW_billion",
                    variable=AffectedVariable.MARGIN,
                    min_value="0",
                )
            )
        for key, metric, unit, variable, min_value, max_value in common:
            drafts.append(
                _identity_draft(
                    context,
                    scenario=scenario,
                    key=key,
                    metric=metric,
                    unit=unit,
                    variable=variable,
                    min_value=min_value,
                    max_value=max_value,
                )
            )
        debt_id = _eid(f"model_{scenario.lower()}_borrowings_q2_2026")
        sign_id = _eid(f"model_{scenario.lower()}_negative_one")
        debt = float(context.ledger.get(debt_id).value)
        drafts.append(
            BridgeDraft(
                assumption_key="borrowings_adjustment",
                scenario_id=scenario,
                bridge=_bridge_record(
                    scenario=scenario,
                    key="borrowings_adjustment",
                    evidence_ids=(debt_id, sign_id),
                    variable=AffectedVariable.NET_DEBT,
                    direction=Direction.DOWN,
                    old_value=0.0,
                    new_value=-debt,
                    unit="KRW_billion",
                ),
                canonical_unit="KRW_billion",
                transform_id="product",
                input_evidence_ids=(debt_id, sign_id),
                max_value="0",
            )
        )
    return BridgeProposalBundle(
        drafts=tuple(drafts),
        rationale="LLM stage proposes only; deterministic compiler owns FCFF, terminal, capital-structure and equity-adjustment commitments. Announced unsettled buyback is excluded.",
    )


def _target_structure(snapshot: SKHynixSnapshot) -> LiveCapitalStructureObservation:
    risk = snapshot.risk
    _, equity_weight, debt_weight, peer_source_refs = _peer_market_structure(
        risk,
        snapshot.beta_snapshot,
    )
    return LiveCapitalStructureObservation(
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        tax_rate=float(risk["tax_rate"]),
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of=snapshot.beta_snapshot.as_of,
        source_refs=tuple(
            sorted((*peer_source_refs, snapshot.sources["half_year_filing"]))
        ),
        rationale=(
            "equal-weighted peer debt/(debt+market-equity) structure using frozen "
            "Nasdaq closes and latest filed shares; target current market "
            "capitalization is not used; tax is the target's filed H1 effective rate"
        ),
    )


def _beta_loader(snapshot: SKHynixSnapshot):
    def load(context) -> LiveBetaUniverse:
        peer_ratios, _, _, _ = _peer_market_structure(
            snapshot.risk,
            snapshot.beta_snapshot,
        )
        levels: list[LiveBetaLevelObservation] = []
        for level in BetaLevelName:
            row = snapshot.risk["beta_levels"][level.value]
            estimate = snapshot.beta_snapshot.estimate(str(row["peer_id"]))
            levels.append(
                LiveBetaLevelObservation(
                    level=level,
                    peers=(
                        LivePeerBetaObservation(
                            peer_id=str(row["peer_id"]),
                            levered_beta=estimate.beta,
                            debt=peer_ratios[estimate.peer_id],
                            equity=1.0,
                            tax_rate=0.21,
                            benchmark_id="NASDAQ_COMP_5Y_WEEKLY",
                            return_frequency="weekly",
                            estimation_window_months=60,
                            as_of=estimate.end_date,
                            source_ref=estimate.price_source_ref,
                            beta_standard_error=estimate.standard_error,
                            estimation_method=(
                                "5Y weekly close-to-close OLS with intercept; "
                                f"frozen series {estimate.series_hash}"
                            ),
                        ),
                    ),
                    selection_rationale="progressively closer semiconductor and memory-cycle systematic-risk exposure, not valuation similarity",
                    selection_evidence_ids=(_eid(f"beta_selection_{level.value}"),),
                    risk_driver_features=("memory pricing cycle", "capital intensity", "inventory cycle", "AI data-center demand") if level is BetaLevelName.L4_ECONOMIC_TWINS else (),
                )
            )
        return LiveBetaUniverse(
            levels=tuple(levels),
            target_capital_structure=_target_structure(snapshot),
            universe_rationale="L1→L4 narrows from broad semiconductor exposure to a memory economic twin under one five-year weekly exchange-return convention",
            source_refs=tuple(
                estimate.price_source_ref
                for estimate in snapshot.beta_snapshot.estimates
            )
            + (snapshot.beta_snapshot.benchmark_source_ref,)
            + tuple(
                estimate.capital_source_ref
                for estimate in snapshot.beta_snapshot.estimates
            ),
        )

    return load


def _wacc_loader(snapshot: SKHynixSnapshot):
    def load(context) -> LiveWACCInputs:
        risk = snapshot.risk
        sources = snapshot.sources
        return LiveWACCInputs(
            cash_flow_currency="KRW",
            risk_free_rate=RateObservation(
                float(risk["risk_free_rate"]),
                "KRW",
                str(risk["as_of"]),
                sources["risk_free"],
                "Korea Ministry of Finance and Economy 5-year Treasury bond yield",
            ),
            equity_risk_premium=RateObservation(
                float(risk["equity_risk_premium"]),
                "KRW",
                str(risk["as_of"]),
                sources["equity_risk_premium"],
                "Damodaran South Korea total equity risk premium",
            ),
            marginal_pre_tax_cost_of_debt=RateObservation(
                float(risk["pre_tax_cost_of_debt"]),
                "KRW",
                str(risk["as_of"]),
                sources["debt_cost"],
                "Korea Ministry of Finance and Economy AA- 3-year corporate bond yield",
            ),
            target_capital_structure=_target_structure(snapshot),
            terminal_growth=float(snapshot.scenarios["Core"]["terminal_growth"]),
            terminal_roic=float(snapshot.payload["terminal_roic"]),
        )

    return load


def _valuation_plan_inputs(context) -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW",
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
            broker="Samsung Securities",
            analyst="Jongwook Lee and Kyoungbeen Kim",
            published_date=str(street["as_of"]),
            target_price=float(street["consensus_target_price"]),
            target_price_currency="KRW",
            valuation_method="post-freeze broker reference only",
            base_year="2026",
            estimates=(),
            source_ref=snapshot.sources["street"],
            aggregate_report_count=int(street["report_count"]),
            aggregate_median_target_price=float(street["median_target_price"]),
            aggregate_min_target_price=float(street["min_target_price"]),
            aggregate_max_target_price=float(street["max_target_price"]),
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
            raise ValueError("SK hynix provider accepts only SK hynix identity")
        return _identity(snapshot)

    def freshness_loader(_: ResolvedCompanyIdentity, industry: IndustryKnowledgeSnapshot) -> LiveFreshnessAssessment:
        return LiveFreshnessAssessment(
            checked_at=snapshot.as_of,
            findings=(
                WatchFinding(
                    WatchStatus.CLEAN,
                    "SKHYNIX_FROZEN_SOURCES",
                    "2026H1 filing, Q2 results and declared underwriting snapshot frozen at the run cutoff",
                    (),
                    False,
                ),
            ),
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
                segment_id=SEGMENT_ID,
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
        industry_snapshot_loader=lambda _: _industry_snapshot(snapshot),
        freshness_loader=freshness_loader,
        segment_decomposer=decomposer,
        industry_dna_router=router,
        collectors=(collector,),
        scanner_runners={scanner_id: _scanner_runner for scanner_id in MANDATORY_SCANNERS},
        intelligence_officer=lambda context: _intelligence_officer(context, snapshot),
        red_team_officer=_red_team_officer,
        bridge_analyst=_bridge_analyst,
        evaluator_registry_loader=live_fcff_dcf_registry_loader(
            registrations=(LiveDCFRegistration("commodity_price_taker", "midcycle_price_volume_dcf", "1", FORECAST_YEARS),),
            include_default_normalized_multiples=True,
        ),
        valuation_plan_inputs_loader=_valuation_plan_inputs,
        beta_loader=_beta_loader(snapshot),
        wacc_loader=_wacc_loader(snapshot),
        street_loader=lambda: _street_reports(snapshot),
        market_loader=lambda: MarketObservation(
            float(snapshot.market["price"]),
            str(snapshot.market["as_of"]),
            snapshot.sources["market"],
        ),
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
