# Industry Knowledge Ingestion v1.0

Status: v0.5 candidate implementation. Metadata-first parsers/indexers and bounded transport are implemented; production public-network transport has not been validated in the current container.

## Objective

Build an evidence-backed industry knowledge layer that sits **before** Industry DNA routing and company valuation.

`Source Registry → Document Registry → Structured Claims → Definition/Conflict Normalization → Mechanism Graph → Module Rule Proposal → Industry DNA Router → Company Overlay → Valuation`

The goal is not to archive every report. The goal is to maintain reusable, citable industry definitions, observed states, mechanisms, leading indicators, valuation links and kill conditions.

## 1. Collection posture

### Index-first, content-second

For each source, collect metadata first:
- source family / authority
- title / publication date / period covered
- document class
- industry tags / geography
- file URL / content hash
- license/publication status
- update cadence

Fetch or parse full content only when the document is new, changed, or fills a coverage gap.

### Source-role separation

- `OBSERVED_STATE`: official statistics, realized industry state.
- `INDUSTRY_STRUCTURE`: definitions, economics, supply chain, accounting/operating mechanics.
- `FORWARD_HYPOTHESIS`: forecasts, surveys, outlooks. Never realized fact.
- `REGULATION_POLICY`: rule or policy intent. Transmission must be separately evidenced.
- `MARKET_REFERENCE`: indices, market valuation benchmarks, prices.
- `DEFINITION_STANDARD`: metric/classification definitions.

## 2. Structured output

Every extracted item becomes one of:

`FACT | DEFINITION | FORECAST | MECHANISM | BENCHMARK | LEADING_INDICATOR | VALUATION_LINK | KILL_CONDITION | POLICY_INTENT | TRANSMISSION_EFFECT`

Minimum fields:
- claim_id
- source_id / source_family
- industry_node / geography
- publication date / covered period
- claim kind
- metric / value / unit when numeric
- definition_id
- statement text (short paraphrase)
- economic_path_id
- lead-lag hypothesis when relevant
- confidence / contradiction flags
- source locator

Do not store long copyrighted passages. Store short paraphrases, metadata and traceable locators.

## 3. Definition normalization gate

A numeric series cannot enter an industry mechanism until definitions align.

Examples:
- nameplate vs qualified/effective capacity
- orders vs backlog vs bookings
- production vs shipment vs sell-through
- new-order ASP vs recognized-revenue ASP
- installed GW vs generation TWh
- ARR vs revenue vs remaining performance obligations
- gross merchandise value vs revenue
- reserves vs resources
- FFO vs AFFO vs net income

Conflicting definitions are `scoped_split` or blocking conflicts; never average them.

## 4. Mechanism promotion

A single report can create only a `SINGLE_SOURCE_CANDIDATE`.

A mechanism becomes `CORROBORATED` only with independent source families and both observed-state and industry-structure evidence.

A `MODULE_RULE_CANDIDATE` additionally needs:
- repeated evidence across at least two periods or cycles;
- a measurable leading indicator;
- a valuation/economic variable link;
- a falsifiable kill condition;
- no unresolved critical definition conflict.

Ingestion never auto-approves a canonical module rule. Final promotion requires explicit approval plus regression cases and Red Team review.

## 5. Industry DNA interface

Industry routing is segment-first and multi-label.

A segment is described by:
- revenue recognition
- price formation
- asset ownership
- capital intensity
- regulation intensity
- customer structure
- reinvestment model
- cash-flow duration
- one or more Economic Archetypes

Economic Archetypes in v1 (19):
`CONTRACTED_BACKLOG, CAPACITY_MANUFACTURING, RECURRING_SUBSCRIPTION, METERED_USAGE_NETWORK, TRANSACTION_MARKETPLACE, COMMODITY_PRICE_TAKER, PROCESS_SPREAD, REGULATED_RATE_BASE, ASSET_YIELD_NAV, FINANCIAL_BALANCE_SHEET, PROBABILISTIC_PIPELINE, RESERVE_DEPLETION, CONSUMER_UNIT_ECONOMICS, PROJECT_FINANCE, IP_ROYALTY_LICENSING, HIT_DRIVEN_CONTENT, ADVERTISING_ATTENTION, DESIGN_LED_PRODUCT, AUM_FEE_ECONOMICS`.

The sector label becomes an adapter, not the primary valuation model.

Example:

`transformer segment = CONTRACTED_BACKLOG + CAPACITY_MANUFACTURING + power.transformer_switchgear + customer_advance_financing overlay`

## 6. Update policy

- APIs / official monthly series: incremental fetch by period.
- HTML/report indexes: metadata poll; fetch only new/changed content hashes.
- Quarterly/annual outlooks: retain vintages; never overwrite earlier forecasts.
- Corrections: preserve revision chain.
- Licensed research: store metadata + derived claims in private state only.

## 7. Data-quality and anti-overfitting rules

- Same family repeated 20 times is still one source family for corroboration.
- Forecast consensus does not turn a forecast into fact.
- Company commentary cannot establish an industry-wide mechanism alone.
- Policy announcement cannot establish transmission without observed mechanical effects.
- A high-correlation historical relationship is not a causal rule without mechanism evidence.
- Module rules should expose `works_when`, `fails_when`, lead/lag and industry scope.

## 8. Recommended implementation phases

### Phase A — Registry + indexers
The candidate registry currently contains 30 verified source specifications plus 6 candidates to verify. Deterministic/transport-separated indexer support is implemented for KIET PSI, KISDI ICT reports, IEA Monthly Electricity (multi-endpoint), OpenDART filing metadata and caller-configured KOSIS JSON snapshots. Other sources remain registry/watch/seed integrations until their parsers are implemented. Credentials are runtime-only.

### Phase B — Gap sources
Verify and add Korea real estate, telecom, gaming/content, mining, oil/refining and defense procurement sources.

### Phase C — Extractors
Build deterministic table extraction first; LLM summarization only after table/metadata extraction. PDF/HWP parsers should retain page/table locators and hashes.

### Phase D — Mechanism graph
Generate proposed edges such as:
`inventory ↓ + shipment ↑ → supply tightness ↑ → ASP pressure ↑ → utilization/margin ↑`
with independent evidence IDs and kill conditions.

### Phase E — Industry module compiler
Compile only approved mechanisms into versioned sector/archetype modules. Preserve provenance and regression tests.

## 9. Source Freshness & Revision Watcher

Every recurring source series carries an expected cadence/release window and four independent fingerprints:

`document_hash | fact_hash | definition_hash | schema_hash`

Classify changes as:
- `NEW_RELEASE`: new period/vintage.
- `REVISION`: same-period facts changed.
- `DEFINITION_CHANGE`: metric meaning or classification changed; automatic promotion is blocked.
- `SCHEMA_CHANGE`: API/table layout changed; parser output is not trusted until revalidated.
- `EXPECTED_RELEASE_MISSED`: expected date + grace passed without a new vintage. This is an operational signal, not automatically a negative industry signal.
- `SOURCE_FAILURE`: fetch/parsing failed.

Impact propagation is source-series → industry node → mechanism → company assumption. Only affected nodes become dirty. A new report does **not** automatically mutate a frozen intrinsic-value run; it creates a revalidation request and, if material, a new valuation run.

The initial watch contract lives in `config/source_watch_registry.yaml`; deterministic status logic lives in `src/valuation_engine/source_watch.py`.

## 10. 2026-08-21 actual-content seed

Current candidate seed:
- 30 verified source specs (+6 unverified candidates kept separate);
- 25 actual public document records;
- 73 structured claims;
- 7 mechanism candidates;
- 21 watched source series;
- 19 economic archetypes;
- 43 sector adapters;
- 39 generic impact-graph edges.

The seed covers semiconductor/memory/equipment, electricity/grid/transformers, automotive, shipping, REITs, ICT/software, pharma monitoring and oil/gas reserve economics. These are short paraphrased claims plus document provenance, never a raw-report archive.

Coverage scoring is fail-closed: independent source families, observed state, structure/mechanism evidence, freshness watch and mechanism coverage are separate dimensions. A high raw score is capped when a critical evidence dimension is missing. The current seed has one A- node (`power.transformers`) after cross-source mechanism corroboration; most nodes remain B/C because missing evidence dimensions cap the grade. This is a gap map, not a promotional scorecard.

Run:

```bash
PYTHONPATH=src python scripts/validate_industry_seed.py
PYTHONPATH=src python scripts/validate_module_registries.py
PYTHONPATH=src python scripts/validate_probe_fixtures.py
PYTHONPATH=src python scripts/build_watch_report.py
PYTHONPATH=src python scripts/build_coverage_snapshot.py
PYTHONPATH=src pytest -q
```

before accepting seed changes.

## 11. Production-readiness boundary

The code includes a bounded HTTP transport (timeout, byte limit, bounded retry) and live-indexer entry points. The current execution environment has not validated public-network HTTP transport, so fixture regression proves parser/reconciliation behavior only. Production activation requires a network-enabled run, source-specific rate/terms checks, credential setup for OpenDART/KOSIS where applicable, and at least one successful incremental update cycle before any freshness finding may block valuation.

See `docs/INDUSTRY_DNA_ROUTER_V1.md` and `docs/SOURCE_FRESHNESS_OPERATION.md` for routing and watcher contracts.
