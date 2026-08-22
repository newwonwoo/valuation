from pathlib import Path

from valuation_engine.unit_contracts import load_unit_contract_registry


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "config" / "unit_contract_registry.yaml"
    registry = load_unit_contract_registry(registry_path)

    missing_refs: list[str] = []
    for unit in registry.units:
        for ref in unit.canonical_refs:
            # Only repository-relative refs can be validated locally. Symbolic references
            # and future/runtime identifiers are intentionally not treated as paths.
            if "/" in ref or ref.endswith((".md", ".py", ".yaml", ".yml")):
                candidate = root / ref
                if not candidate.exists():
                    missing_refs.append(f"{unit.unit_id}:{ref}")

    if missing_refs:
        raise SystemExit("missing canonical refs: " + ", ".join(missing_refs))

    print(
        "PASS unit_contract_registry "
        f"units={len(registry.units)} "
        f"value_effect_units={len(registry.units_affecting('value_effect'))} "
        f"guardrail_units={len(registry.units_affecting('guardrail_effect'))}"
    )


if __name__ == "__main__":
    main()
