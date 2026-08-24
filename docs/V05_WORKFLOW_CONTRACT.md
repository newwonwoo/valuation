# RocketSLA v0.5 workflow contract — Industry Knowledge before valuation

Status: canonical runtime contract, merged into repository `SKILL.md` as v0.5.2. Control-plane authority and recovery semantics are defined in `docs/CONTROL_PLANE_ARCHITECTURE.md`.

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
17 VALUATION_METHOD_INTENT
18 HIERARCHICAL_BETA_ESTIMATION when applicable
19 WACC_VALIDATION
20 DETERMINISTIC_VALUATION
21 HIERARCHICAL_WARRANTED_PER when allowed
22 DCF_PER_ASSUMPTION_CONSISTENCY_GATE
23 CROSS_METHOD_DOUBLE_COUNT_AUDIT
24 PROBABILITY_DISTRIBUTION_ANALYSIS when calibrated
25 AUDIT_GATE
26 INTRINSIC_VALUE_FREEZE
27 STREET_REFERENCE_LOAD
28 STREET_GAP_ANALYZER
29 MARKET_PRICE_LOAD
30 MARKET_COMPARE
31 THESIS_DELTA / SAVE_STATE / FINAL_REPORT
```

The Control Plane groups these stages into operational phases but may not reorder or bypass them. `None`, a failed method, or missing data enters the canonical recovery ladder before `VALUATION BLOCKED`, unless a non-recoverable audit/safety invariant is violated.

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

### VALUATION_METHOD_INTENT
Resolve and hash the exact segment method/version choices before Beta/WACC. Risk-stage applicability and the stage-20 valuation compilation must consume that same intent; a stale module, capability, evaluator, or method-choice identity blocks execution.

## Fail-closed behavior

`VALUATION BLOCKED` is the terminal outcome when a blocking issue remains after the Control Plane's recovery ladder or when a non-recoverable invariant fails.

Examples:
- no supported Economic Archetype can be established after route recovery;
- a required module input remains unavailable or definition-conflicted after research/reconcile/derive/proxy/alternate-model recovery;
- a method is forbidden by any material archetype without an explicit segment split;
- a critical industry source has an unresolved definition/schema break and no substitute evidence;
- a company overlay attempts to override a reusable module rule without evidence;
- Audit detects a blocking invariant such as market-price leakage or duplicate economic paths.

When only some independent segments are unsupported, the Control Plane may emit `PARTIAL_INTRINSIC` and mark unsupported value `UNVALUED_NOT_ZERO`; it must not label the subtotal as full fair value.

## Versioning

A valuation run should retain:
- industry knowledge snapshot hash;
- source-watch snapshot hash;
- taxonomy/module registry version;
- sector adapter version;
- routing evidence IDs;
- selected archetypes;
- module requirement-plan hash;
- doctrine coverage status for every applicable module/scanner/gate;
- Control Plane mission/execution mode;
- intrinsic-freeze token hash when issued.

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
