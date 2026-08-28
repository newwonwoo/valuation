#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.strict_live_runtime import CANONICAL_ENTRYPOINT_ID


REQUIRED_LLM_FORBIDDEN = {
    "commit_assumption",
    "execute_probability_engine",
    "execute_monte_carlo_probability",
    "bind_probability_to_assumptions",
    "execute_valuation_math",
    "choose_canonical_scanner_loadout",
    "authorize_recovery_resolution",
    "authorize_audit",
    "issue_freeze_token",
    "load_pre_freeze_target_market_data",
    "publish_canonical_live_result",
}


def main() -> int:
    path = ROOT / "config" / "execution_authority_policy.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("version") != "1.0":
        raise ValueError("execution authority policy version drift")

    canonical = payload.get("canonical_execution") or {}
    if canonical.get("entrypoint") != "valuation_engine.strict_live_runtime.run_prism":
        raise ValueError("strict LIVE_PRIMARY entrypoint drift")
    if canonical.get("attestation_required") is not True:
        raise ValueError("canonical LIVE result must require execution attestation")
    if canonical.get("stage_receipts_required") is not True:
        raise ValueError("canonical LIVE result must require stage receipts")
    if canonical.get("legacy_live_runtime_entrypoint") != "regression_only":
        raise ValueError("legacy live runtime must remain regression-only")

    rocket = payload.get("rocket_context_engine") or {}
    if rocket.get("decision_owner") != "orchestrator":
        raise ValueError("RocketTesla context routing must be orchestrator-owned")
    if rocket.get("llm_scanner_selection") != "forbidden":
        raise ValueError("LLM scanner selection must remain forbidden")
    if rocket.get("mandatory_scanner_silent_skip") != "forbidden":
        raise ValueError("mandatory RocketTesla scanners cannot be silently skipped")

    llm = payload.get("llm_authority") or {}
    if llm.get("actor") != "proposal_only":
        raise ValueError("LLM authority must remain proposal-only")
    forbidden = set(llm.get("forbidden") or ())
    if not REQUIRED_LLM_FORBIDDEN.issubset(forbidden):
        raise ValueError(
            "LLM forbidden decision set is incomplete: "
            + ", ".join(sorted(REQUIRED_LLM_FORBIDDEN - forbidden))
        )

    recovery = payload.get("recovery_authority") or {}
    if recovery.get("resolution_owner") != "deterministic_readjudication":
        raise ValueError("recovery resolution must be deterministically re-adjudicated")
    if recovery.get("resolved_flag_alone_is_sufficient") is not False:
        raise ValueError("LLM resolved flag alone cannot authorize recovery")

    probability = payload.get("probability_and_valuation") or {}
    for key in (
        "current_market_price_probability_input",
        "target_price_probability_input",
        "scenario_intrinsic_value_probability_input",
    ):
        if probability.get(key) != "forbidden":
            raise ValueError(f"{key} must remain forbidden")
    if probability.get("probability_freeze_before_value_binding") != "required":
        raise ValueError("probability must freeze before value binding")

    result_policy = payload.get("canonical_result") or {}
    proofs = set(result_policy.get("required_proofs") or ())
    required_proofs = {
        "canonical_entrypoint_id",
        "execution_attestation_hash",
        "stage_receipt_hash_chain",
        "intrinsic_freeze_token_hash",
        "audit_pass",
    }
    if proofs != required_proofs:
        raise ValueError("canonical result proof set drift")
    if CANONICAL_ENTRYPOINT_ID != "prism_strict_live_primary/v1":
        raise ValueError("strict runtime canonical entrypoint ID drift")

    print(
        "execution authority policy: PASS "
        "orchestrator_owner=true rocket_context_locked=true llm_proposal_only=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
