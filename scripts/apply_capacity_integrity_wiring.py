from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "src" / "valuation_engine" / "live_runtime.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one live_runtime replacement, found {count}: {old[:80]!r}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if "from .capacity_runtime_integrity import (" in text:
        return 0
    consumption_import = (
        "from .capacity_consumption import (\n"
        "    CapacityBridgeConsumptionLoader,\n"
        "    capacity_bridge_consumption_gate_adapter,\n"
        ")\n"
    )
    text = replace_once(
        text,
        consumption_import,
        consumption_import
        + "from .capacity_runtime_integrity import (\n"
        + "    capacity_audit_adapter,\n"
        + "    capacity_per_binding_adapter,\n"
        + "    capacity_scenario_binding_adapter,\n"
        + "    capacity_valuation_binding_adapter,\n"
        + ")\n",
    )
    text = replace_once(
        text,
        "    scenario_chain.append(scenario_build_adapter())\n",
        "    scenario_chain.append(scenario_build_adapter())\n"
        "    scenario_chain.append(capacity_scenario_binding_adapter())\n",
    )
    text = replace_once(
        text,
        "    valuation = chain_stage_adapters(\n"
        "        deterministic_valuation_adapter(\n"
        "            registry_loader=providers.evaluator_registry_loader,\n"
        "            plan_loader=_valuation_plan_loader(config, capability_registry),\n"
        "        ),\n"
        "        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),\n"
        "    )\n",
        "    valuation = chain_stage_adapters(\n"
        "        deterministic_valuation_adapter(\n"
        "            registry_loader=providers.evaluator_registry_loader,\n"
        "            plan_loader=_valuation_plan_loader(config, capability_registry),\n"
        "        ),\n"
        "        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),\n"
        "        capacity_valuation_binding_adapter(),\n"
        "    )\n",
    )
    text = replace_once(
        text,
        "    per = conditional_warranted_per_adapter(\n"
        "        live_hierarchical_warranted_per_adapter(loader=providers.per_loader)\n"
        "        if providers.per_loader is not None\n"
        "        else None\n"
        "    )\n",
        "    per = chain_stage_adapters(\n"
        "        conditional_warranted_per_adapter(\n"
        "            live_hierarchical_warranted_per_adapter(loader=providers.per_loader)\n"
        "            if providers.per_loader is not None\n"
        "            else None\n"
        "        ),\n"
        "        capacity_per_binding_adapter(),\n"
        "    )\n",
    )
    text = replace_once(
        text,
        "        \"AUDIT_GATE\": generic_audit_adapter(\n"
        "            impact_config=config.impact_config,\n"
        "            unit_contract_registry=effective_unit_contract_registry,\n"
        "        ),\n",
        "        \"AUDIT_GATE\": chain_stage_adapters(\n"
        "            capacity_audit_adapter(),\n"
        "            generic_audit_adapter(\n"
        "                impact_config=config.impact_config,\n"
        "                unit_contract_registry=effective_unit_contract_registry,\n"
        "            ),\n"
        "        ),\n",
    )
    PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
