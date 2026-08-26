from pathlib import Path

path = Path("tests/test_live_runtime_assembly.py")
text = path.read_text(encoding="utf-8")
old = """        self.method_choices = ()
        self.market_currency = None
"""
new = """        self.method_choices = ()
        self.capacity_core_scenario_id = None
        self.market_currency = None
"""
if text.count(old) != 1:
    raise SystemExit(
        "tests/test_live_runtime_assembly.py: expected one FakeRuntimeConfig insertion point"
    )
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("runtime assembly fixture updated")
