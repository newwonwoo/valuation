# PRISM LIVE_PRIMARY Readiness Map v1.2

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

At the v1.2 registry snapshot:

- canonical stages: **32 / 32 mapped**;
- `LIVE_READY` or `RUNTIME_READY`: **24**;
- `PARTIAL_LIVE`: **4**;
- explicit live gaps (`ADAPTER_REQUIRED`, `SHADOW_ONLY`, `CONDITIONAL_NOT_IMPLEMENTED`): **4**.

These counts are not a percentage-complete score. A single unresolved stage can still block a company if its Industry DNA makes that capability material.

The remaining highest-value live gaps are:

1. route-specific Upstream Funding adapter;
2. live Economic-Twin/Beta and WACC input adapters;
3. Warranted PER stage adapter;
4. broader exact-evaluator coverage across the 19 Economic Archetypes;
5. probability calibration datasets beyond the gating contract;
6. source-aware handler coverage for every mandatory Rocket Insight scanner family.

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

## 4. Typed Rocket Insight scanner runtime

`src/valuation_engine/scanner_runtime.py` is the canonical generic dispatcher for `ROCKET_INSIGHT_SCAN`.

The Module Requirement Plan owns the exact mandatory scanner IDs. LLM Staff may add reinforcement scanners, but cannot remove or replace a mandatory scanner.

The dispatcher enforces:

- exact handler coverage for every mandatory scanner;
- one typed finding per executed scanner;
- active Evidence-ID lineage for supporting/contradicting claims;
- an explicit affected variable, economic path, kill-condition hit or reinforcement request for PASS/WARNING findings;
- explicit missing-evidence metric requests;
- deterministic scanner-result hashing;
- `NOT_IMPLEMENTED` for missing mandatory handlers;
- `RECOVERY_REQUIRED` for evidence gaps;
- blocking failure for mismatched scanner IDs, invented Evidence IDs or invalid findings.

The dispatcher itself cannot emit compiled assumptions or valuation outputs. Scanner-specific handlers feed typed findings to LLM Staff, Red Team and the ordinary Bridge/Compiler path.

The stage is `PARTIAL_LIVE`, not `LIVE_READY`, because source-aware handlers and fixtures do not yet cover every scanner ID in `config/module_requirement_scanner_map.yaml`. See `docs/ROCKET_INSIGHT_SCANNER_RUNTIME.md`.

## 5. OpenDART company-fact vertical

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

## 6. What the live front half does not solve

A `LIVE_READY` stage means its typed source/loader contract, traceability and fail-closed behavior are complete. It does **not** claim every jurisdiction, every source family or every company-specific KPI is built in.

OpenDART standard financial facts do not provide every Industry DNA requirement. Backlog quality, effective capacity, customer advances, qualification, project COD, clinical evidence, customer concentration and many segment KPIs still need company-specific filing-note, IR, primary-regulatory or calibrated alternative-data adapters.

Absence of a standard XBRL account ID is not permission to guess from a similar account name. The Control Plane should record the missing metric and enter Recovery/Capability handling.

## 7. Promotion rule

A stage moves toward `LIVE_READY` only when:

1. the source/loader contract is explicit;
2. freshness/revision behavior is auditable;
3. output is typed and traceable;
4. failure is fail-closed;
5. current-price/Street isolation remains intact;
6. fixtures and regression tests exist;
7. the Unit Contract or its canonical maintenance references identify downstream consumers and effects.

Changing a readiness label does not alter valuation formulas or bypass ordinary module-promotion governance.
