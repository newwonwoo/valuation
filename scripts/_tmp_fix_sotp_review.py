from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"{label} source anchor not found")
    return text.replace(old, new, 1)


path = Path("src/valuation_engine/valuation_sensitivity.py")
text = path.read_text(encoding="utf-8")
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
path.write_text(text, encoding="utf-8")

report_form = Path("src/valuation_engine/report_form.py")
r = report_form.read_text(encoding="utf-8")
r = replace_once(
    r,
    '''        moves = " · ".join(
            f"{item.label} {_sensitivity_delta_ko(item.variable, item.base_input, item.high_input)}"
            f" → {item.low_value_pct * 100:+.1f}%/{item.high_value_pct * 100:+.1f}%"
            for item in scenario.variables
        )
''',
    '''        move_items = (
            tuple(
                (segment.asset_id, item)
                for segment in scenario.segments
                for item in segment.variables
            )
            if scenario.segments
            else tuple((None, item) for item in scenario.variables)
        )
        moves = " · ".join(
            f"{(asset_id + ' ') if asset_id else ''}{item.label} "
            f"{_sensitivity_delta_ko(item.variable, item.base_input, item.high_input)}"
            f" → {item.low_value_pct * 100:+.1f}%/{item.high_value_pct * 100:+.1f}%"
            for asset_id, item in move_items
        )
''',
    "user-facing SOTP sensitivity renderer",
)
report_form.write_text(r, encoding="utf-8")

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
    assert not report.scenarios[0].variables
    lines = _valuation_sensitivity_lines({"valuation_sensitivity_report": report})
    detail = next(line for line in lines if line.startswith("- Core 기준"))
    assert "FIRST 가중평균자본비용" in detail
    assert "SECOND 가중평균자본비용" in detail
    assert not detail.rstrip().endswith("—")
'''
guards.write_text(g, encoding="utf-8")
