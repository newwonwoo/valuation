from pathlib import Path

path = Path("scripts/apply_broker_live_primary_integration.py")
text = path.read_text(encoding="utf-8")
old = '''    replace_once(\n        path,\n        """      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n""",\n        """      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/broker_runtime.py'\\nn      - 'tests/test_broker_runtime.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n""",\n    )\n'''
# The generated script uses triple single quotes, so replace the exact semantic block manually.
needle = """    replace_once(\n        path,\n        '''      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n''',\n        '''      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/broker_runtime.py'\\n      - 'tests/test_broker_runtime.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n''',\n    )\n"""
if text.count(needle) != 2:
    raise SystemExit(f"expected duplicated workflow replacement twice, found {text.count(needle)}")
replacement = """    target = ROOT / path\n    workflow_text = target.read_text(encoding=\"utf-8\")\n    old_paths = \"\"\"      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n\"\"\"\n    new_paths = \"\"\"      - 'src/valuation_engine/sanil_live_primary.py'\\n      - 'src/valuation_engine/broker_runtime.py'\\n      - 'tests/test_broker_runtime.py'\\n      - 'src/valuation_engine/dcf_evaluators.py'\\n\"\"\"\n    if workflow_text.count(old_paths) != 2:\n        raise RuntimeError(\n            f\"{path}: expected two Sanil workflow path blocks, found {workflow_text.count(old_paths)}\"\n        )\n    target.write_text(workflow_text.replace(old_paths, new_paths), encoding=\"utf-8\")\n"""
text = text.replace(needle + needle, replacement, 1)
path.write_text(text, encoding="utf-8")
print("broker integration workflow patch repaired")
