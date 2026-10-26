"""Optional source-bound risk policy uses the existing Beta/WACC contracts."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from tests.test_declared_risk_pack import AS_OF, TARGET, _payload, _write
from valuation_engine.authorized_risk_providers import AuthorizedRiskProviderError
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.declared_risk_pack import (
    BETA_SELECTION_METRICS,
    DeclaredRiskPackError,
    declared_risk_collector,
    load_declared_risk_pack,
)
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.risk_adapters import (
    TargetCapitalStructureMethod,
    live_hierarchical_beta_adapter,
    live_wacc_validation_adapter,
)


def _policy():
    return {
        "equity_weight": 0.95,
        "debt_weight": 0.05,
        "tax_rate": 0.0,
        "method": "long_run_policy",
        "as_of": AS_OF,
        "source_refs": ["https://probe.invalid/filing/losses-and-financing"],
        "rationale": "Analyst funding policy: no usable interest tax shield during losses; not the statutory country tax.",
    }


def _override_payload():
    payload = _payload()
    payload["country_risk"]["corporate_tax_rate"] = 0.264
    payload["target_capital_structure"] = _policy()
    payload["risk_free_rate"]["methodology"] = "Derived KRW risk-free proxy: sovereign yield less sourced default spread, not a direct observation."
    payload["marginal_debt"]["methodology"] = "Analyst synthetic coverage-based debt cost; assumed B-/B3 category, not an actual issuer agency rating."
    payload["marginal_debt"]["credit_rating"] = "synthetic B-/B3 assumption"
    payload["marginal_debt"]["series"]["source_ref"] = "https://probe.invalid/synthetic-coverage-table"
    return payload


def test_legacy_defaults_are_unchanged(tmp_path):
    payload = _payload()
    declared = load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)
    peers = [p for level in payload["beta_levels"].values() for p in level["peers"]]
    expected_weight = sum(p["capital"]["debt"] / (p["capital"]["debt"] + p["capital"]["equity_market_value"]) for p in peers) / len(peers)
    inputs = declared.wacc_inputs()
    assert inputs.target_capital_structure.debt_weight == pytest.approx(expected_weight)
    assert inputs.target_capital_structure.tax_rate == 0.24
    assert inputs.target_capital_structure.method is TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE
    assert inputs.risk_free_rate.methodology == "BOK ECOS: 국고채 10년"
    assert inputs.marginal_pre_tax_cost_of_debt.methodology == (
        "BOK ECOS market borrowing benchmark matched to rating=AA-, maturity=3Y; "
        "rating source=https://probe.invalid/rating/issuer"
    )


def test_optional_policy_preserves_sources_tax_fact_and_both_stage_inputs(tmp_path):
    payload = _override_payload()
    path = _write(tmp_path, payload)
    declared = load_declared_risk_pack(path, run_as_of=AS_OF)
    universe, inputs = declared.beta_universe(), declared.wacc_inputs()
    assert universe.target_capital_structure is inputs.target_capital_structure
    assert inputs.target_capital_structure.tax_rate == 0.0
    assert declared.pack.country_risk.corporate_tax_rate == 0.264
    assert inputs.target_capital_structure.method is TargetCapitalStructureMethod.LONG_RUN_POLICY
    assert inputs.risk_free_rate.methodology == payload["risk_free_rate"]["methodology"]
    assert inputs.marginal_pre_tax_cost_of_debt.methodology == payload["marginal_debt"]["methodology"]
    assert inputs.marginal_pre_tax_cost_of_debt.source_ref == payload["marginal_debt"]["series"]["source_ref"]
    assert set(payload["target_capital_structure"]["source_refs"]) <= set(universe.source_refs)
    assert declared.file_sha256 == sha256(Path(path).read_bytes()).hexdigest()
    batch = declared_risk_collector(declared, segment_id="core")(
        EvidenceCollectionRequest(target_id=TARGET, required_metrics=BETA_SELECTION_METRICS)
    )
    result = run_controlled_workflow(
        run_id="DECLARED-POLICY-CHAIN", execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        adapters={
            "HIERARCHICAL_BETA_ESTIMATION": live_hierarchical_beta_adapter(loader=lambda _: declared.beta_universe()),
            "WACC_VALIDATION": live_wacc_validation_adapter(loader=lambda _: declared.wacc_inputs()),
        },
        required_stages=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        initial_data={"evidence_ledger": EvidenceLedger(batch.records)},
    )
    assert result.blocked_reasons == ()
    assert all(t.status is StageStatus.PASS for t in result.stage_traces)
    assert result.data["target_levered_beta"] == pytest.approx(result.data["target_asset_beta"] * (1 + 0.05 / 0.95))
    wacc = result.data["live_wacc_result"].wacc_result
    assert wacc.after_tax_cost_of_debt == pytest.approx(inputs.marginal_pre_tax_cost_of_debt.value)
    assert wacc.wacc == pytest.approx(0.95 * wacc.cost_of_equity + 0.05 * wacc.after_tax_cost_of_debt)


@pytest.mark.parametrize("field,value", [
    ("equity_weight", True), ("debt_weight", "0.05"), ("tax_rate", False),
    ("tax_rate", -0.1), ("tax_rate", 1.0), ("tax_rate", float("nan")),
    ("equity_weight", 0.9), ("debt_weight", -0.05),
    ("method", "current_target_market_cap"), ("method", None),
    ("as_of", 20260827), ("as_of", "2026-99-99"),
    ("source_refs", []), ("source_refs", "https://probe.invalid/filing"),
    ("source_refs", [False]), ("source_refs", ["http-not-a-url"]),
    ("source_refs", ["file:///tmp/memo"]), ("source_refs", ["https://"]),
    ("rationale", " "), ("rationale", []),
])
def test_malformed_explicit_policy_fails_closed(tmp_path, field, value):
    payload = _override_payload()
    payload["target_capital_structure"][field] = value
    with pytest.raises(ValueError):
        load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)


@pytest.mark.parametrize("value", [None, [], "", {"debt_weight": 0.05}])
def test_present_invalid_or_incomplete_policy_is_not_silent_default(tmp_path, value):
    payload = _payload()
    payload["target_capital_structure"] = value
    with pytest.raises(DeclaredRiskPackError):
        load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)


@pytest.mark.parametrize("field", ["risk_free_rate", "marginal_debt"])
@pytest.mark.parametrize("value", [None, "", " ", False, 1.5, [], {}])
def test_present_invalid_methodology_is_not_silent_default(tmp_path, field, value):
    payload = _payload()
    payload[field]["methodology"] = value
    with pytest.raises(DeclaredRiskPackError, match="methodology"):
        load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)


def test_future_policy_date_is_included_in_cutoff_check(tmp_path):
    payload = _override_payload()
    payload["target_capital_structure"]["as_of"] = "2026-08-28"
    with pytest.raises(DeclaredRiskPackError, match="target_capital_structure.as_of.*after run cutoff"):
        load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)


@pytest.mark.parametrize("change", ["tax", "methodology", "source", "rationale"])
def test_policy_or_methodology_changes_bind_new_file_fingerprint(tmp_path, change):
    payload = _override_payload()
    original = load_declared_risk_pack(_write(tmp_path, payload))
    if change == "tax":
        payload["target_capital_structure"]["tax_rate"] = 0.1
    elif change == "methodology":
        payload["marginal_debt"]["methodology"] += " Additional sensitivity."
    elif change == "source":
        payload["target_capital_structure"]["source_refs"].append("https://probe.invalid/second-source")
    else:
        payload["target_capital_structure"]["rationale"] += " Conditional, not observed."
    edited = load_declared_risk_pack(_write(tmp_path, payload))
    assert original.file_sha256 != edited.file_sha256


@pytest.mark.parametrize("field,value", [("method", "long_run_policy"), ("source_refs", ("file:///tmp/memo",)), ("tax_rate", False)])
def test_direct_provider_override_also_validates_new_policy_types_and_sources(tmp_path, field, value):
    declared = load_declared_risk_pack(_write(tmp_path, _override_payload()))
    bad = replace(declared.pack.target_capital_structure_override, **{field: value})
    with pytest.raises(AuthorizedRiskProviderError):
        replace(declared.pack, target_capital_structure_override=bad).beta_universe()


def test_zero_debt_compiled_scenario_is_explicit_not_a_country_tax_mutation(tmp_path):
    payload = _override_payload()
    payload["target_capital_structure"].update(equity_weight=1, debt_weight=0, method="compiled_scenario")
    declared = load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)
    assert declared.wacc_inputs().target_capital_structure.debt_weight == 0
    assert declared.wacc_inputs().target_capital_structure.method is TargetCapitalStructureMethod.COMPILED_SCENARIO
    assert declared.pack.country_risk.corporate_tax_rate == 0.264


@pytest.mark.parametrize("source_ref", [
    "https://reader:password@probe.invalid/filing",
    "https://reader@probe.invalid/filing",
    "https://probe.invalid/filing?api_key=example",
    "https://probe.invalid/filing?TOKEN=example",
    "https://probe.invalid/filing?x-amz-signature=example",
    "https://probe.invalid/filing?crtfc_key=example",
])
def test_policy_sources_reuse_credential_free_reporting_guard_at_both_boundaries(tmp_path, source_ref):
    payload = _override_payload()
    declared = load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)
    payload["target_capital_structure"]["source_refs"] = [source_ref]
    with pytest.raises(DeclaredRiskPackError, match="credential-free"):
        load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)
    bad = replace(declared.pack.target_capital_structure_override, source_refs=(source_ref,))
    with pytest.raises(AuthorizedRiskProviderError, match="credential-free"):
        replace(declared.pack, target_capital_structure_override=bad).beta_universe()


def test_safe_source_query_and_fragment_are_preserved_in_hashed_declaration(tmp_path):
    payload = _override_payload()
    source_ref = "https://probe.invalid/filing?rcpNo=20260811000657#note19"
    payload["target_capital_structure"]["source_refs"] = [source_ref]
    declared = load_declared_risk_pack(_write(tmp_path, payload), run_as_of=AS_OF)
    assert declared.beta_universe().target_capital_structure.source_refs == (source_ref,)
    assert declared.wacc_inputs().target_capital_structure.source_refs == (source_ref,)
