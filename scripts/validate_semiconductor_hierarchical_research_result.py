#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "data" / "calibration" / "semiconductor_hierarchical_research_result_20260828.json"


def main() -> int:
    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    if payload.get("production_certificate_status") != "NOT_CALIBRATED":
        raise ValueError("research migration result must never claim a production certificate")
    if payload.get("decision") != "NO_PROBABILITY_UPDATE":
        raise ValueError("current research result must preserve the failed authorization gate")
    if payload.get("companies_fetched") != 30 or payload.get("failures"):
        raise ValueError("frozen research result must bind the complete 30-company fetch")

    scenario = payload.get("scenario_holdout") or {}
    factor_results = scenario.get("factor_gate_results") or {}
    if scenario.get("scenario_gate_passed") is not True:
        raise ValueError("scenario diagnostic gate is expected to pass in the frozen result")
    if scenario.get("factor_gate_passed") is not False:
        raise ValueError("factor authorization gate must remain failed in the frozen result")
    if all(bool(value) for value in factor_results.values()):
        raise ValueError("at least one active factor must fail when update is withheld")
    if scenario.get("gate_passed") is not False:
        raise ValueError("combined authorization gate must fail when any factor is uncertified")

    prior = payload.get("analyst_prior") or {}
    final = payload.get("final_research_calibrated_probability") or {}
    if prior != final:
        raise ValueError("failed authorization gate must leave scenario probabilities unchanged")
    if abs(sum(float(value) for value in final.values()) - 1.0) > 1e-12:
        raise ValueError("final scenario probabilities must sum to one")

    values = payload.get("scenario_values_krw") or {}
    expected = round(
        sum(float(final[key]) * float(values[key]) for key in ("Down", "Core", "Bull"))
    )
    if expected != int(payload.get("probability_weighted_value_krw")):
        raise ValueError("probability-weighted value is not reproducible from frozen inputs")

    memory = payload.get("memory_hierarchical_event_probability") or {}
    if any(int(item.get("parent_child_overlap", -1)) != 0 for item in memory.values()):
        raise ValueError("parent and memory child histories must be disjoint")
    for event in ("revenue_growth_miss", "margin_compression"):
        node = memory[event]
        if node.get("parent_authorized") is not False or node.get("authorizable") is not False:
            raise ValueError(f"uncertified parent must not authorize memory child: {event}")
    cash = memory["cash_conversion_miss"]
    if cash.get("parent_authorized") is not True or cash.get("state") != "SHRUNK":
        raise ValueError("cash-conversion memory child should be inherited through shrinkage only")
    if int(cash.get("companies", 0)) >= 5:
        raise ValueError("frozen memory child should not meet the five-company promotion gate")

    provenance = payload.get("provenance") or {}
    required = (
        "workflow_run_id",
        "head_sha",
        "artifact_id",
        "artifact_zip_sha256",
        "result_json_sha256",
    )
    if any(not provenance.get(key) for key in required):
        raise ValueError("research result provenance is incomplete")

    print(
        "semiconductor hierarchical research result: PASS "
        f"decision={payload['decision']} probabilities={final} expected={expected}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
