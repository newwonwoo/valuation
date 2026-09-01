from __future__ import annotations

from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.evaluator_registry import SegmentValuationDiagnostics
from valuation_engine.reverse_dcf import ReconstructionStatus
from valuation_engine.sotp import (
    AggregationComponent,
    CompanyScenarioEquityValue,
    ScenarioEquityAggregation,
)
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue
from valuation_engine.valuation_sensitivity import (
    FCFF_LEVEL,
    build_valuation_sensitivity_report,
)


ONE = Decimal("1")
SHARES = Decimal("1000")
UNIT = "KRW_billion"


def _diagnostics(
    fcff_path: tuple[Decimal, ...],
    *,
    rate: Decimal = Decimal("0.09"),
    growth: Decimal = Decimal("0.02"),
    tampered: bool = False,
) -> SegmentValuationDiagnostics:
    explicit = sum(
        (fcff / (ONE + rate) ** year for year, fcff in enumerate(fcff_path, start=1)),
        Decimal("0"),
    )
    terminal = (
        fcff_path[-1]
        * (ONE + growth)
        / (rate - growth)
        / (ONE + rate) ** len(fcff_path)
    )
    if tampered:
        explicit += Decimal("100")
    return SegmentValuationDiagnostics(
        execution_family="explicit_fcff_dcf",
        value_unit=UNIT,
        discount_rate=rate,
        forecast_years=len(fcff_path),
        fcff_path=fcff_path,
        present_value_explicit=explicit,
        present_value_terminal=terminal,
        terminal_growth=growth,
        terminal_roic=Decimal("0.15"),
    )


def _dcf_component(
    asset_id: str,
    diagnostics: SegmentValuationDiagnostics,
    *,
    ownership: Decimal = ONE,
    bridge: Decimal = Decimal("0"),
) -> AggregationComponent:
    full_equity = diagnostics.enterprise_value + bridge
    return AggregationComponent(
        asset_id=asset_id,
        contribution_id=f"{asset_id}:dcf:v1",
        attributable_equity_value=Measure(
            ownership * full_equity,
            UNIT,
            "2026-08-27",
        ),
        economic_path_ids=(f"path:{asset_id}",),
        ownership_ratio=ownership,
        diagnostics=diagnostics,
    )


def _constant_component(asset_id: str, amount: Decimal) -> AggregationComponent:
    return AggregationComponent(
        asset_id=asset_id,
        contribution_id=f"parent:{asset_id}",
        attributable_equity_value=Measure(amount, UNIT, "2026-08-27"),
        economic_path_ids=(f"parent:{asset_id}",),
    )


def _valuation(components: tuple[AggregationComponent, ...]) -> GenericValuationResult:
    total = sum(
        (item.attributable_equity_value.amount for item in components), Decimal("0")
    )
    company = CompanyScenarioEquityValue(
        scenario_id="Core",
        equity_value=Measure(total, UNIT, "2026-08-27"),
        components=components,
        aggregation_hash="sotp-aggregation",
    )
    per_share = ScenarioPerShareValue(
        scenario_id="Core",
        equity_value_amount=total,
        reporting_unit=UNIT,
        diluted_shares=SHARES,
        value_per_share=total / SHARES,
        aggregation_hash="sotp-aggregation",
        economic_path_ids=tuple(
            path for component in components for path in component.economic_path_ids
        ),
    )
    return GenericValuationResult(
        scenarios=(per_share,),
        equity_aggregation=ScenarioEquityAggregation((company,), None, False),
        expected_value_per_share=None,
        reporting_unit=UNIT,
        valuation_hash="sotp-valuation-hash",
    )


def test_dcf_plus_parent_adjustment_is_measured_as_segment_sensitivity():
    core = _dcf_component(
        "CORE",
        _diagnostics((Decimal("100"), Decimal("110"), Decimal("120"))),
    )
    parent = _constant_component("PARENT", Decimal("500"))
    report = build_valuation_sensitivity_report(valuation=_valuation((core, parent)))
    scenario = report.scenarios[0]

    assert scenario.status == ReconstructionStatus.RECONSTRUCTED
    assert scenario.measured
    assert not scenario.variables
    assert len(scenario.segments) == 1
    assert scenario.segments[0].asset_id == "CORE"
    assert {item.variable for item in scenario.segments[0].variables} >= {FCFF_LEVEL}


def test_two_dcf_sotp_components_get_separate_sensitivity_records():
    core = _dcf_component(
        "CORE",
        _diagnostics((Decimal("100"), Decimal("110"), Decimal("120"))),
    )
    logistics = _dcf_component(
        "LOGISTICS",
        _diagnostics((Decimal("40"), Decimal("45"), Decimal("50"))),
        ownership=Decimal("0.75"),
        bridge=Decimal("30"),
    )
    report = build_valuation_sensitivity_report(
        valuation=_valuation((core, logistics))
    )
    scenario = report.scenarios[0]

    assert scenario.measured
    assert tuple(item.asset_id for item in scenario.segments) == (
        "CORE",
        "LOGISTICS",
    )
    assert all(item.variables for item in scenario.segments)


def test_perturbing_one_sotp_dcf_holds_other_components_constant():
    core = _dcf_component(
        "CORE",
        _diagnostics((Decimal("100"), Decimal("110"), Decimal("120"))),
    )
    logistics = _dcf_component(
        "LOGISTICS",
        _diagnostics((Decimal("40"), Decimal("45"), Decimal("50"))),
    )
    parent = _constant_component("PARENT", Decimal("300"))
    valuation = _valuation((core, logistics, parent))
    report = build_valuation_sensitivity_report(valuation=valuation)
    segment = next(item for item in report.scenarios[0].segments if item.asset_id == "CORE")
    level = next(item for item in segment.variables if item.variable == FCFF_LEVEL)

    expected_high_total = (
        valuation.scenarios[0].equity_value_amount
        + core.attributable_equity_value.amount * Decimal("0.10")
    )
    assert abs(
        level.high_value_per_share - expected_high_total / SHARES
    ) < Decimal("1e-18")
    assert level.high_value_pct < Decimal("0.10")


def test_tampered_dcf_component_is_skipped_when_another_component_is_reconstructible():
    bad = _dcf_component(
        "BAD",
        _diagnostics(
            (Decimal("100"), Decimal("110"), Decimal("120")), tampered=True
        ),
    )
    good = _dcf_component(
        "GOOD",
        _diagnostics((Decimal("30"), Decimal("35"), Decimal("40"))),
    )
    report = build_valuation_sensitivity_report(valuation=_valuation((bad, good)))
    scenario = report.scenarios[0]

    assert scenario.measured
    assert tuple(item.asset_id for item in scenario.segments) == ("GOOD",)


def test_non_dcf_sotp_remains_not_measurable():
    report = build_valuation_sensitivity_report(
        valuation=_valuation(
            (
                _constant_component("NAV", Decimal("700")),
                _constant_component("PARENT", Decimal("100")),
            )
        )
    )
    scenario = report.scenarios[0]
    assert not scenario.measured
    assert not scenario.segments


def test_sotp_segment_sensitivity_changes_report_hash():
    first = build_valuation_sensitivity_report(
        valuation=_valuation(
            (
                _dcf_component(
                    "CORE",
                    _diagnostics((Decimal("100"), Decimal("110"), Decimal("120"))),
                ),
                _dcf_component(
                    "OTHER",
                    _diagnostics((Decimal("40"), Decimal("45"), Decimal("50"))),
                ),
            )
        )
    )
    second = build_valuation_sensitivity_report(
        valuation=_valuation(
            (
                _dcf_component(
                    "CORE",
                    _diagnostics((Decimal("100"), Decimal("110"), Decimal("120"))),
                ),
                _dcf_component(
                    "OTHER",
                    _diagnostics((Decimal("50"), Decimal("55"), Decimal("60"))),
                ),
            )
        )
    )
    assert first.report_hash != second.report_hash
