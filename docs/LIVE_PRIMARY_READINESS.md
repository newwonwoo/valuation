# PRISM LIVE_PRIMARY Readiness Map v1.5

Status: canonical maintenance record for distinguishing typed runtime integration from real-source LIVE_PRIMARY readiness.

## 1. Why this exists

A 33-stage Control Plane run can be fully integrated while some stages still depend on incomplete source breadth, historical datasets or evaluator families. Runtime completion therefore must never be reported as universal live-source/method completion.

`config/live_primary_readiness.yaml` is the machine-readable readiness source. It must contain exactly one row for every stage in `config/control_plane_stage_registry.yaml`.

Readiness states:

- `LIVE_READY`: can execute with current/live inputs under a declared source/loader contract.
- `PARTIAL_LIVE`: at least one reusable live path exists, but material source/method coverage remains incomplete.
- `RUNTIME_READY`: deterministic/runtime stage is complete once typed upstream inputs are supplied.
- `ADAPTER_REQUIRED`: reusable components exist, but a canonical live stage adapter is still missing.
- `SHADOW_ONLY`: current stage behavior proves integration only.
- `CONDITIONAL_NOT_IMPLEMENTED`: stage is route-dependent and intentionally fails closed when required without an implementation.

A new workflow stage without a readiness row is a maintenance error.

## 2. Current snapshot

At the v1.5 registry snapshot:

- canonical stages: **33 / 33 mapped**;
- `LIVE_READY` or `RUNTIME_READY`: **29**;
- `PARTIAL_LIVE`: **4**;
- explicit live gaps (`ADAPTER_REQUIRED`, `SHADOW_ONLY`, `CONDITIONAL_NOT_IMPLEMENTED`): **0**.

The four remaining `PARTIAL_LIVE` stages are:

1. `PRIMARY_EVIDENCE_COLLECTION` — company-specific filing-note, IR, regulatory and non-standard KPI source breadth remains incomplete;
2. `DETERMINISTIC_VALUATION` — several exact archetype/method bindings remain `NOT_IMPLEMENTED`, including NAV, residual-income/DDM/PB-ROE and pipeline-option families;
3. `PROBABILITY_DISTRIBUTION_ANALYSIS` — production historical cohorts, predeclared base rates and validated forecast-class/horizon mapping datasets remain incomplete;
4. `STREET_REFERENCE_LOAD` — lawful authorized export loading is implemented, while universal automatic retrieval remains entitlement/vendor specific.

`LIVE_READY` is contract-scoped, not a claim that every jurisdiction or vendor is bundled into the repository.

## 3. LIVE_PRIMARY front-half contract

`src/valuation_engine/live_primary_adapters.py` is the canonical front-half adapter layer for:

- `COMPANY_RESOLUTION`;
- `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT`;
- `SOURCE_FRESHNESS_PRECHECK`;
- `SEGMENT_DECOMPOSITION`;
- `INDUSTRY_DNA_ROUTE`.

The architecture uses typed loader/resolver contracts instead of embedding one country's source logic into the Control Plane. A jurisdiction-specific implementation plugs into the same stage contract.

### Company resolution

`ResolvedCompanyIdentity` must carry legal name, ticker when applicable, jurisdiction, stable target ID, external IDs and source references. Resolution failure or ambiguity enters Recovery; the runtime does not guess among candidates.

OpenDART `corpCode.xml` is the first official resolver implementation. Korean entities may be resolved by exact stock code, exact DART corp code, or normalized exact legal name. Other jurisdictions use the same `CompanyResolver` contract.

### Industry Knowledge snapshot and segment lineage

`IndustryKnowledgeSnapshot` binds an as-of date, source IDs, document IDs, Evidence lineage and content hashes into one deterministic `snapshot_hash`. Segment Evidence must be authoritative for the resolved target and preserve effective/event, published, first-seen and revision chronology. Evidence that was not knowable by the snapshot cutoff is rejected rather than backfilled into historical routing.

### Source freshness

`LiveFreshnessAssessment` carries Source Watch findings and the source-snapshot hash. Source failure, definition/schema revision, unreviewed material update, new release requiring revalidation, or another revalidation-required state blocks downstream valuation until incorporated/reviewed.

### Segment decomposition and Industry DNA routing

Every `SegmentDescriptor` must explicitly state revenue recognition, price formation, asset ownership, capital intensity, regulation, customer structure, reinvestment model, cash-flow duration and Evidence IDs. The live router must cover every decomposed segment exactly once, and every routing Evidence ID must already belong to the authoritative snapshot/segment lineage.

## 4. Live Rocket Insight scanner dispatch

`src/valuation_engine/scanner_runtime.py` executes the mandatory and explicitly activated optional scanner loadout through typed `ScannerRunner` contracts.

Rules:

- every mandatory scanner needs a registered runner or the stage is `NOT_IMPLEMENTED` and blocking;
- optional scanners are declared separately in the Module Requirement Plan and are never inferred from generic active research-unit IDs;
- scanner Evidence IDs must already exist in the pre-freeze `EvidenceLedger`;
- target-market Evidence is forbidden;
- an active scanner must connect to a hypothesis candidate, verification request, economic path, final-output reference, or explicitly declare `context_only`;
- each live finding records `ResearchEffort` and a `ModuleImpactTrace`-compatible path.

The dispatcher produces structured research input and never commits assumptions.

## 5. Live Upstream Funding scan

`src/valuation_engine/funding_adapter.py` executes route-required funding analysis through a typed `FundingScanner` contract.

A credit-improvement candidate must be backed by confirmed or first-order funding Evidence. The funding stage does not directly lower WACC; it exposes Evidence-backed credit candidates to the independent risk/assumption path. Target-equity market Evidence is forbidden pre-freeze.

## 6. Live Hierarchical Beta and WACC

`src/valuation_engine/risk_adapters.py` owns the strict typed live contracts. `src/valuation_engine/authorized_risk_providers.py` supplies the repository's authorized Korean provider pack.

### Hierarchical Beta — `LIVE_READY`

The live universe is exactly:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`.

`AuthorizedKRRiskProviderPack` composes authorized KRX regression Beta observations with Evidence-backed peer capital observations, validates normalized benchmark/frequency/window conventions, and builds the canonical `LiveBetaUniverse`. The deterministic partial-pooling engine fixes asset Beta first and relevers once using the typed target capital structure.

### WACC — `LIVE_READY`

The authorized Korean pack composes:

- Bank of Korea ECOS risk-free/borrowing series;
- Damodaran mature-market ERP separated from country-risk premium;
- an explicitly rating/maturity-matched marginal debt benchmark;
- peer-normalized target capital structure that does not use target current market capitalization.

The same target structure must be used for Beta relevering and WACC weighting. Target current price, target market capitalization, target price and target-company Street references remain forbidden pre-freeze. Other currencies/jurisdictions remain injectable behind the same contract; that breadth is not required for the KR provider path to be `LIVE_READY`.

## 7. Live Hierarchical Warranted PER — `LIVE_READY`

`src/valuation_engine/per_adapters.py` is the canonical stage adapter and `src/valuation_engine/authorized_per_providers.py` supplies the authorized provider pack.

The provider pack:

- builds normalized forward EPS from annual OpenDART filing EPS, explicit Evidence-backed normalization adjustments and an explicit non-Street forward-growth input;
- rejects interim filing EPS as a normalized annual base;
- verifies the compiled normalized EPS assumption and Evidence IDs before Warranted PER consumes it;
- supplies exact L1→L4 peer residual observations using peer-only market references;
- forbids target-company self-inclusion, duplicate peers and mixed residual as-of dates.

Core Fundamental PER remains tied to the compiled assumptions and `LiveWACCStageResult`; DCF consistency checks, expansion-adjusted PER and peer residual hierarchy retain their existing fail-closed contracts.

## 8. OpenDART company-fact vertical — `PARTIAL_LIVE` evidence breadth

`src/valuation_engine/dart_facts.py` and the OpenDART provider bundle support official company resolution, standard financial facts, original filing documents and deterministic KPI extraction.

Design rules include exact XBRL account IDs for standard facts, correct Q2/Q3 cumulative semantics, explicit fiscal-period identity, point-in-time balance-sheet semantics, exact integer precision for large KRW values and fail-closed ambiguity/currency handling.

This does not cover every company-specific KPI. Backlog, effective capacity, customer advances, qualification, project COD, clinical evidence, customer concentration and many segment metrics may still require filing-note, IR, primary-regulatory or other authorized adapters.

## 9. Deterministic valuation — `PARTIAL_LIVE`

The exact registry/SOTP runtime currently supports:

- normalized multiples;
- explicit FCFF DCF families;
- finite-life `project_npv`, `reserve_npv` and `cohort_npv`;
- calibration-certified single-event rNPV;
- SOTP aggregation where registered;
- `PARTIAL_INTRINSIC` with explicit `UNVALUED_NOT_ZERO` preservation.

A partial subtotal is never presented as full-company fair value and whole-company Street/current-price gap comparison is withheld for partial runs.

`config/valuation_method_capability_registry.yaml` remains the machine-readable source for exact method gaps. Current `NOT_IMPLEMENTED` bindings include contracted-backlog normalized EBITDA, hit-driven pipeline option SOTP, regulated/asset-yield DDM-related methods, asset NAV/FFO multiple, financial PB-ROE/residual income, and reserve NAV.

## 10. Probability calibration — `PARTIAL_LIVE`

The probability layer has append-only forecast/outcome revision chains, `first_seen_at` knowledge-time boundaries, deterministic historical replay, Brier/Brier-Skill/log-loss/ECE metrics and hash-bound `CalibrationCertificate` gating. Declared binary events from an audit-passed `LIVE_PRIMARY` run now persist their raw pre-resolution probability and Evidence snapshot into production history. Outcome ingestion rejects non-primary or non-verifiable Evidence and preserves the direct source URL.

What remains is elapsed-time production evidence, not missing arithmetic or capture wiring: the declared events must resolve from real primary sources, and the required historical cohorts, predeclared base rates and validated mapping datasets by forecast class/horizon must be populated before broad numeric probability weighting can be promoted.

## 11. Audit, Freeze and post-freeze references

Audit replays the frozen Evidence Ledger hash, compiled Evidence-input hashes, Compiled Assumption Set hash, Bound Scenario Set hash and valuation hash. The Intrinsic Freeze Token binds the same run to the frozen Ledger, assumptions, valuation, Audit, Industry Knowledge and source snapshot identities.

Post-freeze Street loading accepts only caller-authorized `licensed_export` or `explicit_permission` inputs in the repository-provided loader. Universal automatic retrieval remains entitlement-specific. Market/Street comparisons cannot mutate the frozen intrinsic run.

## 12. What LIVE_READY does not mean

A `LIVE_READY` stage means its typed source/loader/runner contract, traceability, current implementation path and fail-closed behavior are complete for its declared scope. It does not claim every jurisdiction, every vendor, every scanner source or every company KPI is bundled.

Absence of a standard source field is not permission to guess. The Control Plane records the missing requirement and enters Recovery, partial valuation or capability handling according to doctrine.

## 13. Promotion rule

A stage moves toward `LIVE_READY` only when:

1. the source/loader/runner contract is explicit;
2. freshness/revision behavior is auditable where relevant;
3. output is typed and traceable;
4. failure is fail-closed;
5. current-price/Street isolation remains intact;
6. fixtures and regression tests exist;
7. the Unit Contract or its canonical maintenance references identify downstream consumers and effects.

Changing a readiness label does not alter valuation formulas or bypass ordinary module-promotion governance.
