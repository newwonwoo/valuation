# Capacity Commitment Gate

Status: design + contract implementation in progress

## 1. Incident

During the first Sanil Electric valuation run, official evidence that the company had signed and completed a second-factory land/building acquisition and was investing in expansion was treated as insufficient for Core DCF because the exact incremental revenue capacity was not disclosed.

That conclusion violated the intended doctrine:

- an exact capacity amount is required to size the uplift;
- it is **not** required to decide whether a zero-expansion Core is still valid;
- once sufficiently irreversible pre-investment exists, Core must include a conservative expansion path or stop for bounded quantification.

A signed site purchase/lease contract is the minimum Core-inclusion threshold. It does not prove the full capacity amount, timing, utilization or margin.

## 2. Root cause

The failure was not one isolated analyst mistake. The repository had an incomplete control path.

### 2.1 Methodology was localized to PER

The canonical v0.4 method document and PER engine mention committed/pre-invested capacity, but the executable check is attached to Expansion-Adjusted PER. There was no shared capacity commitment contract consumed by Scenario Build, Core DCF and PER.

### 2.2 Collection requirements were too coarse

`capacity_manufacturing` requested `expansion_capex`, capacity and utilization, but did not require commitment-state evidence such as:

- board approval;
- signed site contract;
- site acquisition;
- permit;
- construction contract/start;
- equipment order/installation;
- commissioning and operating status.

Because the Company Collection Plan only collects metrics declared by the Module Requirement Plan, site-control evidence could be missed without failing coverage.

### 2.3 Evidence had no typed commitment meaning

A generic `EvidenceRecord` can store a filing fact, but it cannot distinguish an announcement from an executed land contract in a deterministic way. The semantic difference lived in prose, analyst judgment or a free-form metric name.

### 2.4 The LLM defined both the bridge and its completeness boundary

The Bridge Analyst proposes assumption drafts and Assumption Specs. Existing validation checks that referenced Evidence exists and that the transform is registered. It does not ask whether all material eligible Evidence was consumed.

Therefore an omitted capacity bridge could pass as long as the remaining bridges were internally valid.

### 2.5 Compiler and Audit checked correctness, not omission

The Assumption Compiler correctly recalculates proposed assumptions, validates units and blocks market leakage. The Audit correctly replays hashes and traces included assumptions. Neither compares the frozen Evidence Ledger against mandatory economic-path obligations.

The system could prove that included assumptions were valid while missing a material assumption altogether.

### 2.6 Manual analytical-equivalent execution weakened the last control

The Sanil run was not a persisted LIVE_PRIMARY provider run. It followed the repository logic manually. This exposed the design weakness: without a typed gate, the operator could interpret “capacity amount not disclosed” as “exclude expansion” instead of “Core uplift required; quantification recovery needed.”

## 3. Design principle

> Eligibility and magnitude are separate decisions.

1. **Eligibility:** Has the company crossed an irreversible-enough commitment threshold?
2. **Magnitude:** What capacity, ramp, CAPEX, utilization and margin can be supported or conservatively bounded?

A signed site contract is enough for eligibility. It is not enough to invent a capacity number.

## 4. Commitment ladder

The canonical ladder is:

1. `ANNOUNCED`
2. `BOARD_APPROVED`
3. `SITE_OPTIONED`
4. `SITE_CONTRACTED` — **minimum Core-inclusion threshold**
5. `SITE_ACQUIRED`
6. `PERMITTED`
7. `CONSTRUCTION_CONTRACTED`
8. `UNDER_CONSTRUCTION`
9. `EQUIPMENT_ORDERED`
10. `EQUIPMENT_INSTALLED`
11. `COMMISSIONING`
12. `OPERATING`
13. `CANCELLED`

The latest active official/filing event controls. `CANCELLED` overrides prior progress until new superseding Evidence is recorded.

## 5. Typed output

`CapacityCommitmentAssessment` is produced per run and contains one segment assessment for each `capacity_manufacturing` segment:

- latest verified commitment stage;
- qualifying and supporting Evidence IDs;
- whether Core inclusion is mandatory;
- quantification status;
- recovery requirement;
- disclosed capacity, site-area, committed-CAPEX and ramp-date Evidence IDs;
- stable assessment hash.

Quantification statuses:

- `DISCLOSED`: a positive committed capacity amount is available;
- `BOUNDED_INPUTS_AVAILABLE`: exact capacity is absent, but site area and/or committed CAPEX supports a bounded derivation;
- `UNQUANTIFIED`: commitment crossed the threshold but no defensible sizing input exists;
- `NOT_REQUIRED`: no Core-qualifying commitment exists.

## 6. Orchestration

Target canonical order:

`RESEARCH_LOOP`
→ `CAPACITY_COMMITMENT_GATE`
→ `EVIDENCE_TO_ASSUMPTION_BRIDGE`
→ `SCENARIO_BUILD`

The gate runs after recovery has finalized the Evidence Ledger and before the LLM proposes assumptions.

For non-capacity routes it ends as `SKIPPED_NOT_APPLICABLE`.

For a capacity route:

- no official commitment-stage Evidence → `RECOVERY_REQUIRED`;
- below `SITE_CONTRACTED` → pass, no mandatory Core uplift;
- `SITE_CONTRACTED` or above + disclosed/bounded sizing inputs → pass with a mandatory Core-consumption obligation;
- `SITE_CONTRACTED` or above + no sizing input → `RECOVERY_REQUIRED`, never silent zero expansion;
- latest event `CANCELLED` → pass with the expansion path disabled and cancellation Evidence preserved as a kill condition.

## 7. Core, Bull and PER semantics

### Core DCF

Core includes existing operations plus a conservative path for commitment-qualified expansion. The Core path must include the corresponding CAPEX and ramp constraints. Site commitment does not authorize full nameplate utilization or peak margin.

### Verified Bull

Bull may use a higher, separately evidenced utilization/ramp outcome. It cannot repeat the same committed capacity as an independent SOTP option.

### PER

Core Fundamental PER must share the Core DCF capacity path. The existing separate boolean `expansion_is_committed_or_preinvested` will be replaced or bound to the shared assessment hash. Expansion-Adjusted PER is reserved for additional evidence-backed upside above the conservative Core path, not for capacity already required in Core.

## 8. Enforcement points

### Collection

Add canonical metrics:

- `capacity_commitment_stage`;
- `expansion_capacity_committed`;
- `expansion_site_area`;
- `expansion_capex_committed`;
- `expansion_ramp_date`;
- `expansion_equipment_commitment`.

### LLM Staff

The Staff context receives the typed assessment. If Core inclusion is mandatory, a Bridge bundle without the required capacity/CAPEX/ramp economic path is invalid.

### Scenario Build

Scenario Build compares compiled assumptions with the assessment. A commitment-qualified segment cannot bind a Core scenario that assumes zero or omits the required capacity path.

### DCF and PER

Both engines consume the same `capacity_commitment_assessment_hash` and economic path IDs.

### Audit

Add blocking checks:

- `material_capacity_evidence_consumed`;
- `capacity_commitment_hash_binding`;
- `core_capacity_floor_respected`;
- `dcf_per_capacity_consistency`;
- `capacity_double_count`.

## 9. Non-goals

The gate does not:

- infer exact capacity from a land contract alone;
- treat management aspiration as committed capacity;
- assign an uncalibrated probability to construction success;
- lower WACC because a site was purchased;
- add the same expansion through Core FCF, PER duration and SOTP option simultaneously.

## 10. Regression requirements

At minimum, tests must prove:

1. `SITE_CONTRACTED` makes Core inclusion mandatory.
2. A mere announcement does not.
3. Exact capacity is not required for eligibility.
4. Missing quantification after the threshold causes recovery, not zero expansion.
5. External/Street commentary cannot open the gate pre-freeze.
6. A later cancellation overrides an earlier acquisition.
7. Non-capacity segments are skipped.
8. DCF and PER cannot consume different capacity assessments.
9. Committed CAPEX cannot be omitted or deducted twice.
10. The assessment is hash-bound to the frozen Evidence Ledger.
