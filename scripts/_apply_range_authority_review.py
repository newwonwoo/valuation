from __future__ import annotations

from pathlib import Path


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise SystemExit(f"patch marker missing: {label}")


def patch_range_rules() -> None:
    path = Path("src/valuation_engine/assumption_range_rules.py")
    text = path.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        '''def load_assumption_range_rule_registry(\n    path: str | Path = DEFAULT_RANGE_RULE_REGISTRY_PATH,\n) -> AssumptionRangeRuleRegistry:\n    raw = Path(path).read_text(encoding="utf-8")\n''',
        '''def load_assumption_range_rule_registry(\n    path: str | Path | None = None,\n) -> AssumptionRangeRuleRegistry:\n    resolved_path = DEFAULT_RANGE_RULE_REGISTRY_PATH if path is None else Path(path)\n    raw = Path(resolved_path).read_text(encoding="utf-8")\n''',
        "dynamic canonical registry loader",
    )

    marker = "def apply_reviewed_assumption_ranges(\n"
    helper = '''def derive_reviewed_assumption_range(\n    rule: AssumptionRangeRule,\n    *,\n    ledger: EvidenceLedger,\n    target_id: str,\n    scenario_id: str,\n    registry_hash: str,\n) -> AssumptionRangeReceipt:\n    """Re-derive one reviewed range from authoritative filing history."""\n    rule.validate()\n    if not target_id or not scenario_id or not registry_hash:\n        raise AssumptionRangeRuleError(\n            "range derivation requires target, scenario and registry hash"\n        )\n    active = tuple(item for item in ledger.active() if item.target == target_id)\n    candidates = tuple(\n        item\n        for item in active\n        if item.metric == rule.anchor_metric\n        and item.source_layer in rule.source_layers\n    )\n    by_date: dict[str, list] = {}\n    for item in candidates:\n        canonical_date = date.fromisoformat(item.effective_date[:10]).isoformat()\n        by_date.setdefault(canonical_date, []).append(item)\n    observations: list[tuple[str, Decimal, str, str]] = []\n    for canonical_date in sorted(by_date, reverse=True):\n        rows = by_date[canonical_date]\n        amounts: dict[Decimal, list] = {}\n        for item in rows:\n            measure = measure_from_raw(\n                item.value, item.unit, item.effective_date\n            ).convert_to(rule.canonical_unit)\n            amounts.setdefault(measure.amount, []).append(item)\n        if len(amounts) != 1:\n            raise AssumptionRangeRuleError(\n                f"range rule {rule.rule_id} has ambiguous {rule.anchor_metric} "\n                f"filing values for {canonical_date}"\n            )\n        amount, matching_rows = next(iter(amounts.items()))\n        chosen = sorted(matching_rows, key=lambda item: item.id)[0]\n        observations.append(\n            (canonical_date, amount, chosen.id, chosen.effective_date)\n        )\n        if len(observations) >= rule.lookback_observations:\n            break\n    if len(observations) < rule.min_observations:\n        raise AssumptionRangeRuleError(\n            f"range rule {rule.rule_id} requires {rule.min_observations} filing "\n            f"observations of {rule.anchor_metric}; found {len(observations)}"\n        )\n    values = tuple(item[1] for item in observations)\n    lower = min(values) * rule.lower_multiplier\n    upper = max(values) * rule.upper_multiplier\n    if rule.floor is not None:\n        lower = max(lower, rule.floor)\n    if rule.ceiling is not None:\n        upper = min(upper, rule.ceiling)\n    if lower > upper:\n        raise AssumptionRangeRuleError(\n            f"range rule {rule.rule_id} derived inverted bounds {lower}>{upper}"\n        )\n    return AssumptionRangeReceipt(\n        target_id=target_id,\n        rule_id=rule.rule_id,\n        assumption_key=rule.assumption_key,\n        scenario_id=scenario_id,\n        anchor_metric=rule.anchor_metric,\n        min_value=lower,\n        max_value=upper,\n        anchor_evidence_ids=tuple(item[2] for item in observations),\n        anchor_values=values,\n        anchor_effective_dates=tuple(item[3] for item in observations),\n        canonical_unit=rule.canonical_unit,\n        registry_hash=registry_hash,\n        review_ref=rule.review_ref,\n    )\n\n\n'''
    if "def derive_reviewed_assumption_range(" not in text:
        if marker not in text:
            raise SystemExit("patch marker missing: range apply")
        text = text.replace(marker, helper + marker, 1)

    start = text.index("def apply_reviewed_assumption_ranges(")
    end = text.index("\n\ndef range_receipts_hash", start)
    replacement = '''def apply_reviewed_assumption_ranges(\n    specs: tuple[AssumptionSpec, ...],\n    *,\n    ledger: EvidenceLedger,\n    target_id: str,\n    registry: AssumptionRangeRuleRegistry,\n    llm_bounds: tuple[tuple[str, str, str | None, str | None], ...] = (),\n) -> RangeApplicationResult:\n    """Attach only reviewed, evidence-derived bounds to compiler specs."""\n    registry.validate()\n    if not target_id:\n        raise AssumptionRangeRuleError("range application requires target_id")\n    receipts: list[AssumptionRangeReceipt] = []\n    resolved: list[AssumptionSpec] = []\n    for spec in specs:\n        rule = registry.for_key(spec.key)\n        if rule is None:\n            resolved.append(replace(spec, min_value=None, max_value=None))\n            continue\n        if spec.canonical_unit != rule.canonical_unit:\n            raise AssumptionRangeRuleError(\n                f"range rule {rule.rule_id} unit {rule.canonical_unit} does not match "\n                f"assumption {spec.key} unit {spec.canonical_unit}"\n            )\n        receipt = derive_reviewed_assumption_range(\n            rule,\n            ledger=ledger,\n            target_id=target_id,\n            scenario_id=spec.scenario_id,\n            registry_hash=registry.registry_hash,\n        )\n        resolved.append(\n            replace(spec, min_value=receipt.min_value, max_value=receipt.max_value)\n        )\n        receipts.append(receipt)\n    return RangeApplicationResult(\n        specs=tuple(resolved),\n        receipts=tuple(receipts),\n        ignored_llm_bounds=llm_bounds,\n    )\n'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def patch_compiler() -> None:
    path = Path("src/valuation_engine/assumption_compiler.py")
    text = path.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        '''    specs: tuple[AssumptionSpec, ...],\n    bridge_input_map: dict[str, tuple[str, ...]],\n    range_rule_registry_path=None,\n) -> CompilationResult:\n''',
        '''    specs: tuple[AssumptionSpec, ...],\n    bridge_input_map: dict[str, tuple[str, ...]],\n) -> CompilationResult:\n''',
        "compiler registry override signature",
    )
    text = _replace_once(
        text,
        '''        range_registry = load_assumption_range_rule_registry(\n            range_rule_registry_path\n            if range_rule_registry_path is not None\n            else DEFAULT_RANGE_RULE_REGISTRY_PATH\n        )\n''',
        '''        range_registry = load_assumption_range_rule_registry()\n''',
        "compiler canonical registry load",
    )
    text = text.replace("    DEFAULT_RANGE_RULE_REGISTRY_PATH,\n", "", 1)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def patch_run_hash() -> None:
    path = Path("src/valuation_engine/run_hash.py")
    text = path.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        '''from .assumption_range_rules import (\n    AssumptionRangeReceipt,\n    range_provenance_hash_part,\n)\n''',
        '''from .assumption_range_rules import (\n    AssumptionRangeReceipt,\n    AssumptionRangeRuleError,\n    derive_reviewed_assumption_range,\n    load_assumption_range_rule_registry,\n    range_provenance_hash_part,\n)\n''',
        "run hash range imports",
    )
    start = text.index("def compiled_evidence_hash_mismatches(")
    replacement = '''def compiled_evidence_hash_mismatches(\n    compiled: CompiledAssumptionSet,\n    ledger: EvidenceLedger,\n) -> tuple[str, ...]:\n    """Return scenario/key identities whose compiled Evidence hash no longer replays."""\n    mismatches: list[str] = []\n    try:\n        registry = load_assumption_range_rule_registry()\n    except (AssumptionRangeRuleError, OSError, ValueError):\n        registry = None\n    for item in compiled.assumptions:\n        try:\n            replay_range = None\n            receipt = item.range_provenance\n            if receipt is not None:\n                if registry is None:\n                    raise ValueError("reviewed range registry unavailable at Audit")\n                if receipt.target_id != compiled.target_id:\n                    raise ValueError("range provenance target differs from compiled target")\n                if receipt.assumption_key != item.key:\n                    raise ValueError("range provenance assumption key mismatch")\n                if receipt.scenario_id != item.scenario_id:\n                    raise ValueError("range provenance scenario mismatch")\n                if receipt.canonical_unit != item.measure.unit:\n                    raise ValueError("range provenance canonical unit mismatch")\n                if receipt.registry_hash != registry.registry_hash:\n                    raise ValueError("range provenance registry hash mismatch")\n                rule = registry.for_key(item.key)\n                if rule is None:\n                    raise ValueError("compiled range has no reviewed canonical rule")\n                replay_range = derive_reviewed_assumption_range(\n                    rule,\n                    ledger=ledger,\n                    target_id=compiled.target_id,\n                    scenario_id=item.scenario_id,\n                    registry_hash=registry.registry_hash,\n                )\n                if replay_range != receipt:\n                    raise ValueError("range provenance differs from canonical re-derivation")\n            replayed = compiled_input_evidence_hash(\n                ledger,\n                item.evidence_ids,\n                range_provenance=replay_range,\n            )\n        except (AssumptionRangeRuleError, ValueError):\n            mismatches.append(f"{item.scenario_id}/{item.key}")\n            continue\n        if replayed != item.input_evidence_hash:\n            mismatches.append(f"{item.scenario_id}/{item.key}")\n    return tuple(mismatches)\n'''
    text = text[:start] + replacement
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/test_assumption_range_rules.py")
    text = path.read_text(encoding="utf-8")
    if "import valuation_engine.assumption_range_rules as range_rules_module\n" not in text:
        text = text.replace(
            "import pytest\n\n",
            "import pytest\n\nimport valuation_engine.assumption_range_rules as range_rules_module\n",
            1,
        )
    text = text.replace(
        "def test_compiler_ignores_unreviewed_llm_bounds_but_enforces_reviewed_rule(tmp_path):",
        "def test_compiler_ignores_unreviewed_llm_bounds_but_enforces_reviewed_rule(tmp_path, monkeypatch):",
        1,
    )
    marker = "    # A reviewed filing-history rule derives 0.70..0.90 and blocks 0.95.\n"
    injection = '''    # Test-only monkeypatch replaces the canonical frozen registry; production\n    # compilation exposes no registry-path override.\n    monkeypatch.setattr(\n        range_rules_module,\n        "DEFAULT_RANGE_RULE_REGISTRY_PATH",\n        _rule_file(tmp_path),\n    )\n'''
    if injection not in text:
        text = text.replace(marker, injection + marker, 1)
    text = text.replace(
        "        bridge_input_map={},\n        range_rule_registry_path=_rule_file(tmp_path),\n",
        "        bridge_input_map={},\n",
        1,
    )
    path.write_text(text.rstrip() + "\n", encoding="utf-8")

    path = Path("tests/test_assumption_range_review_regressions.py")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "import valuation_engine.assumption_compiler as assumption_compiler_module\n",
        "import valuation_engine.assumption_range_rules as range_rules_module\n",
        1,
    )
    text = text.replace(
        "        assumption_compiler_module,\n        \"DEFAULT_RANGE_RULE_REGISTRY_PATH\",",
        "        range_rules_module,\n        \"DEFAULT_RANGE_RULE_REGISTRY_PATH\",",
        1,
    )
    path.write_text(text.rstrip() + "\n", encoding="utf-8")

    path = Path("tests/test_assumption_range_authority_regressions.py")
    text = path.read_text(encoding="utf-8")
    begin = text.find("\ndef _forge_compiled(")
    if begin != -1:
        end = text.find(
            "\n\ndef test_compile_api_has_no_runtime_registry_path_override", begin
        )
        text = text[:begin] + text[end:]
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    patch_range_rules()
    patch_compiler()
    patch_run_hash()
    patch_tests()
