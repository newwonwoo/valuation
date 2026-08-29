from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.control_plane import ExecutionMode, StageStatus, issue_freeze_token
from valuation_engine.evaluator_registry import (
    SegmentValuation,
    SegmentValuationDiagnostics,
    ValueKind,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.post_freeze_adapters import reverse_dcf_expectations_adapter
from valuation_engine.records import MarketObservation
from valuation_engine.reverse_dcf import (
    ReverseDCFError,
    ReverseDCFPolicy,
    build_reverse_dcf_result,
    implied_fcff_scale,
    implied_terminal_growth,
    solve_scenario_probability_requirement,
)
from valuation_engine.sotp import (
    AggregationComponent,
    CompanyScenarioEquityValue,
    ScenarioEquityAggregation,
)
from valuation_engine.valuation_execution import (
    GenericValuationResult,
    ScenarioPerShareValue,
)


ONE = Decimal("1")


def _forward_enterprise_value(
    *,
    fcff_path: tuple[Decimal, ...],
    discount_rate: Decimal,
    terminal_growth: Decimal,
) -> tuple[Decimal, Decimal]:
    explicit = Decimal("0")
    for year, fcff in enumerate(fcff_path, start=1):
        explicit += fcff / (ONE + discount_rate) ** year
    terminal = (
        fcff_path[-1]
        * (ONE + terminal_growth)
        / (discount_rate - terminal_growth)
        / (ONE + discount_rate) ** len(fcff_path)
    )
    return explicit, terminal


def _diagnostics(
    *,
    fcff_path: tuple[Decimal, ...] = (
        Decimal("100"),
        Decimal("110"),
        Decimal("120"),
    ),
    discount_rate: Decimal = Decimal("0.09"),
    terminal_growth: Decimal = Decimal("0.02"),
    terminal_roic: Decimal = Decimal("0.15"),
    unit: str = "KRW_billion",
) -> SegmentValuationDiagnostics:
    explicit, terminal = _forward_enterprise_value(
        fcff_path=fcff_path,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
    )
    diagnostics = SegmentValuationDiagnostics(
        execution_family="explicit_fcff_dcf",
        value_unit=unit,
        discount_rate=discount_rate,
        forecast_years=len(fcff_path),
        fcff_path=fcff_path,
        present_value_explicit=explicit,
        present_value_terminal=terminal,
        terminal_growth=terminal_growth,
        terminal_roic=terminal_roic,
    )
    diagnostics.validate()
    return diagnostics


def _valuation(
    *,
    diagnostics: SegmentValuationDiagnostics | None,
    value_per_share: Decimal = Decimal("1000"),
    diluted_shares: Decimal = Decimal("1000000"),
    ownership: Decimal | None = ONE,
    extra_components: tuple[AggregationComponent, ...] = (),
    reporting_unit: str = "KRW",
) -> GenericValuationResult:
    equity_amount = value_per_share * diluted_shares
    equity = Measure(equity_amount, reporting_unit, "2026-08-27")
    component = AggregationComponent(
        asset_id="ASSET",
        contribution_id="ASSET:Core:seg.driver_dcf:v1",
        attributable_equity_value=equity,
        economic_path_ids=("path:core",),
        ownership_ratio=ownership,
        diagnostics=diagnostics,
    )
    company = CompanyScenarioEquityValue(
        scenario_id="Core",
        equity_value=equity,
        components=(component, *extra_components),
        aggregation_hash="agg",
    )
    per_share = ScenarioPerShareValue(
        scenario_id="Core",
        equity_value_amount=equity_amount,
        reporting_unit=reporting_unit,
        diluted_shares=diluted_shares,
        value_per_share=value_per_share,
        aggregation_hash="agg",
        economic_path_ids=("path:core",),
    )
    return GenericValuationResult(
        scenarios=(per_share,),
        equity_aggregation=ScenarioEquityAggregation((company,), None, False),
        expected_value_per_share=None,
        reporting_unit=reporting_unit,
        valuation_hash="valuation-hash",
    )


# --------------------------------------------------------------------------- solvers


def test_implied_terminal_growth_round_trips_the_forward_model():
    fcff_path = (Decimal("100"), Decimal("110"), Decimal("120"))
    discount_rate = Decimal("0.09")
    true_growth = Decimal("0.031")
    explicit, terminal = _forward_enterprise_value(
        fcff_path=fcff_path,
        discount_rate=discount_rate,
        terminal_growth=true_growth,
    )
    solved = implied_terminal_growth(
        market_enterprise_value=explicit + terminal,
        present_value_explicit=explicit,
        terminal_fcff=fcff_path[-1],
        discount_rate=discount_rate,
        forecast_years=len(fcff_path),
    )
    assert solved is not None
    assert abs(solved - true_growth) < Decimal("1e-20")


def test_implied_terminal_growth_never_reaches_the_discount_rate():
    discount_rate = Decimal("0.08")
    solved = implied_terminal_growth(
        market_enterprise_value=Decimal("1e12"),
        present_value_explicit=Decimal("100"),
        terminal_fcff=Decimal("100"),
        discount_rate=discount_rate,
        forecast_years=5,
    )
    assert solved is not None
    assert solved < discount_rate


def test_implied_terminal_growth_has_no_solution_below_explicit_pv():
    assert (
        implied_terminal_growth(
            market_enterprise_value=Decimal("50"),
            present_value_explicit=Decimal("260"),
            terminal_fcff=Decimal("120"),
            discount_rate=Decimal("0.09"),
            forecast_years=3,
        )
        is None
    )


def test_implied_terminal_growth_rejects_non_positive_terminal_fcff():
    with pytest.raises(ReverseDCFError):
        implied_terminal_growth(
            market_enterprise_value=Decimal("1000"),
            present_value_explicit=Decimal("100"),
            terminal_fcff=Decimal("0"),
            discount_rate=Decimal("0.09"),
            forecast_years=3,
        )


def test_implied_fcff_scale_is_an_exact_ratio():
    assert implied_fcff_scale(
        market_enterprise_value=Decimal("600"),
        model_enterprise_value=Decimal("800"),
    ) == Decimal("0.75")


def test_implied_fcff_scale_rejects_non_positive_model_value():
    with pytest.raises(ReverseDCFError):
        implied_fcff_scale(
            market_enterprise_value=Decimal("600"),
            model_enterprise_value=Decimal("0"),
        )


# ----------------------------------------------------------------- scenario position


def test_scenario_requirement_brackets_the_market_price():
    requirement = solve_scenario_probability_requirement(
        market_value_per_share=Decimal("201500"),
        scenario_values=(
            ("Down", Decimal("151821")),
            ("Core", Decimal("242038")),
            ("Bull", Decimal("287875")),
        ),
    )
    assert requirement.position == "INSIDE"
    assert (requirement.lower_scenario_id, requirement.upper_scenario_id) == ("Down", "Core")
    assert requirement.lower_weight + requirement.upper_weight == ONE
    assert Decimal("0.44") < requirement.lower_weight < Decimal("0.46")


def test_scenario_requirement_flags_prices_outside_the_ladder():
    ladder = (("Down", Decimal("100")), ("Core", Decimal("200")))
    assert (
        solve_scenario_probability_requirement(
            market_value_per_share=Decimal("50"), scenario_values=ladder
        ).position
        == "BELOW_ALL"
    )
    assert (
        solve_scenario_probability_requirement(
            market_value_per_share=Decimal("500"), scenario_values=ladder
        ).position
        == "ABOVE_ALL"
    )


def test_scenario_requirement_requires_a_ladder():
    with pytest.raises(ReverseDCFError):
        solve_scenario_probability_requirement(
            market_value_per_share=Decimal("100"), scenario_values=()
        )


# ------------------------------------------------------------------------- assembly


def test_build_reverse_dcf_result_reconstructs_and_hashes():
    diagnostics = _diagnostics()
    valuation = _valuation(diagnostics=diagnostics)
    result = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("900"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    )
    scenario = result.scenarios[0]
    assert scenario.reconstructed
    assert scenario.market_enterprise_value < scenario.model_enterprise_value
    assert scenario.implied_terminal_growth < scenario.model_terminal_growth
    assert scenario.implied_fcff_scale < ONE
    assert len(result.result_hash) == 64

    cheaper = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("800"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    )
    assert cheaper.result_hash != result.result_hash


def test_terminal_value_share_matches_the_published_decomposition():
    diagnostics = _diagnostics()
    valuation = _valuation(diagnostics=diagnostics)
    result = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("1000"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    )
    expected = diagnostics.present_value_terminal / diagnostics.enterprise_value
    assert abs(result.scenarios[0].terminal_value_share - expected) < Decimal("1e-24")


def test_multi_component_scenarios_are_not_reconstructible():
    extra = AggregationComponent(
        asset_id="PARENT",
        contribution_id="parent:PARENT",
        attributable_equity_value=Measure(Decimal("10"), "KRW", "2026-08-27"),
        economic_path_ids=("parent:PARENT",),
    )
    valuation = _valuation(diagnostics=_diagnostics(), extra_components=(extra,))
    result = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("900"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    )
    scenario = result.scenarios[0]
    assert not scenario.reconstructed
    assert scenario.implied_terminal_growth is None
    reconstruction = next(
        item for item in result.findings if item.check == "reverse_dcf_reconstruction"
    )
    assert not reconstruction.passed


def test_non_dcf_scenarios_are_not_reconstructible():
    valuation = _valuation(diagnostics=None)
    scenario = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("900"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    ).scenarios[0]
    assert not scenario.reconstructed


def test_currency_mismatch_is_rejected():
    with pytest.raises(ReverseDCFError):
        build_reverse_dcf_result(
            valuation=_valuation(diagnostics=_diagnostics()),
            market_price=Decimal("900"),
            market_as_of="2026-08-27",
            market_currency="USD",
        )


def test_policy_thresholds_drive_findings():
    valuation = _valuation(diagnostics=_diagnostics())
    strict = build_reverse_dcf_result(
        valuation=valuation,
        market_price=Decimal("1000"),
        market_as_of="2026-08-27",
        market_currency="KRW",
        policy=ReverseDCFPolicy(max_terminal_value_share=Decimal("0.01")),
    )
    share_finding = next(
        item for item in strict.findings if item.check == "reverse_dcf_terminal_value_share"
    )
    assert not share_finding.passed
    assert not share_finding.blocking


def test_every_finding_is_non_blocking():
    result = build_reverse_dcf_result(
        valuation=_valuation(diagnostics=_diagnostics()),
        market_price=Decimal("400"),
        market_as_of="2026-08-27",
        market_currency="KRW",
    )
    assert result.findings
    assert all(not item.blocking for item in result.findings)


def test_invalid_policy_is_rejected():
    with pytest.raises(ReverseDCFError):
        ReverseDCFPolicy(max_terminal_value_share=Decimal("0")).validate()


# -------------------------------------------------------------------------- adapter


def _freeze_token(run_id: str = "RUN"):
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=(),
        expected_module_ids=(),
        ledger_snapshot_hash="ledger",
        assumption_set_hash="assumption",
        valuation_hash="valuation",
        audit_hash="audit",
        industry_snapshot_hash="industry",
        source_snapshot_hash="source",
    )


def _context(data: dict, *, run_id: str = "RUN", token=None) -> OrchestratorContext:
    return OrchestratorContext(
        run_id,
        ExecutionMode.LIVE_PRIMARY,
        data,
        [],
        token,
    )


def test_adapter_requires_a_same_run_freeze_token():
    adapter = reverse_dcf_expectations_adapter()
    result = adapter(_context({}, token=None))
    assert result.status is StageStatus.BLOCKED
    assert result.blocking

    mismatched = adapter(_context({}, run_id="OTHER", token=_freeze_token("RUN")))
    assert mismatched.status is StageStatus.BLOCKED


def test_adapter_skips_when_market_comparison_is_withheld():
    adapter = reverse_dcf_expectations_adapter()
    result = adapter(_context({}, token=_freeze_token()))
    assert result.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert not result.blocking
    assert "reverse_dcf_withheld_reason" in result.outputs


def test_adapter_emits_declared_outputs():
    valuation = _valuation(diagnostics=_diagnostics())
    adapter = reverse_dcf_expectations_adapter()
    result = adapter(
        _context(
            {
                "market_comparison": object(),
                "generic_valuation_result": valuation,
                "market_observation": MarketObservation(1000.0, "2026-08-27", "https://x"),
                "market_currency": "KRW",
            },
            token=_freeze_token(),
        )
    )
    assert result.status in {StageStatus.PASS, StageStatus.WARNING}
    assert not result.blocking
    assert set(result.outputs) == {
        "reverse_dcf_context",
        "reverse_dcf_result_hash",
        "reverse_dcf_findings",
    }


def test_adapter_never_blocks_a_frozen_run_on_reverse_dcf_failure():
    """A market-side failure must not retract an already-frozen intrinsic result."""
    valuation = _valuation(diagnostics=_diagnostics())
    adapter = reverse_dcf_expectations_adapter()
    result = adapter(
        _context(
            {
                "market_comparison": object(),
                "generic_valuation_result": valuation,
                "market_observation": MarketObservation(1000.0, "2026-08-27", "https://x"),
                "market_currency": "USD",  # deliberate mismatch
            },
            token=_freeze_token(),
        )
    )
    assert result.status is StageStatus.WARNING
    assert not result.blocking
    assert result.outputs["reverse_dcf_withheld_reason"] == "ReverseDCFError"
