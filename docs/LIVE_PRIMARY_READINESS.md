# PRISM LIVE_PRIMARY Readiness Map v1.0

Status: canonical maintenance record for distinguishing full PRIMARY_SHADOW integration from real-source LIVE_PRIMARY readiness.

## 1. Why this exists

A 32-stage Control Plane run can be fully integrated while some stages still depend on caller-supplied profiles, shadow assertions, incomplete source coverage, or a narrow evaluator set. `PRIMARY_SHADOW PASS` therefore must never be reported as `LIVE_PRIMARY complete`.

`config/live_primary_readiness.yaml` is the machine-readable readiness source. It must contain exactly one row for every stage in `config/control_plane_stage_registry.yaml`.

Readiness states:

- `LIVE_READY`: can execute with current/live inputs under a declared source/loader contract.
- `PARTIAL_LIVE`: at least one reusable live path exists, but material source/method coverage remains incomplete.
- `RUNTIME_READY`: deterministic runtime is complete once typed upstream inputs are supplied.
- `ADAPTER_REQUIRED`: reusable components exist, but a canonical live stage adapter is still missing.
- `SHADOW_ONLY`: current stage behavior proves integration only.
- `CONDITIONAL_NOT_IMPLEMENTED`: stage is route-dependent and intentionally fails closed when required without an implementation.

A new workflow stage without a readiness row is a maintenance error.

## 2. Current snapshot

At the v1.0 registry snapshot:

- canonical stages: **32 / 32 mapped**;
- `LIVE_READY` or `RUNTIME_READY`: **19**;
- `PARTIAL_LIVE`: **3**;
- explicit live gaps (`ADAPTER_REQUIRED`, `SHADOW_ONLY`, `CONDITIONAL_NOT_IMPLEMENTED`): **10**.

These counts are not a percentage-complete score. A single unresolved stage can still block a company if its Industry DNA makes that capability material.

The current `main` already includes executable Module Requirement planning, automatic ablation, append-only impact history, adaptive Control Plane loadout, atomic state persistence and a full 32-stage PRIMARY_SHADOW runtime. None of those integration achievements should be relabelled as complete live-source coverage.

The highest-value remaining live gaps are currently:

1. company/entity resolution from live identifiers;
2. live Industry Knowledge snapshot/freshness orchestration;
3. evidence-backed segment decomposition and Industry DNA route construction;
4. actual Rocket Insight scanner dispatch rather than plan-only inspection;
5. route-specific Upstream Funding adapter;
6. live Economic-Twin/Beta and WACC input adapters;
7. Warranted PER stage adapter;
8. broader exact-evaluator coverage across the 19 Economic Archetypes;
9. probability calibration datasets beyond the gating contract.

## 3. OpenDART company-fact vertical

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

## 4. What this adapter does not solve

OpenDART standard financial facts do **not** provide every Industry DNA requirement. Backlog quality, effective capacity, customer advances, qualification, project COD, clinical evidence, customer concentration, and many segment KPIs still need company-specific filing-note, IR, primary-regulatory, or calibrated alternative-data adapters.

Absence of a standard XBRL account ID is not permission to guess from a similar Korean account name. The Control Plane should record the missing metric and enter Recovery/Capability handling.

## 5. Promotion rule

A stage moves toward `LIVE_READY` only when:

1. the source/loader contract is explicit;
2. freshness/revision behavior is auditable;
3. output is typed and traceable;
4. failure is fail-closed;
5. current-price/Street isolation remains intact;
6. fixtures and regression tests exist;
7. the Unit Contract identifies its downstream consumers and effects.

Changing a readiness label does not alter valuation formulas or bypass ordinary module-promotion governance.
