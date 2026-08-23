from __future__ import annotations

from pathlib import Path

import yaml

from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_requirements import build_module_requirement_plan_from_repo


ROOT = Path(__file__).resolve().parents[1]


def profile(adapter_id: str, archetypes: tuple[EconomicArchetype, ...]) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id=adapter_id,
        sector_adapter=adapter_id,
        archetypes=archetypes,
        revenue_recognition="registry_validation",
        price_formation="registry_validation",
        asset_ownership="registry_validation",
        capital_intensity="registry_validation",
        regulation_intensity="registry_validation",
        customer_structure="registry_validation",
        reinvestment_model="registry_validation",
        cashflow_duration="registry_validation",
        evidence_keys=(f"VALIDATION:{adapter_id}",),
    )


def main() -> int:
    registry = yaml.safe_load(
        (ROOT / "config/sector_adapter_registry.yaml").read_text(encoding="utf-8")
    )
    adapters = registry.get("adapters", {})
    if not isinstance(adapters, dict) or not adapters:
        raise SystemExit("sector_adapter_registry.yaml has no adapters")

    plans = []
    for adapter_id, adapter in adapters.items():
        default_values = tuple(adapter.get("default_archetypes", ()))
        if not default_values:
            raise SystemExit(f"adapter has no default_archetypes: {adapter_id}")
        try:
            archetypes = tuple(EconomicArchetype(value) for value in default_values)
            plan = build_module_requirement_plan_from_repo(
                profile(adapter_id, archetypes),
                repo_root=ROOT,
            )
        except Exception as exc:
            raise SystemExit(f"plan compilation failed for {adapter_id}: {exc}") from exc
        if not plan.mandatory_scanner_ids:
            raise SystemExit(f"plan has no mandatory scanners: {adapter_id}")
        plans.append(plan)

    print(
        "module requirement plans: "
        f"{len(plans)} adapters / "
        f"{len({a for plan in plans for a in plan.archetypes})} archetypes / "
        f"{len({s.scanner_id for plan in plans for s in plan.scanners})} scanners"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
