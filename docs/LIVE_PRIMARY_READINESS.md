# PRISM LIVE_PRIMARY Readiness Map v1.3

Status: canonical maintenance record for distinguishing full PRIMARY_SHADOW integration from real-source LIVE_PRIMARY readiness.

## 1. Why this exists

A 32-stage Control Plane run can be fully integrated while some stages still depend on incomplete source coverage or narrow evaluator/calibration sets. `PRIMARY_SHADOW PASS` therefore must never be reported as `LIVE_PRIMARY complete`.

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

At the v1.3 registry snapshot:

- canonical stages: **32 / 32 mapped**;
- `LIVE_READY` or `RUNTIME_READY`: **26**;
- `PARTIAL_LIVE`: **5**;
- explicit live gaps (`ADAPTER_REQUIRED`, `SHADOW_ONLY`, `CONDITIONAL_NOT_IMPLEMENTED`): **1**.

These counts are not a percentage-complete score. A single unresolved stage can still block a company if its Industry DNA makes that capability material.

The remaining highest-value live gaps are:

1. Warranted PER stage adapter;
2. broader exact-evaluator coverage across the 19 Economic Archetypes;
3. probability calibration datasets beyond the gating contract;
4. broader company-specific KPI/IR/primary-regulatory source adapters;
5. reusable jurisdiction/source providers for live peer returns, market risk inputs and company credit observations.

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

OpenDART `corpCode.xml` is the first official resolver implementation. The archive is parsed as ZIP/XML, and Korean entities may be resolved by exact stock code, exact DART corp code, or normalized exact legal name. Other jurisdictions should add resolvers behind the same `CompanyResolver` contract.

### Industry Knowledge snapshot

`IndustryKnowledgeSnapshot` binds an as-of date, source IDs, document IDs, Evidence IDs and content hashes into one deterministic `snapshot_hash`. A supplied hash that does not reproduce from its components is rejected.

### Source freshness

`LiveFreshnessAssessment` carries Source Watch findings and the source-snapshot hash. Source failure, definition/schema revision, unreviewed material update, new release requiring revalidation, or another revalidation-required state blocks downstream valuation until incorporated/reviewed. A missed expected release may remain a warning when the source is otherwise healthy.

### Segment decomposition

Every `SegmentDescriptor` must explicitly state revenue recognition, price formation, asset ownership, capital intensity, regulation, customer structure, reinvestment model, cash-flow duration and Evidence IDs. A segment without evidence cannot be routed.

### Industry DNA routing

The live router must cover every decomposed segment exactly once with an `IndustryDNAProfile`. Every `evidence_key` in the route must already exist in either the loaded Industry Knowledge snapshot or that run's segment evidence. Invented/unresolved Evidence IDs fail closed.

## 4. Live Rocket Insight scanner dispatch

`src/valuation_engine/scanner_runtime.py` executes the mandatory/adaptive scanner loadout through typed `ScannerRunner` contracts.

Rules:

- every mandatory scanner needs a registered runner or the stage is `NOT_IMPLEMENTED` and blocking;
- scanner Evidence IDs must already exist in the pre-freeze `EvidenceLedger`;
- target-market Evidence is forbidden;
- an active scanner must connect to a hypothesis candidate, verification request, economic path, final-output reference, or explicitly declare `context_only`;
- `context_only` is recorded as a research-only path so repeated cost/low impact can be down-ranked;
- each live finding records `ResearchEffort` and a `ModuleImpactTrace`-compatible path;
- typed scanner findings are included in `LLMStaffContext`.

The dispatcher does not commit assumptions. It produces structured research input for LLM Staff and later Decision Impact / ablation.

## 5. Live Upstream Funding scan

`src/valuation_engine/funding_adapter.py` executes route-required funding analysis through a typed `FundingScanner` contract.

The result must include a validated contiguous `FundingLadder`, Evidence IDs, funded-demand state and any financing constraints/verification requests. A credit-improvement candidate must be backed by confirmed or first-order funding evidence.

Crucially:

- customer advances/funding evidence may support FCFF, funded-demand or credit-mechanism reasoning;
- the funding stage does **not** directly lower WACC;
- only Evidence IDs are exposed as a credit-improvement candidate to the independent WACC stage;
- target-equity market Evidence is forbidden pre-freeze.

Funding results are also exposed to `LLMStaffContext` so the Researcher/Bridge stages can reason from the verified financing chain without bypassing the Assumption Compiler.

## 6. Live Hierarchical Beta and WACC

`src/valuation_engine/risk_adapters.py` is the typed live adapter layer for `HIERARCHICAL_BETA_ESTIMATION` and `WACC_VALIDATION`.

### Hierarchical Beta

The live universe must be exactly:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`.

Each level requires an explicit selection rationale and active Evidence IDs. Peer observations carry Beta, unlevering capital structure, tax rate, benchmark, frequency, estimation window, as-of date, source and optional standard error. One run must use a normalized benchmark/frequency/window convention; a peer cannot be counted in multiple levels.

The existing deterministic partial-pooling engine fixes asset Beta first, then the adapter relevers once using a typed target capital structure. Target current market capitalization is not a permitted pre-freeze source for choosing that structure.

### WACC

WACC consumes the `LiveBetaStageResult`; its loader cannot override Beta. It loads typed currency-consistent risk-free, ERP, country-risk, marginal debt cost and target-capital-structure observations.

The same equity/debt weights, tax rate and target-structure method must be used for Beta relevering and WACC weighting. A mismatch fails closed.

Positive additional risk premia require an explicit evidenced basis and active Evidence IDs. Generic small-cap plugs are not accepted.

Customer-advance/funding Evidence may be recorded as a credit-improvement candidate, but does not mechanically lower WACC. Actual WACC changes must appear through separately observed/rebuilt Beta, marginal Cost of Debt, target structure or another evidenced input.

Both stages reject target-company current price, target market capitalization, target price, consensus targets/multiples and target-company Street references before Intrinsic Freeze.

The stages are `PARTIAL_LIVE`: deterministic contracts and fail-closed calculation are complete, while universal jurisdiction/source providers for live peer-return, sovereign-curve, ERP and company-credit data remain outside the repository. See `docs/LIVE_BETA_WACC_ADAPTERS.md`.

## 7. OpenDART company-fact vertical

`src/valuation_engine/dart_facts.py` is the first reusable LIVE_PRIMARY company-fact adapter.

The transport contract follows the official OpenDART **single-company full financial statements** API:

`GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json`

with `crtfc_key`, `corp_code`, `bsns_year`, `reprt_code`, and `fs_div`.

Design rules:

- default to consolidated `CFS`; `OFS` requires an explicit request;
- standard core facts use exact XBRL account IDs only;
- account-name fuzzy matching is forbidden;
- company-specific facts such as contract liabilities/customer advances need an explicit `DartFactMetricSpec`;
- interim income-statement facts use cumulative `thstrm_add_amount` when available; `thstrm_amount` is not silently treated as YTD;
- balance-sheet facts use point-in-time `thstrm_amount`;
- response rows may omit `fs_div` because the official endpoint receives it at request level;
- currency mismatch fails closed;
- large KRW amounts preserve exact integer precision rather than passing through float;
- multiple different values matching one metric fail closed rather than being averaged or summed;
- DART facts enter only as `REALIZED_OR_FILING` Evidence. They do not become assumptions without the ordinary LLM Bridge → deterministic Assumption Compiler path.

The live collector uses an injected `fetch_text` transport. Network/credential handling therefore stays outside deterministic valuation code, while fixtures and historical replays use the same fact parser.

## 8. What LIVE_READY does not mean

A `LIVE_READY` stage means its typed source/loader/runner contract, traceability and fail-closed behavior are complete. It does **not** claim every jurisdiction, every source family, every scanner implementation, every risk-data provider or every company-specific KPI is built in.

OpenDART standard financial facts do not provide every Industry DNA requirement. Backlog quality, effective capacity, customer advances, qualification, project COD, clinical evidence, customer concentration and many segment KPIs still need company-specific filing-note, IR, primary-regulatory or calibrated alternative-data adapters.

Absence of a standard XBRL account ID is not permission to guess from a similar account name. The Control Plane should record the missing metric and enter Recovery/Capability handling.

## 9. Promotion rule

A stage moves toward `LIVE_READY` only when:

1. the source/loader/runner contract is explicit;
2. freshness/revision behavior is auditable where relevant;
3. output is typed and traceable;
4. failure is fail-closed;
5. current-price/Street isolation remains intact;
6. fixtures and regression tests exist;
7. the Unit Contract or its canonical maintenance references identify downstream consumers and effects.

Changing a readiness label does not alter valuation formulas or bypass ordinary module-promotion governance.
