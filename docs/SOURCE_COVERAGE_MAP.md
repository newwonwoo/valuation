# Industry Knowledge Source Coverage Map v0.5 candidate

This map distinguishes **actual ingested seed coverage** from the larger source universe in the registry. The grade is deterministic source QA, not an investment rating. Grades are capped when independent-family, observed-state, structure/mechanism, freshness-watch or mechanism coverage is missing.

## Current high-value nodes

| Industry node | Seed grade | Independent families | Main remaining gap |
|---|---:|---:|---|
| power.transformers | **A-** | 3 | deepen voltage-class/order-slot history and OEM capacity |
| power.grid | B | 3 | direct observed grid/interconnection state at the same node |
| power.switchgear | B | 3 | direct observed shipment/backlog state |
| semiconductor | B | 4 | explicit broad-sector structure/mechanism evidence |
| semiconductor.memory | B | 4 | direct observed memory-state series in the seed |
| semiconductor_equipment | B | 4 | direct observed WFE/order state in the seed |
| automotive.oem | B | 2 | add independent regional registrations/inventory/incentives |
| shipping.container | B | 2 | contract coverage and fleet-supply evidence |
| shipping.dry_bulk | B | 2 | fleet-supply/orderbook and charter-cost evidence |
| mining | C+ | 1 | independent source family + mine-level costs/projects |
| oil_gas_ep | C+ | 1 | independent reserve/geology/company evidence |
| reit | C+ | 1 | primary property-state series integrated into REIT mechanism |
| commercial_real_estate | C+ | 1 | parse primary REB numeric attachment + second independent family |

## Newly verified sources in this pass

- **USGS Mineral Commodity Summaries 2026**: production/reserve/resource/import-reliance and mineral definitions; annual with revision chain.
- **NERC Long-Term Reliability Assessment**: large-load demand/resource-adequacy and infrastructure-delay structure; forecast evidence, never realized demand.
- **Korea Real Estate Board commercial-property survey**: quarterly primary survey metadata plus official property-type definitions. Numeric attachment ingestion remains a parser task rather than being backfilled from secondary media.

## Highest-priority gaps

1. **Memory / semiconductor equipment observed state** — add official/industry actual shipments, inventory, fab utilization or order data rather than more outlook reports.
2. **Grid observed state** — add interconnection completions/queue withdrawals/transmission additions and equipment deliveries; NERC/IEA/DOE forecasts/structure are not enough.
3. **REIT / commercial real estate** — implement REB attachment/table parser, then connect vacancy/rent/return to Nareit FFO/NAV/funding mechanics.
4. **Mining / critical minerals** — pair USGS with independent country/company/project evidence and mine-level cost/capacity curves.
5. **Oil & gas E&P** — pair EIA reserve definitions/actuals with basin/company reserve replacement and finding/development cost.
6. **Software / cloud** — industry-wide public sources are inherently thin for NRR/RPO/usage; use company-primary KPI extraction plus Economic-Twin normalization instead of forcing a broad-industry average.
7. **Aerospace/defense, telecom, gaming/content** — verified primary parsers remain incomplete; keep routing fail-closed rather than filling gaps with weak secondary reports.

## Stop rule

Once a node has independent observed state, industry structure, freshness watch and a corroborated mechanism, stop collecting generic reports. Shift collection to the bottleneck variables that change valuation: lead time, slots, capacity qualification, pricing, funding terms, reserve replacement, regulation, churn/usage or contract coverage.
