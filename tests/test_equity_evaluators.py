from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.equity_evaluators import (
    FFOMultipleEvaluator,
    GordonDDMEvaluator,
    JustifiedPBROEEvaluator,
    LiveEquityMethodRegistration,
    NetAssetValueEvaluator,
    NormalizedEBITDAMultipleEvaluator,
    RateBaseROEEvaluator,
    ResidualIncomeEvaluator,
    live_equity_evaluator_registry_loader,
)
from valuation_engine.evaluator_registry import ValueKind
from valuation_engine.method_capabilities import (
    MethodKind,
    MethodRuntimeStatus,
    load_default_method_capability_registry,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.risk import HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.scenario_binding import BoundScenario
from valuation_engine.wacc import WACCResult


def assumption(key: str, amount: str, unit: str, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="BASE",
        measure=Measure(Decimal(amount), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def scenario(*items: CompiledAssumption) -> BoundScenario:
    return BoundScenario("BASE", items)


def test_gordon_ddm_outputs_equity_and_preserves_risk_paths():
    value = GordonDDMEvaluator(
        "financial_balance_sheet",
        Decimal("0.10"),
        "wacc:WACC",
        "beta:BETA",
    ).evaluate(
        scenario(
            assumption("forward_distribution", "12", "KRW_billion", "distribution"),
            assumption("terminal_growth", "0.04", "ratio", "growth"),
        ),
        segment_id="bank",
    )
    assert value.value_kind is ValueKind.EQUITY_VALUE
    assert value.value.amount == Decimal("200")
    assert "beta:BETA:bank" in value.economic_path_ids
    assert "wacc:WACC:bank" in value.economic_path_ids


def test_justified_pb_roe_uses_book_value_roe_growth_and_cost_of_equity():
    value = JustifiedPBROEEvaluator(
        "financial_balance_sheet",
        Decimal("0.09"),
        "wacc:WACC",
        "beta:BETA",
    ).evaluate(
        scenario(
            assumption("current_book_value", "100", "KRW_billion", "book"),
            assumption("forward_roe", "0.15", "ratio", "roe"),
            assumption("terminal_growth", "0.03", "ratio", "growth"),
        ),
        segment_id="bank",
    )
    assert value.value.amount == Decimal("200")
    assert value.value_kind is ValueKind.EQUITY_VALUE


def test_residual_income_follows_clean_surplus_and_terminal_residual_income():
    value = ResidualIncomeEvaluator(
        "financial_balance_sheet",
        Decimal("0.08"),
        "wacc:WACC",
        "beta:BETA",
        forecast_years=1,
    ).evaluate(
        scenario(
            assumption("beginning_book_value", "100", "KRW_billion", "book"),
            assumption("roe_year_1", "0.12", "ratio", "roe1"),
            assumption("distribution_year_1", "8", "KRW_billion", "dist1"),
            assumption("terminal_roe", "0.10", "ratio", "terminal-roe"),
            assumption("terminal_growth", "0.02", "ratio", "growth"),
        ),
        segment_id="bank",
    )
    expected = (
        Decimal("100")
        + Decimal("4") / Decimal("1.08")
        + (
            (Decimal("0.10") - Decimal("0.08"))
            * Decimal("104")
            * Decimal("1.02")
            / Decimal("0.06")
            / Decimal("1.08")
        )
    )
    assert abs(value.value.amount - expected) < Decimal("1e-24")
    assert value.value_kind is ValueKind.EQUITY_VALUE


def test_nav_and_ffo_multiple_are_equity_value_methods_without_market_inputs():
    nav = NetAssetValueEvaluator("asset_yield_nav").evaluate(
        scenario(
            assumption("gross_asset_value", "150", "KRW_billion", "assets"),
            assumption("liabilities", "40", "KRW_billion", "liabilities"),
        ),
        segment_id="reit",
    )
    ffo = FFOMultipleEvaluator("asset_yield_nav").evaluate(
        scenario(
            assumption("normalized_forward_ffo", "10", "KRW_billion", "ffo"),
            assumption("ffo_multiple", "12", "multiple", "multiple"),
        ),
        segment_id="reit",
    )
    assert nav.value.amount == Decimal("110")
    assert ffo.value.amount == Decimal("120")
    assert nav.value_kind is ValueKind.EQUITY_VALUE
    assert ffo.value_kind is ValueKind.EQUITY_VALUE


def test_rate_base_roe_is_discounted_equity_value():
    value = RateBaseROEEvaluator(
        "regulated_rate_base",
        Decimal("0.09"),
        "wacc:WACC",
        "beta:BETA",
    ).evaluate(
        scenario(
            assumption("rate_base", "1000", "KRW_billion", "rate-base"),
            assumption("equity_ratio", "0.50", "ratio", "equity-ratio"),
            assumption("allowed_roe", "0.10", "ratio", "allowed-roe"),
            assumption("terminal_growth", "0.03", "ratio", "growth"),
        ),
        segment_id="utility",
    )
    expected = Decimal("1000") * Decimal("0.5") * Decimal("0.10") * Decimal("1.03") / Decimal("0.06")
    assert value.value.amount == expected
    assert value.value_kind is ValueKind.EQUITY_VALUE


def test_contracted_backlog_normalized_ebitda_is_enterprise_multiple_method():
    value = NormalizedEBITDAMultipleEvaluator().evaluate(
        scenario(
            assumption("normalized_ebitda", "20", "KRW_billion", "ebitda"),
            assumption("normalized_ebitda_multiple", "8", "multiple", "multiple"),
        ),
        segment_id="backlog",
    )
    assert value.value.amount == Decimal("160")
    assert value.value_kind is ValueKind.ENTERPRISE_VALUE


def test_discounted_equity_methods_fail_closed_when_cost_of_equity_does_not_exceed_growth():
    evaluator = GordonDDMEvaluator(
        "financial_balance_sheet",
        Decimal("0.04"),
        "wacc:WACC",
        "beta:BETA",
    )
    with pytest.raises(ValueError, match="exceed terminal growth"):
        evaluator.evaluate(
            scenario(
                assumption("forward_distribution", "12", "KRW_billion", "distribution"),
                assumption("terminal_growth", "0.04", "ratio", "growth"),
            ),
            segment_id="bank",
        )


def _live_wacc() -> LiveWACCStageResult:
    structure = LiveCapitalStructureObservation(
        equity_weight=0.8,
        debt_weight=0.2,
        tax_rate=0.25,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of="2026-06-30",
        source_refs=("CAPITAL",),
        rationale="fixture",
    )
    beta = LiveBetaStageResult(
        estimate=HierarchicalBetaEstimate(1.0, 0.1, ()),
        target_asset_beta=1.0,
        target_levered_beta=1.1,
        target_capital_structure=structure,
        peer_ids=("P1",),
        source_refs=("BETA-SOURCE",),
        selection_evidence_ids=("E-BETA",),
        snapshot_hash="BETA",
    )
    return LiveWACCStageResult(
        beta_result=beta,
        wacc_result=WACCResult(0.10, 0.04, 0.8, 0.2, 0.088),
        terminal_consistency=None,
        source_refs=("WACC-SOURCE",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash="WACC",
    )


def test_live_loader_registers_exact_discounted_method_from_same_run_wacc():
    loader = live_equity_evaluator_registry_loader(
        registrations=(
            LiveEquityMethodRegistration(
                "financial_balance_sheet",
                "residual_income",
                forecast_years=1,
            ),
        )
    )
    registry = loader(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"live_wacc_result": _live_wacc()},
        )
    )
    assert registry.keys()[0].archetype == "financial_balance_sheet"
    assert registry.keys()[0].method == "residual_income"


def test_live_loader_rejects_pre_freeze_target_market_leakage():
    loader = live_equity_evaluator_registry_loader(
        registrations=(
            LiveEquityMethodRegistration("asset_yield_nav", "nav"),
        )
    )
    with pytest.raises(PermissionError, match="target Street/market"):
        loader(
            OrchestratorContext(
                "RUN",
                ExecutionMode.LIVE_PRIMARY,
                {"current_market_price": 100},
            )
        )


def test_capability_registry_has_no_unimplemented_exact_binding_and_pipeline_option_uses_sotp():
    registry = load_default_method_capability_registry()
    coverage = registry.coverage_summary()
    assert coverage.not_implemented == ()
    pipeline = registry.get("hit_driven_content", "pipeline_option_sotp")
    assert pipeline.kind is MethodKind.AGGREGATOR
    assert pipeline.runtime_status is MethodRuntimeStatus.RUNTIME_READY
    assert pipeline.execution_family == "sotp"
