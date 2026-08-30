from __future__ import annotations

from importlib import resources

from valuation_engine.doctrine_runtime import load_default_unit_contract_registry
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.sanil_live_primary import (
    load_sanil_market_snapshot,
    load_sanil_snapshot,
)


REGISTRIES = (
    "control_plane_stage_registry.yaml",
    "live_primary_readiness.yaml",
    "archetype_module_registry.yaml",
    "valuation_method_capability_registry.yaml",
    "unit_contract_registry.yaml",
    "probability_calibration_policy.yaml",
    "sanil_live_snapshot.yaml",
    "sanil_market_snapshot.yaml",
)
EXPECTED_METHOD_COUNT = 42


def main() -> int:
    package_root = resources.files("valuation_engine._registry_data")
    missing = [name for name in REGISTRIES if not package_root.joinpath(name).is_file()]
    if missing:
        raise SystemExit("missing packaged runtime registries: " + ", ".join(missing))

    method_registry = load_default_method_capability_registry()
    if method_registry.coverage_summary().total != EXPECTED_METHOD_COUNT:
        raise SystemExit("installed wheel method registry is incomplete")

    # Exercise the exact production default path used by run_controlled_workflow and Audit.
    unit_registry = load_default_unit_contract_registry()
    unit_registry.validate()
    if not unit_registry.units:
        raise SystemExit("installed wheel Unit Contract registry is empty")

    sanil = load_sanil_snapshot()
    market = load_sanil_market_snapshot()
    if sanil.company["ticker"] != "062040" or market.ticker != "062040":
        raise SystemExit("installed wheel Sanil runtime resources are invalid")

    print(
        "installed-wheel runtime OK: "
        f"registries={len(REGISTRIES)} methods={method_registry.coverage_summary().total} "
        f"units={len(unit_registry.units)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
