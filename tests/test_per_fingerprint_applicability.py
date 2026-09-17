"""Withheld PER must not require invented operating-driver DCF fingerprints."""
from dataclasses import replace

import pytest

from valuation_engine.assumption_compiler import CompiledAssumptionSet
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.per_adapters import LivePERInputs, PERApplicability
from valuation_engine.runtime_support_adapters import dcf_consistency_fingerprint_adapter
from valuation_engine.valuation_method_intent import ValuationMethodIntent
from valuation_engine.valuation_plan_compiler import ValuationPlanStatus


def context(**extra):
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {
        "valuation_method_intent": ValuationMethodIntent(
            status=ValuationPlanStatus.READY, segments=(), warranted_per_segments=("aerospace",),
            requires_beta=True, requires_wacc=True, module_plan_hash="module",
            capability_registry_hash="capability"),
        "compiled_assumption_set": CompiledAssumptionSet("TARGET", (), "compiled"),
        **extra,
    })


def withheld():
    return LivePERInputs(target_id="TARGET", applicability=PERApplicability.NOT_APPLICABLE,
        applicability_rationale="No authorized same-period comparable PER inputs; primary DCF only.")


def test_explicit_withholding_skips_only_cross_method_fingerprint():
    result = dcf_consistency_fingerprint_adapter(None, per_loader=lambda _: withheld())(context())
    assert result.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert not result.blocking
    assert result.outputs == {"dcf_fingerprint_withholding_rationale": withheld().applicability_rationale}
    assert "warranted_per_applicable" not in result.outputs
    assert "No authorized same-period" in result.rationale
    assert "dcf_assumption_fingerprint" not in result.outputs


def test_missing_per_provider_is_still_a_blocker():
    result = dcf_consistency_fingerprint_adapter(None)(context(warranted_per_applicable=False))
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking  # A bare boolean cannot waive the provider contract.


@pytest.mark.parametrize("change", [
    {"target_id": "OTHER"},
    {"applicability_rationale": ""},
    {"applicability": PERApplicability.APPLICABLE},
    {"core_assumption_keys": object()},
])
def test_unbound_or_invalid_applicability_cannot_skip_fingerprint(change):
    result = dcf_consistency_fingerprint_adapter(
        None, per_loader=lambda _: replace(withheld(), **change))(context())
    assert result.blocking
    assert "warranted_per_applicable" not in result.outputs


def test_precheck_preserves_target_market_isolation_before_loading():
    def must_not_load(_):
        pytest.fail("loader accessed target market context")
    result = dcf_consistency_fingerprint_adapter(None, per_loader=must_not_load)(
        context(market_price=123))
    assert result.blocking
    assert "target Street/market" in result.rationale


def test_valid_applicable_per_still_requires_the_real_dcf_fingerprint():
    from valuation_engine.per_adapters import LivePERAssumptionKeys
    keys = LivePERAssumptionKeys("BASE", "eps", "KRW", ("growth",),
        ("conversion1", "conversion2"), "terminal_growth", "terminal_roe",
        ("margin",), ("reinvestment",))
    inputs = replace(withheld(), applicability=PERApplicability.APPLICABLE,
        core_assumption_keys=keys)
    inputs.validate()
    result = dcf_consistency_fingerprint_adapter(None, per_loader=lambda _: inputs)(context())
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking
    assert "driver-specific" in result.rationale


def test_precheck_then_actual_per_adapter_preserves_stage_output_ownership():
    from valuation_engine.ledger import EvidenceLedger
    from valuation_engine.per_adapters import live_hierarchical_warranted_per_adapter
    from valuation_engine.risk_adapters import LiveWACCStageResult
    from valuation_engine.runtime_support_adapters import chain_stage_adapters, conditional_warranted_per_adapter
    from valuation_engine.wacc import WACCResult
    # Withheld PER requires a completed WACC result but consumes no beta numbers.
    wacc = LiveWACCStageResult(beta_result=None,
        wacc_result=WACCResult(0.10, 0.04, 0.75, 0.25, 0.085),
        terminal_consistency=None, source_refs=("https://example.com/risk",),
        funding_credit_evidence_ids=(), customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash="risk")
    loader = lambda _: withheld()
    precheck = dcf_consistency_fingerprint_adapter(None, per_loader=loader)
    per = conditional_warranted_per_adapter(live_hierarchical_warranted_per_adapter(loader=loader))
    first = precheck(context(evidence_ledger=EvidenceLedger(), live_wacc_result=wacc))
    following = context(evidence_ledger=EvidenceLedger(), live_wacc_result=wacc, **first.outputs)
    # The next stage itself is wrapped in a chain, which rejects keys that its
    # incoming context already owns even when the duplicated value is identical.
    result = chain_stage_adapters(per)(following)
    assert not result.blocking
    assert result.outputs == {"warranted_per_applicable": False}
    assert first.outputs.keys().isdisjoint(result.outputs)
