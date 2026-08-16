from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IndustryModel(str, Enum):
    ORDER_EQUIPMENT = "order_equipment"
    CONSTRUCTION_SHIPBUILDING = "construction_shipbuilding"
    COMMODITY_MATERIALS = "commodity_materials"
    ENERGY_INFRA = "energy_infrastructure"
    SOFTWARE_PLATFORM = "software_platform"
    CONSUMER_RETAIL = "consumer_retail"
    HOLDING_COMPANY = "holding_company"
    FINANCIALS = "financials"
    PHARMA_BIO = "pharma_bio"
    GENERIC = "generic"


KEYWORDS = {
    IndustryModel.ORDER_EQUIPMENT: ("장비", "수주잔고", "backlog", "equipment"),
    IndustryModel.CONSTRUCTION_SHIPBUILDING: ("건설", "조선", "진행률", "shipbuilding"),
    IndustryModel.COMMODITY_MATERIALS: ("소재", "폴리실리콘", "원재료", "commodity", "materials"),
    IndustryModel.ENERGY_INFRA: ("전력", "발전", "변압기", "터빈", "energy", "power"),
    IndustryModel.SOFTWARE_PLATFORM: ("소프트웨어", "플랫폼", "cloud", "software", "saas"),
    IndustryModel.CONSUMER_RETAIL: ("소비재", "유통", "retail", "consumer"),
    IndustryModel.HOLDING_COMPANY: ("홀딩스", "지주", "holding"),
    IndustryModel.FINANCIALS: ("은행", "보험", "증권", "financial"),
    IndustryModel.PHARMA_BIO: ("제약", "바이오", "pipeline", "clinical"),
}


def route_industry(description: str) -> IndustryModel:
    text = description.lower()
    if any(keyword.lower() in text for keyword in KEYWORDS[IndustryModel.HOLDING_COMPANY]):
        return IndustryModel.HOLDING_COMPANY
    scores = {}
    for model, keywords in KEYWORDS.items():
        if model is IndustryModel.HOLDING_COMPANY:
            continue
        scores[model] = sum(1 for keyword in keywords if keyword.lower() in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else IndustryModel.GENERIC


@dataclass(frozen=True)
class ModelSpec:
    industry: IndustryModel
    required_evidence: tuple[str, ...]
    primary_kpis: tuple[str, ...]
    allowed_methods: tuple[str, ...]
    scenario_variables: tuple[str, ...]
    double_count_traps: tuple[str, ...]
    required_kill_conditions: tuple[str, ...]


@dataclass(frozen=True)
class SegmentRoute:
    segment_id: str
    industry: IndustryModel
    model_method: str
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True)
class RoutingDecision:
    company_model: IndustryModel
    segments: tuple[SegmentRoute, ...]
    rationale_evidence_keys: tuple[str, ...]

    def validate(self) -> None:
        if not self.rationale_evidence_keys:
            raise ValueError("industry routing requires evidence")
        if self.company_model is IndustryModel.HOLDING_COMPANY and not self.segments:
            raise ValueError("holding company routing requires segment delegation")
        for segment in self.segments:
            spec = MODEL_SPECS.get(segment.industry)
            if spec is None or segment.model_method not in spec.allowed_methods:
                raise ValueError(f"method {segment.model_method} not allowed for {segment.industry.value}")
            if not segment.evidence_keys:
                raise ValueError(f"segment {segment.segment_id} requires routing evidence")


MODEL_SPECS = {
    IndustryModel.HOLDING_COMPANY: ModelSpec(
        IndustryModel.HOLDING_COMPANY, ("segments", "ownership", "net_debt"),
        ("segment_nav", "holding_net_debt", "nci"), ("sotp",),
        ("segment_value", "ownership", "net_debt", "discount"),
        ("subsidiary debt counted twice", "listed stake overlaps operating value"),
        ("segment ownership or debt cannot be verified",),
    ),
    IndustryModel.COMMODITY_MATERIALS: ModelSpec(
        IndustryModel.COMMODITY_MATERIALS, ("price", "capacity", "utilization", "unit_cost"),
        ("price", "volume", "unit_margin"), ("price_volume_margin_exit_multiple",),
        ("price", "quantity", "utilization", "margin", "multiple", "net_debt"),
        ("policy floor treated as enterprise ASP", "capacity option counted twice"),
        ("realized ASP or utilization contradicts the scenario",),
    ),
    IndustryModel.ORDER_EQUIPMENT: ModelSpec(
        IndustryModel.ORDER_EQUIPMENT, ("backlog", "contract_liability", "revenue_recognition"),
        ("backlog_conversion", "cash_prepayment", "normalized_margin"), ("backlog_normalized_ebitda_dcf",),
        ("quantity", "margin", "utilization", "multiple"), ("backlog and revenue option overlap",),
        ("backlog fails to convert or contract liabilities fall",),
    ),
    IndustryModel.ENERGY_INFRA: ModelSpec(
        IndustryModel.ENERGY_INFRA, ("project_capacity", "contract_term", "capex", "funding"),
        ("contracted_capacity", "time_to_power", "project_return"), ("asset_npv_sotp",),
        ("quantity", "margin", "funding_gap", "discount_rate"), ("project and parent value overlap",),
        ("project financing or binding contract fails",),
    ),
    IndustryModel.SOFTWARE_PLATFORM: ModelSpec(
        IndustryModel.SOFTWARE_PLATFORM, ("revenue", "retention", "gross_margin"),
        ("arr", "retention", "fcf_margin"), ("revenue_grossprofit_fcf",),
        ("quantity", "price", "margin", "discount_rate"), ("users and revenue counted separately",),
        ("retention or monetization deteriorates",),
    ),
    IndustryModel.FINANCIALS: ModelSpec(
        IndustryModel.FINANCIALS, ("book_value", "roe", "cost_of_equity"),
        ("roe", "credit_cost", "capital_ratio"), ("pb_roe_residual_income",),
        ("margin", "discount_rate", "segment_value"), ("net debt deducted from financials",),
        ("regulatory capital blocks distributions",),
    ),
    IndustryModel.PHARMA_BIO: ModelSpec(
        IndustryModel.PHARMA_BIO, ("pipeline", "trial_stage", "cash_burn"),
        ("stage_probability", "market_size", "cash_runway"), ("rnpv",),
        ("probability", "quantity", "margin", "discount_rate"), ("pipeline value counted in core and option",),
        ("trial failure or runway exhaustion",),
    ),
}


def oci_routing_decision() -> RoutingDecision:
    decision = RoutingDecision(
        IndustryModel.HOLDING_COMPANY,
        (
            SegmentRoute("polysilicon", IndustryModel.COMMODITY_MATERIALS, "price_volume_margin_exit_multiple", ("EV-OCI-ROUTE-1",)),
            SegmentRoute("wafer", IndustryModel.COMMODITY_MATERIALS, "price_volume_margin_exit_multiple", ("EV-OCI-ROUTE-2",)),
        ),
        ("EV-OCI-ROUTE-HOLDING",),
    )
    decision.validate()
    return decision
