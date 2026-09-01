from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evaluator_registry import SegmentValuationDiagnostics
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.reverse_dcf import ReconstructionStatus
from valuation_engine.sotp import (
    AggregationComponent,
    CompanyScenarioEquityValue,
    ScenarioEquityAggregation,
)
from valuation_engine.valuation_execution import (
    GenericValuationResult,
    ScenarioPerShareValue,
)
from valuation_engine.valuation_sensitivity import (
    DISCOUNT_RATE,
    FCFF_LEVEL,
    TERMINAL_GROWTH,
    SensitivityPolicy,
    ValuationSensitivityError,
    build_valuation_sensitivity_report,
    enterprise_value,
    valuation_sensitivity_adapter,
)


ONE = Decimal("1")
FCFF_PATH = (Decimal("100"), Decimal("110"), Decimal("120"))
RATE = Decimal("0.09")
GROWTH = Decimal("0.02")
SHARES = Decimal("1000")


def _diagnostics(
    *,
    fcff_path: tuple[Decimal, ...] = FCFF_PATH,
    discount_rate: Decimal = RATE,
    terminal_growth: Decimal = GROWTH,
    unit: str = "KRW_billion",
    pv_override: tuple[Decimal, Decimal] | None = None,
) -> SegmentValuationDiagnostics:
    explicit = Decimal("0")
    for year, fcff in enumerate(fcff_path, start=1):
        explicit += fcff / (ONE + discount_rate) ** year
    terminal = (
        fcff_path[-1]
        * (ONE + terminal_growth)
        / (discount_rate - terminal_growth)
        / (ONE + discount_rate) ** len(fcff_path)
    )
    if pv_override is not None:
        explicit, terminal = pv_override
    return SegmentValuationDiagnostics(
        execution_family="explicit_fcff_dcf",
        value_unit=unit,
        discount_rate=discount_rate,
        forecast_years=len(fcff_path),
        fcff_path=fcff_path,
        present_value_explicit=explicit,
        present_value_terminal=terminal,
        terminal_growth=terminal_growth,
        terminal_roic=Decimal("0.15"),
    )


def _valuation(
    *,
    diagnostics: SegmentValuationDiagnostics | None,
    ownership: Decimal | None = ONE,
    net_cash: Decimal = Decimal("0"),
    extra_components: tuple[AggregationComponent, ...] = (),
    reporting_unit: str = "KRW_billion",
) -> GenericValuationResult:
    if diagnostics is not None:
        base_ev = diagnostics.enterprise_value
        equity_amount = (ownership or ONE) * (base_ev + net_cash)
    else:
        equity_amount = Decimal("1000")
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
        diluted_shares=SHARES,
        value_per_share=equity_amount / SHARES,
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


# ---------------------------------------------------------------------------- kernel


def test_enterprise_value_matches_a_hand_computed_gordon_model():
    fcff = (Decimal("100"),)
    rate = Decimal("0.10")
    growth = Decimal("0")
    expected = Decimal("100") / Decimal("1.1") + (
        Decimal("100") / Decimal("0.10") / Decimal("1.1")
    )
    assert abs(
        enterprise_value(fcff_path=fcff, discount_rate=rate, terminal_growth=growth) - expected
    ) < Decimal("1e-24")


def test_enterprise_value_rejects_growth_at_or_above_the_discount_rate():
    with pytest.raises(ValuationSensitivityError):
        enterprise_value(
            fcff_path=FCFF_PATH, discount_rate=Decimal("0.02"), terminal_growth=Decimal("0.02")
        )


def test_enterprise_value_is_linear_in_the_fcff_path():
    base = enterprise_value(
        fcff_path=FCFF_PATH, discount_rate=RATE, terminal_growth=GROWTH
    )
    doubled = enterprise_value(
        fcff_path=tuple(item * 2 for item in FCFF_PATH),
        discount_rate=RATE,
        terminal_growth=GROWTH,
    )
    assert abs(doubled - base * 2) < Decimal("1e-20")


# ----------------------------------------------------------------------------- report


def test_three_kernel_variables_are_measured():
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=_diagnostics()))
    scenario = report.scenarios[0]
    assert scenario.measured
    assert {item.variable for item in scenario.variables} == {
        DISCOUNT_RATE,
        TERMINAL_GROWTH,
        FCFF_LEVEL,
    }


def test_directions_are_monotonic_as_expected():
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=_diagnostics()))
    variables = {item.variable: item for item in report.scenarios[0].variables}

    rate = variables[DISCOUNT_RATE]
    assert rate.low_value_per_share > rate.base_value_per_share > rate.high_value_per_share
    assert rate.low_value_pct > 0 > rate.high_value_pct

    growth = variables[TERMINAL_GROWTH]
    assert growth.low_value_per_share < growth.base_value_per_share < growth.high_value_per_share

    assert all(item.monotonic for item in report.scenarios[0].variables)


def test_fcff_level_moves_value_by_the_scaled_enterprise_share():
    """With no net cash and full ownership, a 10% FCFF move is a 10% value move."""
    report = build_valuation_sensitivity_report(
        valuation=_valuation(diagnostics=_diagnostics(), net_cash=Decimal("0"))
    )
    level = next(
        item for item in report.scenarios[0].variables if item.variable == FCFF_LEVEL
    )
    assert abs(level.high_value_pct - Decimal("0.10")) < Decimal("1e-9")
    assert abs(level.low_value_pct + Decimal("0.10")) < Decimal("1e-9")


def test_net_cash_damps_the_fcff_sensitivity():
    """A large net-cash bridge means enterprise moves translate into smaller equity moves."""
    plain = build_valuation_sensitivity_report(
        valuation=_valuation(diagnostics=_diagnostics(), net_cash=Decimal("0"))
    )
    cash_rich = build_valuation_sensitivity_report(
        valuation=_valuation(diagnostics=_diagnostics(), net_cash=Decimal("1000"))
    )

    def level_pct(report):
        return next(
            item for item in report.scenarios[0].variables if item.variable == FCFF_LEVEL
        ).high_value_pct

    assert level_pct(cash_rich) < level_pct(plain)


def test_base_case_must_reproduce_the_published_enterprise_value():
    tampered = _diagnostics(pv_override=(Decimal("1"), Decimal("1")))
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=tampered))
    scenario = report.scenarios[0]
    assert scenario.status == ReconstructionStatus.NOT_RECONSTRUCTIBLE
    assert not scenario.variables


def test_dcf_plus_parent_adjustment_is_measurable_by_component():
    diagnostics = _diagnostics()
    base_ev = diagnostics.enterprise_value
    parent_amount = Decimal("10")
    core = AggregationComponent(
        asset_id="ASSET",
        contribution_id="ASSET:Core:seg.driver_dcf:v1",
        attributable_equity_value=Measure(base_ev, "KRW_billion", "2026-08-27"),
        economic_path_ids=("path:core",),
        ownership_ratio=ONE,
        diagnostics=diagnostics,
    )
    parent = AggregationComponent(
        asset_id="PARENT",
        contribution_id="parent:PARENT",
        attributable_equity_value=Measure(parent_amount, "KRW_billion", "2026-08-27"),
        economic_path_ids=("parent:PARENT",),
    )
    total = base_ev + parent_amount
    company = CompanyScenarioEquityValue(
        scenario_id="Core",
        equity_value=Measure(total, "KRW_billion", "2026-08-27"),
        components=(core, parent),
        aggregation_hash="agg",
    )
    valuation = GenericValuationResult(
        scenarios=(
            ScenarioPerShareValue(
                scenario_id="Core",
                equity_value_amount=total,
                reporting_unit="KRW_billion",
                diluted_shares=SHARES,
                value_per_share=total / SHARES,
                aggregation_hash="agg",
                economic_path_ids=("path:core", "parent:PARENT"),
            ),
        ),
        equity_aggregation=ScenarioEquityAggregation((company,), None, False),
        expected_value_per_share=None,
        reporting_unit="KRW_billion",
        valuation_hash="valuation-hash",
    )
    report = build_valuation_sensitivity_report(valuation=valuation)
    assert report.scenarios[0].measured
    assert len(report.scenarios[0].segments) == 1


def test_non_dcf_scenarios_are_not_measurable():
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=None))
    assert not report.scenarios[0].measured
    measured = next(
        item for item in report.findings if item.check == "valuation_sensitivity_measured"
    )
    assert not measured.passed


def test_terminal_growth_is_skipped_when_the_perturbation_would_cross_the_discount_rate():
    tight = _diagnostics(discount_rate=Decimal("0.023"), terminal_growth=Decimal("0.02"))
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=tight))
    variables = {item.variable for item in report.scenarios[0].variables}
    assert TERMINAL_GROWTH not in variables
    assert DISCOUNT_RATE not in variables
    assert FCFF_LEVEL in variables


def test_dominant_variable_is_the_largest_absolute_move():
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=_diagnostics()))
    scenario = report.scenarios[0]
    dominant = scenario.dominant
    assert dominant is not None
    assert dominant.max_abs_pct == max(item.max_abs_pct for item in scenario.variables)


def test_concentration_finding_triggers_on_a_tight_threshold():
    report = build_valuation_sensitivity_report(
        valuation=_valuation(diagnostics=_diagnostics()),
        policy=SensitivityPolicy(high_sensitivity_pct=Decimal("0.001")),
    )
    concentration = next(
        item for item in report.findings if item.check == "valuation_sensitivity_concentration"
    )
    assert not concentration.passed
    assert not concentration.blocking


def test_every_finding_is_non_blocking():
    report = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=_diagnostics()))
    assert report.findings
    assert all(not item.blocking for item in report.findings)


def test_report_hash_tracks_policy_changes():
    base = build_valuation_sensitivity_report(valuation=_valuation(diagnostics=_diagnostics()))
    wider = build_valuation_sensitivity_report(
        valuation=_valuation(diagnostics=_diagnostics()),
        policy=SensitivityPolicy(discount_rate_delta=Decimal("0.01")),
    )
    assert base.report_hash != wider.report_hash
    assert len(base.report_hash) == 64


def test_policy_is_validated():
    with pytest.raises(ValuationSensitivityError):
        SensitivityPolicy(fcff_level_delta=Decimal("1")).validate()
    with pytest.raises(ValuationSensitivityError):
        SensitivityPolicy(discount_rate_delta=Decimal("0")).validate()


# ---------------------------------------------------------------------------- adapter


def _context(data: dict) -> OrchestratorContext:
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data, [], None)


def test_adapter_requires_a_valuation_result():
    result = valuation_sensitivity_adapter()(_context({}))
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.blocking


def test_adapter_emits_declared_outputs_without_blocking():
    result = valuation_sensitivity_adapter()(
        _context({"generic_valuation_result": _valuation(diagnostics=_diagnostics())})
    )
    assert result.status in {StageStatus.PASS, StageStatus.WARNING}
    assert not result.blocking
    assert set(result.outputs) == {
        "valuation_sensitivity_report",
        "valuation_sensitivity_hash",
        "valuation_sensitivity_summary",
    }
