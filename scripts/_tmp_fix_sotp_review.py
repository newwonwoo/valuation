from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"{label} source anchor not found")
    return text.replace(old, new, 1)


path = Path("src/valuation_engine/valuation_sensitivity.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from dataclasses import dataclass\n",
    "from dataclasses import dataclass, replace\n",
    "dataclass import",
)
text = replace_once(
    text,
    """    @property
    def dominant(self) -> VariableSensitivity | None:
        candidates = list(self.variables)
        candidates.extend(
            variable for segment in self.segments for variable in segment.variables
        )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.max_abs_pct)
""",
    """    @property
    def dominant(self) -> VariableSensitivity | None:
        candidates = (
            [
                variable
                for segment in self.segments
                for variable in segment.variables
            ]
            if self.segments
            else list(self.variables)
        )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.max_abs_pct)
""",
    "ScenarioSensitivity.dominant",
)
text = replace_once(
    text,
    """    except (TypeError, ValueError):
        return None
    if diluted_shares <= 0:
""",
    """    except (TypeError, ValueError):
        return None
    if diagnostics.execution_family != "explicit_fcff_dcf":
        return None
    if diluted_shares <= 0:
""",
    "execution family guard",
)
text = replace_once(
    text,
    """        base_value_per_share=base_value_per_share,
        segments=segments,
    )
""",
    """        base_value_per_share=base_value_per_share,
        variables=tuple(
            replace(variable, label=f"{segment.asset_id} {variable.label}")
            for segment in segments
            for variable in segment.variables
        ),
        segments=segments,
    )
""",
    "SOTP rendered variables",
)
path.write_text(text, encoding="utf-8")

guards = Path("tests/test_sotp_segment_sensitivity_guards.py")
g = guards.read_text(encoding="utf-8")
if "from dataclasses import replace\n" not in g:
    g = g.replace(
        "from decimal import Decimal\n",
        "from dataclasses import replace\nfrom decimal import Decimal\n",
        1,
    )
if "from valuation_engine.report_form import _valuation_sensitivity_lines\n" not in g:
    g = g.replace(
        "from valuation_engine.sotp import AggregationComponent\n",
        "from valuation_engine.report_form import _valuation_sensitivity_lines\n"
        "from valuation_engine.sotp import AggregationComponent\n",
        1,
    )
if "def test_non_dcf_execution_family_is_not_perturbed()" not in g:
    g += '''

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
    lines = _valuation_sensitivity_lines({"valuation_sensitivity_report": report})
    detail = next(line for line in lines if line.startswith("- Core 기준"))
    assert "FIRST 가중평균자본비용" in detail
    assert "SECOND 가중평균자본비용" in detail
    assert not detail.rstrip().endswith("—")
'''
guards.write_text(g, encoding="utf-8")
