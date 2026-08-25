from __future__ import annotations

from importlib import resources

from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.runtime_resources import runtime_registry_path
from valuation_engine.unit_contracts import load_unit_contract_registry


REGISTRIES = (
    "control_plane_stage_registry.yaml",
    "live_primary_readiness.yaml",
    "archetype_module_registry.yaml",
    "valuation_method_capability_registry.yaml",
    "unit_contract_registry.yaml",
    "probability_calibration_policy.yaml",
)


def main() -> int:
    package_root = resources.files("valuation_engine._registry_data")
    missing = [name for name in REGISTRIES if not package_root.joinpath(name).is_file()]
    if missing:
        raise SystemExit("missing packaged runtime registries: " + ", ".join(missing))

    method_registry = load_default_method_capability_registry()
    if method_registry.coverage_summary().total != 41:
        raise SystemExit("installed wheel method registry is incomplete")

    unit_registry = load_unit_contract_registry(runtime_registry_path("unit_contract_registry.yaml"))
    unit_registry.validate()
    if not unit_registry.units:
        raise SystemExit("installed wheel Unit Contract registry is empty")

    print(
        "installed-wheel runtime OK: "
        f"registries={len(REGISTRIES)} methods={method_registry.coverage_summary().total} "
        f"units={len(unit_registry.units)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
