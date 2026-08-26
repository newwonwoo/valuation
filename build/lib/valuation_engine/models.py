from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceLayer(str, Enum):
    REALIZED = "realized_or_filing"
    COMPANY_PLAN = "company_official_plan"
    POLICY = "policy_primary_source"
    EXTERNAL_REFERENCE = "external_reference"
    MODEL_ASSUMPTION = "model_assumption"
    MODEL_OUTPUT = "model_output"
    MARKET_COMPARISON = "market_comparison"


@dataclass(frozen=True)
class Evidence:
    key: str
    value: Any
    source_layer: SourceLayer
    source: str
    as_of: str | None = None
    confidence: float = 1.0
    note: str = ""


@dataclass(frozen=True)
class Assumption:
    key: str
    value: float
    unit: str
    source_layer: SourceLayer = SourceLayer.MODEL_ASSUMPTION
    rationale: str = ""
    evidence_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float
    poly_asp_usd_per_kg: float
    poly_cash_cost_usd_per_kg: float
    poly_other_cost_usd_per_kg: float
    poly_capacity_kmt: float
    poly_utilization: float
    poly_multiple: float
    wafer_capacity_gw: float
    wafer_utilization: float
    wafer_ebitda_usd_per_w: float
    wafer_multiple: float
    wafer_economic_share: float
    fx_krw_per_usd: float
    discount_rate: float
    terminal_years: float
    net_debt_trn_krw: float
    other_business_pv_trn_krw: float


@dataclass(frozen=True)
class ScenarioValue:
    name: str
    probability: float
    poly_ebitda_trn: float
    wafer_ebitda_trn: float
    terminal_ev_trn: float
    terminal_core_equity_trn: float
    pv_core_equity_trn: float
    total_equity_trn: float
    fair_value_per_share: float


@dataclass
class ValuationResult:
    scenarios: list[ScenarioValue]
    expected_equity_trn: float
    expected_value_per_share: float
    audit: dict[str, Any] = field(default_factory=dict)
