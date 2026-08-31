from pathlib import Path

p = Path('src/valuation_engine/evaluator_registry.py')
t = p.read_text(encoding='utf-8')
anchor = '''    def has_scoped_registrations(self) -> bool:\n        return bool(self._segment_evaluators)\n'''
addition = '''    def keys_for_segment(self, segment_id: str) -> tuple[ModelKey, ...]:\n        if not segment_id:\n            raise ValueError("segment evaluator lookup requires segment_id")\n        keys = set(self._evaluators)\n        keys.update(\n            key\n            for scoped_segment_id, key in self._segment_evaluators\n            if scoped_segment_id == segment_id\n        )\n        return tuple(\n            sorted(\n                keys,\n                key=lambda item: (item.archetype, item.method, item.version),\n            )\n        )\n\n'''
if anchor not in t:
    raise SystemExit('registry scoped anchor missing')
p.write_text(t.replace(anchor, addition + anchor, 1), encoding='utf-8')

p = Path('src/valuation_engine/valuation_plan_compiler.py')
t = p.read_text(encoding='utf-8')
old = '''        for key in evaluator_registry.keys()\n'''
new = '''        for key in evaluator_registry.keys_for_segment(segment_id)\n'''
if old not in t:
    raise SystemExit('compiler registry key iteration missing')
p.write_text(t.replace(old, new, 1), encoding='utf-8')

p = Path('tests/test_generic_valuation_plan.py')
t = p.read_text(encoding='utf-8')
old = '''    assert registry.get(key, segment_id="recycling").required_assumption_keys == (\n        "recycling_normalized_ebitda",\n        "recycling_normalized_multiple",\n    )\n    with pytest.raises(KeyError, match="no exact evaluator"):\n        registry.get(key)\n'''
new = '''    assert registry.get(key, segment_id="recycling").required_assumption_keys == (\n        "recycling_normalized_ebitda",\n        "recycling_normalized_multiple",\n    )\n    assert registry.keys_for_segment("trading") == (key,)\n    assert registry.keys_for_segment("recycling") == (key,)\n    assert registry.keys_for_segment("manufacturing") == ()\n    with pytest.raises(KeyError, match="no exact evaluator"):\n        registry.get(key)\n'''
if old not in t:
    raise SystemExit('scoped registry regression block missing')
p.write_text(t.replace(old, new, 1), encoding='utf-8')
