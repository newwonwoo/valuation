# RocketSLA v0.5 workflow contract — Industry Knowledge before valuation

Status: candidate integration contract; not yet merged into the repository runtime `SKILL.md`.

## Required order

```text
1  COMPANY_RESOLUTION
2  LOAD_COMPANY_STATE
3  LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT
4  SOURCE_FRESHNESS_PRECHECK
5  SEGMENT_DECOMPOSITION
6  INDUSTRY_DNA_ROUTE
7  MODULE_REQUIREMENT_PLAN
8  PRIMARY_EVIDENCE_COLLECTION
9  EVIDENCE_LEDGER
10 ROCKET_INSIGHT_SCAN
11 UPSTREAM_FUNDING_SCAN when material
12 RESEARCHER_A
13 BLIND_RED_TEAM_B
14 targeted RESEARCH_LOOP
15 EVIDENCE_TO_ASSUMPTION_BRIDGE
16 SCENARIO_BUILD
17 HIERARCHICAL_BETA_ESTIMATION when applicable
18 WACC_VALIDATION
19 DETERMINISTIC_VALUATION
20 HIERARCHICAL_WARRANTED_PER when allowed
21 DCF_PER_ASSUMPTION_CONSISTENCY_GATE
22 CROSS_METHOD_DOUBLE_COUNT_AUDIT
23 PROBABILITY_DISTRIBUTION_ANALYSIS when calibrated
24 AUDIT_GATE
25 INTRINSIC_VALUE_FREEZE
26 STREET_REFERENCE_LOAD
27 STREET_GAP_ANALYZER
28 MARKET_PRICE_LOAD
29 MARKET_COMPARE
30 THESIS_DELTA / SAVE_STATE / FINAL_REPORT
```

## New v0.5 gates

### LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT
Freeze the industry knowledge version/hash used by the run. A later report cannot silently mutate an in-progress valuation.

### SOURCE_FRESHNESS_PRECHECK
Check only source series that are material to the selected sector/likely archetypes. `SOURCE_FAILURE` is operational, not negative industry evidence. `DEFINITION_CHANGE` or `SCHEMA_CHANGE` blocks automatic mechanism/module promotion and may block valuation when a required critical input cannot be trusted.

### SEGMENT_DECOMPOSITION
Route each economically distinct business separately. Holding companies and mixed businesses cannot receive one company-wide sector label when segments have different cash-flow mechanics.

### INDUSTRY_DNA_ROUTE
Assign one or more Economic Archetypes using explicit routing evidence. Sector adapters propose defaults only. Keyword matching cannot finalize a route.

### MODULE_REQUIREMENT_PLAN
Compile required evidence, normalization, Beta/PER Economic-Twin features, scenario variables, funding checks, forbidden methods, terminal policy, double-count traps and kill conditions from the selected archetype/sector contracts before data collection. This prevents the analyst from collecting only evidence that supports a preferred model.

## Fail-closed behavior

Valuation is blocked rather than falling back to generic DCF when:
- no supported Economic Archetype can be established;
- a required module input is unavailable or definition-conflicted;
- a method is forbidden by any material archetype without an explicit segment split;
- a critical industry source has an unresolved definition/schema break and no substitute evidence;
- a company overlay attempts to override a reusable module rule without evidence.

## Versioning

A valuation run should retain:
- industry knowledge snapshot hash;
- source-watch snapshot hash;
- taxonomy/module registry version;
- sector adapter version;
- routing evidence IDs;
- selected archetypes;
- module requirement-plan hash.

This makes later thesis changes attributable to either new company evidence, new industry evidence, a module-version change or a market-only change.

## Knowledge placement contract

Every source exposed to the workflow must be assigned to a `KnowledgeLayer` and pass the fail-closed placement rules in `src/valuation_engine/knowledge_placement.py`.

Use `config/foundation_source_registry.yaml`, `config/knowledge_placement_policy.yaml`, and `config/workflow_source_injection_map.yaml` as the cross-industry source-placement contracts.

The key separation is:

`classification/metric/provenance standards → definitions and requirements`

`input-output/topology → structural prior`

`official/company primary → realized evidence → Bridge → assumption`

`public research/broker/alternative data → discovery/corroboration/verification request`

`calibration references → Beta/WACC/PER sanity only`

`target Street/market → post-freeze comparison only`
