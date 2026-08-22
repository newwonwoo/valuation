# Industry DNA Router v1.0

Status: v0.5 candidate contract. The router is segment-first and multi-label. Sector names are adapters, not valuation models.

## 1. Composition model

```text
Common Valuation Core
  → Segment Decomposition
  → Economic Archetype(s)
  → Sector Adapter
  → Company Overlay (private/live state)
  → Allowed Evaluator Set
  → Segment Valuation
  → SOTP / Aggregation
```

The Common Core remains mandatory: evidence/provenance, accounting normalization, Beta/WACC/PER validation when applicable, funding scan, scenario/Monte Carlo discipline, double-count audit, blind intrinsic-value freeze and post-freeze Street/market comparison.

## 2. Why archetypes precede sectors

A sector label does not determine cash-flow mechanics. A regulated utility, transformer OEM and project developer can all be called “power” while relying respectively on rate base/allowed return, backlog/capacity/pricing, and project finance/COD/utilization. The router therefore identifies economic DNA first.

The registry currently defines 19 archetypes, including contracted backlog, capacity manufacturing, recurring subscription, metered usage, marketplace, commodity price taker, process spread, regulated rate base, asset-yield/NAV, financial balance sheet, probabilistic pipeline, reserve depletion, consumer unit economics, project finance, IP royalty, hit-driven content, advertising attention, design-led product and AUM-fee economics.

## 3. Sector adapters

`config/sector_adapter_registry.yaml` contains 35 initial adapters. Adapter archetypes are hypotheses, not facts. They must be verified against segment evidence such as revenue recognition, contract structure, asset ownership, price formation, capital intensity, customer concentration, regulation, reinvestment and cash-flow duration.

No generic DCF fallback is allowed when routing confidence is insufficient. The run should request evidence or block the segment valuation.

## 4. Module contract

Each archetype declares:
- required evidence;
- accounting normalization;
- Beta Economic-Twin features;
- PER Economic-Twin features;
- scenario variables/correlations;
- funding scan trigger;
- terminal-value policy;
- forbidden methods;
- double-count traps.

Sector adapters add domain-specific evidence, definitions, sources, leading indicators, valuation links and kill conditions. Company overlays contain only company-specific facts and belong in private state rather than the reusable public module.

## 5. Routing evidence and confidence

Routing should be supported by explicit Evidence IDs. Keyword matches may propose a route but cannot finalize it. A route should record at least:
- revenue-recognition model;
- primary unit of economic output;
- pricing mechanism;
- contract/recurrence structure;
- asset/capital intensity;
- funding dependence;
- regulation;
- reinvestment/depletion mechanics.

Mixed businesses are decomposed into segments and valued with different evaluator contracts before aggregation.
