from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EconomicArchetype(str, Enum):
    CONTRACTED_BACKLOG = "contracted_backlog"
    CAPACITY_MANUFACTURING = "capacity_manufacturing"
    RECURRING_SUBSCRIPTION = "recurring_subscription"
    TRANSACTION_MARKETPLACE = "transaction_marketplace"
    COMMODITY_PRICE_TAKER = "commodity_price_taker"
    PROCESS_SPREAD = "process_spread"
    REGULATED_RATE_BASE = "regulated_rate_base"
    ASSET_YIELD_NAV = "asset_yield_nav"
    FINANCIAL_BALANCE_SHEET = "financial_balance_sheet"
    PROBABILISTIC_PIPELINE = "probabilistic_pipeline"
    RESERVE_DEPLETION = "reserve_depletion"
    CONSUMER_UNIT_ECONOMICS = "consumer_unit_economics"
    PROJECT_FINANCE = "project_finance"
    METERED_USAGE_NETWORK = "metered_usage_network"
    IP_ROYALTY_LICENSING = "ip_royalty_licensing"
    HIT_DRIVEN_CONTENT = "hit_driven_content"
    ADVERTISING_ATTENTION = "advertising_attention"
    DESIGN_LED_PRODUCT = "design_led_product"
    AUM_FEE_ECONOMICS = "aum_fee_economics"


@dataclass(frozen=True)
class IndustryDNAProfile:
    segment_id: str
    sector_adapter: str
    archetypes: tuple[EconomicArchetype, ...]
    revenue_recognition: str
    price_formation: str
    asset_ownership: str
    capital_intensity: str
    regulation_intensity: str
    customer_structure: str
    reinvestment_model: str
    cashflow_duration: str
    evidence_keys: tuple[str, ...]

    def validate(self) -> None:
        if not self.segment_id or not self.sector_adapter:
            raise ValueError("segment_id and sector_adapter are required")
        if not self.archetypes:
            raise ValueError("at least one economic archetype is required")
        if not self.evidence_keys:
            raise ValueError("Industry DNA routing requires evidence")


@dataclass(frozen=True)
class ModuleComposition:
    common_core: tuple[str, ...]
    archetype_modules: tuple[str, ...]
    sector_adapter: str
    company_overlays: tuple[str, ...]
    allowed_valuation_methods: tuple[str, ...]


def compose_modules(profile: IndustryDNAProfile, overlays: tuple[str, ...] = ()) -> ModuleComposition:
    profile.validate()
    archetype_modules = tuple(a.value for a in profile.archetypes)

    methods: set[str] = set()
    if EconomicArchetype.CONTRACTED_BACKLOG in profile.archetypes:
        methods.update(
            (
                "backlog_burn_dcf",
                "normalized_dcf",
                "normalized_ebitda",
                "warranted_per",
            )
        )
    if EconomicArchetype.CAPACITY_MANUFACTURING in profile.archetypes:
        methods.update(("driver_dcf", "warranted_per"))
    if EconomicArchetype.RECURRING_SUBSCRIPTION in profile.archetypes:
        methods.update(("arr_fcf_dcf", "warranted_per"))
    if EconomicArchetype.TRANSACTION_MARKETPLACE in profile.archetypes:
        methods.update(("gmv_take_rate_dcf", "warranted_per"))
    if EconomicArchetype.COMMODITY_PRICE_TAKER in profile.archetypes:
        methods.update(("midcycle_price_volume_dcf", "normalized_multiple"))
    if EconomicArchetype.PROCESS_SPREAD in profile.archetypes:
        methods.update(("midcycle_spread_dcf", "normalized_multiple"))
    if EconomicArchetype.REGULATED_RATE_BASE in profile.archetypes:
        methods.update(("rate_base_roe", "ddm", "regulated_dcf"))
    if EconomicArchetype.ASSET_YIELD_NAV in profile.archetypes:
        methods.update(("nav", "ffo_multiple", "ddm"))
    if EconomicArchetype.FINANCIAL_BALANCE_SHEET in profile.archetypes:
        methods.update(("residual_income", "pb_roe", "ddm"))
    if EconomicArchetype.PROBABILISTIC_PIPELINE in profile.archetypes:
        methods.update(("rnpv",))
    if EconomicArchetype.RESERVE_DEPLETION in profile.archetypes:
        methods.update(("reserve_npv", "nav"))
    if EconomicArchetype.CONSUMER_UNIT_ECONOMICS in profile.archetypes:
        methods.update(("unit_economics_dcf", "warranted_per"))
    if EconomicArchetype.PROJECT_FINANCE in profile.archetypes:
        methods.update(("project_npv", "sotp"))
    if EconomicArchetype.METERED_USAGE_NETWORK in profile.archetypes:
        methods.update(("usage_driver_dcf", "warranted_per"))
    if EconomicArchetype.IP_ROYALTY_LICENSING in profile.archetypes:
        methods.update(("royalty_dcf", "warranted_per"))
    if EconomicArchetype.HIT_DRIVEN_CONTENT in profile.archetypes:
        methods.update(("cohort_npv", "pipeline_option_sotp"))
    if EconomicArchetype.ADVERTISING_ATTENTION in profile.archetypes:
        methods.update(("attention_monetization_dcf", "warranted_per"))
    if EconomicArchetype.DESIGN_LED_PRODUCT in profile.archetypes:
        methods.update(("product_driver_dcf", "warranted_per"))
    if EconomicArchetype.AUM_FEE_ECONOMICS in profile.archetypes:
        methods.update(("aum_fee_dcf", "warranted_per"))

    return ModuleComposition(
        common_core=(
            "industry_knowledge_freshness",
            "evidence_gate",
            "accounting_normalization",
            "hierarchical_beta",
            "wacc_validation",
            "upstream_funding",
            "scenario_distribution",
            "warranted_per_if_allowed",
            "double_count_audit",
            "intrinsic_value_freeze",
        ),
        archetype_modules=archetype_modules,
        sector_adapter=profile.sector_adapter,
        company_overlays=overlays,
        allowed_valuation_methods=tuple(sorted(methods)),
    )
