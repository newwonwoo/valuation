from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.report_form import _valuation_sensitivity_lines
from valuation_engine.sotp import AggregationComponent
from valuation_engine.valuation_sensitivity import build_valuation_sensitivity_report
from tests.test_sotp_segment_sensitivity import _dcf_component, _diagnostics, _valuation


class FakeDiagnostics:
    def __init__(self) -> None:
        real = _diagnostics((Decimal("10"), Decimal("11"), Decimal("12")))
        self.execution_family = real.execution_family
        self.value_unit = real.value_unit
        self.discount_rate = real.discount_rate
        self.forecast_years = real.forecast_years
        self.fcff_path = real.fcff_path
        self.present_value_explicit = real.present_value_explicit
        self.present_value_terminal = real.present_value_terminal
        self.terminal_growth = real.terminal_growth
        self.terminal_roic = real.terminal_roic

    @property
    def enterprise_value(self):
        return self.present_value_explicit + self.present_value_terminal

    def validate(self) -> None:
        return None


def test_non_finite_ownership_component_is_skipped():
    bad = AggregationComponent(
        asset_id="BAD",
        contribution_id="BAD:dcf:v1",
        attributable_equity_value=Measure(Decimal("100"), "KRW_billion", "2026-08-27"),
        economic_path_ids=("path:BAD",),
        ownership_ratio=Decimal("NaN"),
        diagnostics=_diagnostics((Decimal("10"), Decimal("11"), Decimal("12"))),
    )
    good = _dcf_component(
        "GOOD",
        _diagnostics((Decimal("30"), Decimal("35"), Decimal("40"))),
    )
    report = build_valuation_sensitivity_report(valuation=_valuation((bad, good)))
    assert tuple(item.asset_id for item in report.scenarios[0].segments) == ("GOOD",)


def test_untyped_diagnostics_object_is_not_treated_as_dcf_authority():
    fake = AggregationComponent(
        asset_id="FAKE",
        contribution_id="FAKE:dcf:v1",
        attributable_equity_value=Measure(Decimal("100"), "KRW_billion", "2026-08-27"),
        economic_path_ids=("path:FAKE",),
        ownership_ratio=Decimal("1"),
        diagnostics=FakeDiagnostics(),
    )
    good = _dcf_component(
        "GOOD",
        _diagnostics((Decimal("30"), Decimal("35"), Decimal("40"))),
    )
    report = build_valuation_sensitivity_report(valuation=_valuation((fake, good)))
    assert tuple(item.asset_id for item in report.scenarios[0].segments) == ("GOOD",)


def test_non_dcf_execution_family_is_not_perturbed():
    bad_diagnostics = replace(
        _diagnostics((Decimal("10"), Decimal("11"), Decimal("12"))),
        execution_family="normalized_multiple",
    )
    bad = _dcf_component("BAD", bad_diagnostics)
    good = _dcf_component(
        "GOOD",
        _diagnostics((Decimal("30"), Decimal("35"), Decimal("40"))),
    )
    report = build_valuation_sensitivity_report(valuation=_valuation((bad, good)))
    assert tuple(item.asset_id for item in report.scenarios[0].segments) == ("GOOD",)


def test_segment_variables_are_exposed_to_the_user_facing_renderer():
    first = _dcf_component(
        "FIRST",
        _diagnostics((Decimal("30"), Decimal("35"), Decimal("40"))),
    )
    second = _dcf_component(
        "SECOND",
        _diagnostics((Decimal("20"), Decimal("24"), Decimal("28"))),
    )
    report = build_valuation_sensitivity_report(valuation=_valuation((first, second)))
    assert not report.scenarios[0].variables
    lines = _valuation_sensitivity_lines({"valuation_sensitivity_report": report})
    detail = next(line for line in lines if line.startswith("- Core 기준"))
    assert "FIRST 가중평균자본비용" in detail
    assert "SECOND 가중평균자본비용" in detail
    assert not detail.rstrip().endswith("—")
