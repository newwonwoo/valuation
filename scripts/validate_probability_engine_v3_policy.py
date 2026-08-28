#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.continuous_predictive_weight import ContinuousWeightPolicy


def main() -> int:
    path = ROOT / "config" / "probability_engine_v3_policy.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("version") != "3.0":
        raise ValueError("probability engine v3 policy version drift")
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
        if "block" in str(continuous.get(key, "")).lower() and "not_block" not in str(continuous.get(key, "")).lower():
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
    hierarchy = payload.get("hierarchical_posterior") or {}
    if hierarchy.get("sparse_leaf_action") != "inherit_parent_with_wide_interval":
        raise ValueError("sparse leaf must inherit parent rather than become unavailable")
    assembly = payload.get("scenario_assembly") or {}
    if assembly.get("naive_independent_factor_multiplication") != "forbidden":
        raise ValueError("naive independent scenario multiplication must remain forbidden")
    binding = payload.get("valuation_binding") or {}
    if not binding.get("no_minimum_leaf_sample_for_probability_existence"):
        raise ValueError("v3 must calculate probabilities for sparse leaves")
    if binding.get("target_price_or_market_price_tuning") != "forbidden":
        raise ValueError("market/target price tuning must remain forbidden")
    print("probability engine v3 policy: PASS integrity_only_hard_gates=true sparse_leaf_probability=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
