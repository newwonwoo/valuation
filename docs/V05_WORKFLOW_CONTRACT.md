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

## Major-gate reporting contract

The same 33 stages are partitioned once, by `config/control_plane_stage_registry.yaml`, into five contiguous reporting gates:

1. `G1_EVIDENCE_ROUTING` — stages 1–9;
2. `G2_INSIGHT_CHALLENGE` — stages 10–14;
3. `G3_ASSUMPTIONS_METHOD_RISK` — stages 15–19;
4. `G4_VALUATION_AUDIT_FREEZE` — stages 20–26;
5. `G5_POST_FREEZE_PERSISTENCE` — stages 27–33.

The orchestrator emits a four-field summary when a gate reaches its terminal stage or terminates blocked: status, decisive result, residual risk and next action. A blocked gate never causes later-gate summaries to be fabricated. Routine output uses these five summaries; the compact verified appendix retains every stage identity/status and the immutable trace artifact retains exact rationale/output-key detail.

The final result report has an editorial target of 3–4 pages for the decision-facing body and 1–2 pages for the compact audit appendix, capped at 6 pages combined. Body text is at least 13pt, primary headings at least 22pt and section headings at least 18pt; dense wide tables are forbidden. The appendix preserves all 33 stage identities/statuses, while exact rationales/output keys remain in the immutable trace artifact. Page limits do not authorize omission of material blockers, uncertainty labels, source lineage, frozen identities or audit evidence.

The reader-facing order is Korean brokerage-research style: 투자 요약 → 가치평가 → 핵심 가정과 위험 → 증권사·시장 비교 → 원문 출처, followed by the audit appendix. `투자 요약` is the primary investment report rather than a preface and must independently expose the decision, current price, reference intrinsic value, valuation range, one-sentence conclusion, investment points and decision-change conditions. Stage names and statuses are Korean in the visible appendix. Raw technical IDs, enums and hashes are collapsed or retained in immutable machine artifacts so the user sees the investment case before execution diagnostics.

The user-facing report and all five major-gate summaries are Korean by default. LLM-authored environment-change/company-strength reasoning is isolated in one `인공지능 인사이트` section capped at 1,000 characters; it must not be presented as a deterministic assumption, calculation, Audit finding or Freeze authorization. The full typed insight remains in the immutable `context_strength_linkages.json` artifact.

Each successful report also persists two deterministic Korean SVG cards from the same frozen run payload: one for company strengths, investment conclusion and valuation; one for valuation assumptions, risks and source access. These two visual pages are included inside the 3–4 page main-body target rather than added on top of the six-page cap. The visual layer may not invent an entry price. Without authorized calibration and an explicit entry rule it displays scenario values/current price and marks the specific buy price as withheld.

Source links are a hard exception to omission. Every active Evidence record and each reported identity/Beta/WACC/PER/Street/market reference must resolve to a credential-free HTTP(S) original-source link. Repeated claims from one document are grouped under that link with covered metrics/effective dates; high-volume groups show counts and representative metrics while the immutable Evidence Ledger retains every exact ID/metric/date mapping. A `LIVE_PRIMARY` run with a missing, non-HTTP or credential-bearing reference is blocked during final-report persistence; a verified wrapper also checks that every accepted link is actually embedded in the persisted report.

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
