from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = Path("src/valuation_engine/live_runtime.py")
replace_once(
    runtime,
    '''            loader=providers.broker_research_loader,\n            require_broker_research=config.require_broker_research,\n''',
    '''            loader=getattr(providers, "broker_research_loader", None),\n            require_broker_research=bool(\n                getattr(config, "require_broker_research", False)\n            ),\n''',
)
replace_once(
    runtime,
    '''            broker_research_audit_adapter(required=config.require_broker_research),\n''',
    '''            broker_research_audit_adapter(\n                required=bool(getattr(config, "require_broker_research", False))\n            ),\n''',
)

report = Path("src/valuation_engine/report_form.py")
text = report.read_text(encoding="utf-8")
marker = "def render_controlled_run_report("
if text.count(marker) != 1:
    raise SystemExit("report_form.py: render_controlled_run_report marker missing")
head, tail = text.split(marker, 1)
old = '''    data = result.data\n    lines = [\n'''
new = '''    data = result.data\n    broker_configured = bool(data.get("broker_research_required", False)) or (\n        data.get("broker_research_prefreeze_result") is not None\n    )\n    lines = [\n'''
if tail.count(old) != 1:
    raise SystemExit(
        "report_form.py: expected one renderer data/lines block, "
        f"found {tail.count(old)}"
    )
report.write_text(head + marker + tail.replace(old, new, 1), encoding="utf-8")

print("post-patch Broker Research regressions repaired")
