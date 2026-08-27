from pathlib import Path

path = Path("scripts/apply_broker_live_primary_integration.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '''            loader=providers.broker_research_loader,\n            require_broker_research=config.require_broker_research,\n''',
    '''            loader=getattr(providers, "broker_research_loader", None),\n            require_broker_research=bool(\n                getattr(config, "require_broker_research", False)\n            ),\n''',
    1,
)
text = text.replace(
    '''            broker_research_audit_adapter(required=config.require_broker_research),\n''',
    '''            broker_research_audit_adapter(\n                required=bool(getattr(config, "require_broker_research", False))\n            ),\n''',
    1,
)
needle = '''    replace_once(\n        path,\n        ''' + "'''        \"hashes\": {\\n            key: data.get(key)\\n            for key in (\\n                \"ledger_snapshot_hash\",\\n'''" + ''',\n        ''' + "'''        \"hashes\": {\\n            key: data.get(key)\\n            for key in (\\n                \"ledger_snapshot_hash\",\\n'''" + ''',\n    )\n'''
# Insert a renderer-local broker flag before the immutable identity table is assembled.
marker = '''    replace_once(\n        path,\n        ''' + "'''        \"freeze_token_hash\": getattr(token, \"token_hash\", None),\\n    }\\n    if broker_configured:\\n'''" + '''
if marker not in text:
    raise SystemExit("report patch marker not found")
insertion = '''    replace_once(\n        path,\n        ''' + "'''    data = result.data\\n    lines = [\\n'''" + ''',\n        ''' + "'''    data = result.data\\n    broker_configured = bool(data.get(\"broker_research_required\", False)) or (\\n        data.get(\"broker_research_prefreeze_result\") is not None\\n    )\\n    lines = [\\n'''" + ''',\n    )\n'''
text = text.replace(marker, insertion + marker, 1)
path.write_text(text, encoding="utf-8")
print("broker integration regression patch repaired")
