from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DCF_PATH = ROOT / "src" / "valuation_engine" / "dcf_evaluators.py"
SANIL_PATH = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
SNAPSHOT_PATH = ROOT / "config" / "sanil_live_snapshot.yaml"
TEST_PATH = ROOT / "tests" / "test_sanil_live_primary.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"{path.relative_to(ROOT)} patch target not found: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_dcf() -> None:
    for _ in range(2):
        replace_once(
            DCF_PATH,
            '''    additional_expansion_capex: tuple[tuple[str, int], ...] = ()\n''',
            '''    additional_expansion_capex: tuple[tuple[str, int], ...] = ()\n    trace_assumption_keys: tuple[str, ...] = ()\n''',
        )
    for _ in range(2):
        replace_once(
            DCF_PATH,
            '''        _validate_capex_entries(\n            forecast_years=self.forecast_years,\n            primary_key=self.expansion_capex_key,\n            primary_year=self.expansion_capex_year,\n            additional=self.additional_expansion_capex,\n        )\n''',
            '''        _validate_capex_entries(\n            forecast_years=self.forecast_years,\n            primary_key=self.expansion_capex_key,\n            primary_year=self.expansion_capex_year,\n            additional=self.additional_expansion_capex,\n        )\n        for key in self.trace_assumption_keys:\n            _validate_relative_key(key, "trace assumption key")\n        if len(self.trace_assumption_keys) != len(set(self.trace_assumption_keys)):\n            raise ValueError("trace assumption keys must be unique")\n''',
        )
    replace_once(
        DCF_PATH,
        '''        capex_keys = tuple(self._key(key) for key, _ in self._capex_entries())\n        return (\n            *base_fcff,\n            *additive_fcff,\n            *capex_keys,\n''',
        '''        capex_keys = tuple(self._key(key) for key, _ in self._capex_entries())\n        trace_keys = tuple(self._key(key) for key in self.trace_assumption_keys)\n        return (\n            *base_fcff,\n            *additive_fcff,\n            *capex_keys,\n            *trace_keys,\n''',
    )
    replace_once(
        DCF_PATH,
        '''        terminal_growth_assumption = scenario.get(self._key("terminal_growth"))\n''',
        '''        trace_assumptions = tuple(\n            scenario.get(self._key(key)) for key in self.trace_assumption_keys\n        )\n\n        terminal_growth_assumption = scenario.get(self._key("terminal_growth"))\n''',
    )
    replace_once(
        DCF_PATH,
        '''            *(item.measure.as_of for item in capex_assumptions),\n            terminal_growth_assumption.measure.as_of,\n''',
        '''            *(item.measure.as_of for item in capex_assumptions),\n            *(item.measure.as_of for item in trace_assumptions),\n            terminal_growth_assumption.measure.as_of,\n''',
    )
    replace_once(
        DCF_PATH,
        '''                    *(item.economic_path_id for item in capex_assumptions),\n                    terminal_growth_assumption.economic_path_id,\n''',
        '''                    *(item.economic_path_id for item in capex_assumptions),\n                    *(item.economic_path_id for item in trace_assumptions),\n                    terminal_growth_assumption.economic_path_id,\n''',
    )
    replace_once(
        DCF_PATH,
        '''                    additional_expansion_capex=item.additional_expansion_capex,\n''',
        '''                    additional_expansion_capex=item.additional_expansion_capex,\n                    trace_assumption_keys=item.trace_assumption_keys,\n''',
    )


def patch_snapshot() -> None:
    payload = yaml.safe_load(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    values = {"Down": 3.0, "Core": 2.0, "Bull": 1.5}
    for scenario, ramp_years in values.items():
        payload["scenarios"][scenario]["uhv_ramp_years"] = ramp_years
    SNAPSHOT_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def patch_sanil() -> None:
    replace_once(
        SANIL_PATH,
        '''            if float(row.get("uhv_property_capex_krw_billion", 0)) <= 0:\n                raise ValueError(f"{name} requires positive UHV property CAPEX")\n''',
        '''            if float(row.get("uhv_property_capex_krw_billion", 0)) <= 0:\n                raise ValueError(f"{name} requires positive UHV property CAPEX")\n            if float(row.get("uhv_ramp_years", 0)) <= 0:\n                raise ValueError(f"{name} requires positive UHV ramp duration")\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        for key in ("terminal_growth", "terminal_roic"):\n''',
        '''        rows.append(\n            _record(\n                snapshot,\n                metric=f"model_{scenario.lower()}_uhv_ramp_years",\n                value=inputs["uhv_ramp_years"],\n                unit="years",\n                source_key=source,\n                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,\n                effective_date=snapshot.cutoff,\n                confidence=(0.45 if scenario != "Core" else 0.55),\n                notes=(\n                    "Bounded duration from property closing to a stabilized UHV capacity "\n                    "contribution; not company guidance."\n                ),\n            )\n        )\n        for key in ("terminal_growth", "terminal_roic"):\n''',
    )
    special_ramp = '''            if scenario == "Core" and year == 3:\n                bridge_id = "B:SANIL:UHV:RAMP"\n                path = f"{UHV_CAPACITY_PATH_ROOT}:ramp"\n                evidence_ids = (\n                    _uhv_evidence_id("ramp_boundary"),\n                    _evidence_id(metric),\n                )\n                hypothesis = "H:SANIL:UHV_CAPACITY"\n            elif scenario == "Core" and year == FORECAST_YEARS:\n'''
    replace_once(
        SANIL_PATH,
        special_ramp,
        '''            if scenario == "Core" and year == FORECAST_YEARS:\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        uhv_capex_metric = f"model_{scenario.lower()}_uhv_property_capex"\n''',
        '''        uhv_ramp_metric = f"model_{scenario.lower()}_uhv_ramp_years"\n        uhv_ramp_value = float(\n            context.ledger.get(_evidence_id(uhv_ramp_metric)).value\n        )\n        uhv_ramp_bridge_id = f"B:SANIL:{scenario}:uhv_ramp_years"\n        uhv_ramp_path = f"sanil:{scenario.lower()}:uhv_ramp_years"\n        uhv_ramp_evidence_ids = (_evidence_id(uhv_ramp_metric),)\n        uhv_ramp_hypothesis = f"H:SANIL:{scenario}"\n        if scenario == "Core":\n            uhv_ramp_bridge_id = "B:SANIL:UHV:RAMP"\n            uhv_ramp_path = f"{UHV_CAPACITY_PATH_ROOT}:ramp"\n            uhv_ramp_evidence_ids = (\n                _uhv_evidence_id("ramp_boundary"),\n                _evidence_id(uhv_ramp_metric),\n            )\n            uhv_ramp_hypothesis = "H:SANIL:UHV_CAPACITY"\n        drafts.append(\n            BridgeDraft(\n                assumption_key="uhv_ramp_years",\n                scenario_id=scenario,\n                bridge=_bridge(\n                    bridge_id=uhv_ramp_bridge_id,\n                    evidence_ids=uhv_ramp_evidence_ids,\n                    hypothesis_id=uhv_ramp_hypothesis,\n                    affected_variable=AffectedVariable.QUANTITY,\n                    direction=Direction.UP,\n                    old_value=0.0,\n                    new_value=uhv_ramp_value,\n                    unit="years",\n                    economic_path_id=uhv_ramp_path,\n                    rationale=(\n                        "separate time-domain ramp assumption prevents FCFF money from "\n                        "masquerading as a Capacity ramp input"\n                    ),\n                ),\n                canonical_unit="years",\n                transform_id="identity_observation",\n                input_evidence_ids=(_evidence_id(uhv_ramp_metric),),\n                min_value="0",\n            )\n        )\n\n        uhv_capex_metric = f"model_{scenario.lower()}_uhv_property_capex"\n''',
    )
    replace_once(
        SANIL_PATH,
        '''                    additional_expansion_capex=(("uhv_property_capex", 2),),\n''',
        '''                    additional_expansion_capex=(("uhv_property_capex", 2),),\n                    trace_assumption_keys=("uhv_ramp_years",),\n''',
    )
    replace_once(
        SANIL_PATH,
        '''                "uhv_property_capex",\n                "terminal_growth",\n''',
        '''                "uhv_property_capex",\n                "uhv_ramp_years",\n                "terminal_growth",\n''',
    )


def patch_test() -> None:
    replace_once(
        TEST_PATH,
        '''    assert compiled.get("uhv_fcff_year_5", "Core").measure.amount == 42\n''',
        '''    assert compiled.get("uhv_fcff_year_5", "Core").measure.amount == 42\n    assert compiled.get("uhv_ramp_years", "Core").measure.amount == 2\n''',
    )


def main() -> int:
    patch_dcf()
    patch_snapshot()
    patch_sanil()
    patch_test()
    print("UHV ramp separated into a typed time-domain trace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
