#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.continuous_financial_path_probability import (
    ContinuousDriverDependence,
    ContinuousDriverPosterior,
    ScenarioFinancialPath,
)
from valuation_engine.continuous_predictive_weight import ContinuousWeightPolicy
from valuation_engine.probability_engine_v3 import ProbabilityEngineV3Result, ProbabilityEngineV3Spec


FORBIDDEN_FIELD_TOKENS = (
    "price",
    "market",
    "target",
    "value",
    "valuation",
    "intrinsic",
    "return",
    "entry",
    "upside",
)


def main() -> int:
    path = ROOT / "config" / "probability_engine_v3_policy.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("version") != "3.2":
        raise ValueError("probability engine v3 policy version drift")

    isolation = payload.get("probability_value_isolation") or {}
    if isolation.get("probability_contract") != "evidence_only":
        raise ValueError("probability contract must remain evidence-only")
    if isolation.get("valuation_binding_stage") != "post_probability_freeze_only":
        raise ValueError("valuation binding must occur only after probability freeze")
    if isolation.get("probability_hash_depends_on_valuation_inputs") is not False:
        raise ValueError("probability hashes must not depend on valuation inputs")
    forbidden_domains = set(isolation.get("forbidden_input_domains") or ())
    required_forbidden_domains = {
        "current_market_price",
        "target_price",
        "scenario_intrinsic_value",
        "expected_value",
        "valuation_gap",
        "return_target",
        "entry_price",
    }
    if forbidden_domains != required_forbidden_domains:
        raise ValueError("probability forbidden input domains drifted")

    for cls in (
        ProbabilityEngineV3Spec,
        ProbabilityEngineV3Result,
        ContinuousDriverPosterior,
        ContinuousDriverDependence,
        ScenarioFinancialPath,
    ):
        for item in fields(cls):
            lowered = item.name.lower()
            if any(token in lowered for token in FORBIDDEN_FIELD_TOKENS):
                raise ValueError(f"probability contract leaked valuation field: {cls.__name__}.{item.name}")

    gates = payload.get("hard_integrity_gates") or {}
    required = {
        "first_seen_at": "required",
        "publication_timestamp_cutoff": "required",
        "duplicate_event_identity": "forbidden",
        "outcome_leakage": "forbidden",
        "source_traceability": "required",
        "period_unit_consistency": "required",
    }
    if gates != required:
        raise ValueError("probability engine v3 hard gates must remain data-integrity only")

    continuous = payload.get("continuous_predictive_weight") or {}
    for key in (
        "brier_skill_zero_action",
        "confidence_interval_crosses_zero_action",
        "missing_oos_action",
    ):
        action = str(continuous.get(key, "")).lower()
        if "block" in action and "not_block" not in action:
            raise ValueError(f"{key} must not become a hard statistical gate")
    policy = ContinuousWeightPolicy(
        minimum_weight=__import__("decimal").Decimal(str(continuous["minimum_weight"])),
        skill_temperature=__import__("decimal").Decimal(str(continuous["skill_temperature"])),
        target_resolved_events=int(continuous["target_resolved_events"]),
        target_companies=int(continuous["target_companies"]),
        target_quarters=int(continuous["target_quarters"]),
        target_oos_windows=int(continuous["target_oos_windows"]),
        ece_soft_scale=__import__("decimal").Decimal(str(continuous["ece_soft_scale"])),
        uncertainty_inflation_max=__import__("decimal").Decimal(str(continuous["uncertainty_inflation_max"])),
    )
    policy.validate()

    hierarchy = payload.get("hierarchical_calibration") or {}
    if hierarchy.get("binary_event_posteriors_role") != "diagnostics_and_tail_risk_only":
        raise ValueError("binary event posteriors must not directly define scenarios")
    if hierarchy.get("scenario_probability_source") != "continuous_driver_posterior_predictive":
        raise ValueError("scenario probability must come from continuous driver posterior predictive")
    if hierarchy.get("sparse_leaf_action") != "inherit_parent_with_wide_interval":
        raise ValueError("sparse leaf must inherit parent rather than become unavailable")

    path_model = payload.get("continuous_financial_path_model") or {}
    required_drivers = set(path_model.get("required_driver_families") or ())
    if required_drivers != {"revenue_growth", "operating_margin", "cash_conversion", "capex_intensity"}:
        raise ValueError("continuous financial path required drivers drifted")
    if path_model.get("tail_model") != "student_t":
        raise ValueError("continuous financial path model must retain fat-tail predictive sampling")

    assembly = payload.get("scenario_assembly") or {}
    if assembly.get("default_method") != "continuous_financial_path_monte_carlo":
        raise ValueError("continuous financial path Monte Carlo must remain the default scenario assembler")
    if assembly.get("binary_event_state_to_scenario_mapping") != "forbidden":
        raise ValueError("binary event state to scenario mapping must remain forbidden")
    if assembly.get("bull_requires_all_risk_events_inactive") != "forbidden":
        raise ValueError("Bull cannot require every risk event to be inactive")
    if assembly.get("scenario_intrinsic_value_in_assignment") != "forbidden":
        raise ValueError("scenario intrinsic values cannot classify probability paths")
    if assembly.get("current_market_price_in_assignment") != "forbidden":
        raise ValueError("current market price cannot classify probability paths")
    if assembly.get("path_assignment") != "nearest_predeclared_economic_scenario_path":
        raise ValueError("continuous path assignment contract drifted")

    binding = payload.get("valuation_binding") or {}
    if not binding.get("no_minimum_leaf_sample_for_probability_existence"):
        raise ValueError("v3 must calculate probabilities for sparse leaves")
    if binding.get("target_price_or_market_price_tuning") != "forbidden":
        raise ValueError("market/target price tuning must remain forbidden")
    if binding.get("intrinsic_value_consumption") != "after_probability_snapshot_hash_is_frozen":
        raise ValueError("intrinsic values must be consumed only after probability freeze")

    legacy = payload.get("legacy_compatibility") or {}
    if legacy.get("v3_boolean_event_scenario_assembly") != "legacy_replay_only":
        raise ValueError("boolean event scenario assembly cannot remain a live default")

    print(
        "probability engine v3 policy: PASS "
        "continuous_paths=true boolean_scenario_mapping=false price_isolation=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
