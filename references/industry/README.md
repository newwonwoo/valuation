# Industry Operator References

> **Canonical routing is not this directory.** v0.5.2 routes `segment → Industry DNA → Economic Archetype(s) → Sector Adapter` using `config/industry_taxonomy.yaml`, `config/archetype_module_registry.yaml`, and `config/sector_adapter_registry.yaml`. These Markdown files are operator supplements for accounting quirks, practical KPIs and diligence questions.

## Routing order

1. Decompose economically distinct segments.
2. Read revenue-recognition, asset ownership, contract structure, price formation, capital intensity, regulation, reinvestment model and cash-flow duration.
3. Assign one or more Economic Archetypes from evidence; keyword/industry-name matching cannot finalize the route.
4. Use the Sector Adapter as a default evidence/KPI map, not as authority.
5. Compile the Module Requirement Plan before collecting valuation inputs.
6. If no supported archetype can be established or a material archetype forbids the proposed method, fail closed rather than falling back to generic DCF.

## Manual supplements in this directory

| File | Useful for | Canonical v0.5.2 mapping / caution |
|---|---|---|
| `order-equipment.md` | order/backlog equipment | usually `contracted_backlog + capacity_manufacturing`; short/long-cycle distinction required |
| `construction-shipbuilding.md` | over-time project accounting | revenue-recognition/cost-to-complete supplement; not “backlog = revenue” |
| `consumer-retail.md` | stores/retail/franchise | `consumer_unit_economics`, sometimes `recurring_subscription` |
| `platform-game.md` | legacy broad platform/game notes | v0.5.2 splits SaaS, usage cloud, marketplace, gaming and advertising; registry wins |
| `pharma-bio.md` | pipeline-dominant / commercial bio | use Clinical Evidence Gate and healthcare adapters; fixed phase labels are insufficient |
| `financials.md` | legacy financial-sector overview | bank/life/P&C/securities/asset-manager/exchange are separate adapters; registry wins |
| `capital-intensive.md` | capacity-heavy manufacturing | `capacity_manufacturing` operator checklist |
| `materials-parts.md` | materials/consumables/parts | capacity, process-spread and/or commodity archetypes |
| `holding-company.md` | conglomerate/SOTP | segment route + SOTP aggregation; no generic holding discount |
| `reit-realestate.md` | REIT/property | `real_assets.reit` / `asset_yield_nav` |

The supplied `utilities-telecom.md` and `techbio-platform.md` are intentionally **not adopted as canonical modules** because they combine economically different businesses that v0.5.2 routes separately or multi-labels. Their useful ideas should be expressed through the relevant archetypes/adapters, not by reintroducing coarse single-label routing.

## Precedence

If an operator reference conflicts with `SKILL.md`, `AGENTS.md`, `docs/V04_ROCKETSLA_EXTENSION.md`, `docs/V05_WORKFLOW_CONTRACT.md`, deterministic code, or the v0.5.2 registries, the canonical contract wins. Do not copy a simplified rule into valuation assumptions merely because it appears in a reference file.
