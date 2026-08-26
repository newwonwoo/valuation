# Capacity Commitment Gate

Status: root-cause analysis complete; typed contract implementation in progress

## 1. Incident

During the first Sanil Electric valuation run, official evidence that the company had signed and completed a second-factory land/building acquisition and was investing in expansion was treated as insufficient for Core DCF because the exact incremental revenue capacity was not disclosed.

That conclusion violated the intended doctrine:

- an exact capacity amount is required to size the uplift;
- it is **not** required to decide whether a zero-expansion Core is still valid;
- once land control is verified, Core must include a conservative expansion path or stop for bounded quantification.

A signed site purchase/lease contract is sufficient to verify `ProjectGate.LAND_CONTROL`. It does not prove the full capacity amount, timing, utilization or margin.

## 2. Root cause

The failure was not that the repository had no relevant concept. The repository already had the right project-realization primitive, but it was stranded in the Signal Intelligence layer and never became a mandatory valuation obligation.

### 2.1 Existing canonical gate was not consumed

`signal_intelligence.py` already defines independent `ProjectGate`s and a project-scoped `ProjectGateSet`, including `LAND_CONTROL`, construction, commissioning and revenue. It also correctly states that realization maturity is an evidence-completion ratio, not a probability.

However:

- `ProjectGateSet` was not passed as a typed input to the Bridge Analyst;
- no stage between Research Loop and Scenario Build converted verified `LAND_CONTROL` into a Core-inclusion obligation;
- DCF, PER and Audit did not bind to a shared project-gate assessment hash.

The actual defect was an orchestration disconnect, not a missing land-control vocabulary.

### 2.2 Methodology was localized to PER

The canonical v0.4 method document and PER engine mention committed/pre-invested capacity, but the executable check is attached to Expansion-Adjusted PER. There was no shared project-capacity contract consumed by Core DCF and PER.

### 2.3 Collection requirements were too coarse

`capacity_manufacturing` requested `expansion_capex`, capacity and utilization, but did not require the evidence needed to resolve the independent project gates and baseline treatment:

- land control;
- incremental-vs-baseline status;
- disclosed committed capacity;
- site area;
- committed CAPEX;
- ramp date;
- equipment commitment;
- cancellation or explicit no-active-expansion evidence.

Because the Company Collection Plan only collects metrics declared by the Module Requirement Plan, land-control evidence could be missed without failing coverage.

### 2.4 Evidence was not project-bound at the valuation boundary

A generic `EvidenceRecord` has segment and provenance, but not project identity. Free-form notes or source URLs cannot safely group multiple concurrent expansion projects.

The existing `ProjectGateSet.project_id` solves this, but the valuation path did not require an authorized project-to-segment binding. Without it, one cancelled project could incorrectly suppress another active project, or already-operating capacity could be added twice.

### 2.5 The LLM defined both the bridge and its completeness boundary

The Bridge Analyst proposes bridge drafts and Assumption Specs. Existing validation checks that referenced Evidence exists and that transforms are registered. It does not ask whether all material project-gate obligations were consumed.

Therefore an omitted capacity bridge could pass as long as the remaining bridges were internally valid.

### 2.6 Compiler and Audit checked correctness, not omission

The Assumption Compiler correctly recalculates proposed assumptions, validates units and blocks market leakage. The Audit correctly replays hashes and traces included assumptions. Neither compares the frozen Evidence Ledger and project gates against mandatory economic-path obligations.

The system could prove that included assumptions were valid while missing a material assumption altogether.

### 2.7 Manual analytical-equivalent execution exposed the gap

The Sanil run was not a persisted LIVE_PRIMARY provider run. It followed the repository logic manually. Without a typed project-gate artifact, the operator could interpret “capacity amount not disclosed” as “exclude expansion” instead of “Core uplift required; quantification recovery needed.”

## 3. Correct design principle

> Eligibility, incrementality and magnitude are three separate decisions.

1. **Eligibility:** Is canonical `ProjectGate.LAND_CONTROL` verified from official/filing Evidence?
2. **Incrementality:** Is the project outside the financial/operating baseline, rather than already reflected in actual capacity and earnings?
3. **Magnitude:** What capacity, ramp, CAPEX, utilization and margin can be disclosed or conservatively bounded?

A signed site contract is enough for eligibility. It is not enough to invent a capacity number. Capacity already included in the baseline cannot be added again.

## 4. Reuse the canonical ProjectGate model

No parallel linear capacity-stage enum is introduced.

The gate consumes the existing independent project gates:

- `ANNOUNCEMENT`
- `LAND_CONTROL`
- `FINANCING`
- `PERMIT_APPLICATION`
- `PERMIT_APPROVAL`
- `OFFTAKE_CONTRACT`
- `GRID_UTILITIES`
- `CONSTRUCTION`
- `COMMISSIONING`
- `REVENUE`

`LAND_CONTROL` is the Core eligibility gate because it captures a signed purchase/lease contract or completed site acquisition. Other gates improve timing, execution and confidence but do not silently substitute for land control.

Cancellation and baseline inclusion are separate typed dispositions because project gates are independent and an operating project may already be embedded in the base period.

## 5. Typed inputs and outputs

### Input

`CapacityCommitmentInput` contains one `CapacitySegmentCommitmentInput` for each `capacity_manufacturing` segment.

Each segment must provide either:

- project-scoped `CapacityProjectBinding`s; or
- explicit official Evidence that there is no active capacity expansion.

Each project binding contains:

- `project_id` and `segment_id`;
- the canonical `ProjectGateSet`;
- `BaselineInclusionStatus` and its Evidence;
- active/cancelled disposition and its Evidence;
- disclosed capacity, site-area, committed-CAPEX, ramp-date and equipment Evidence IDs.

Project identity is never inferred from free-form notes.

### Output

`CapacityCommitmentAssessment` contains project and segment assessments:

- verified canonical gates;
- whether `LAND_CONTROL` is resolved and verified;
- whether the project is incremental to baseline;
- whether Core inclusion is mandatory;
- quantification status;
- recovery requirement;
- qualifying Evidence IDs;
- stable assessment hash.

Quantification statuses:

- `DISCLOSED`: a positive committed capacity amount is available;
- `BOUNDED_INPUTS_AVAILABLE`: exact capacity is absent, but site area and/or committed CAPEX supports bounded derivation;
- `UNQUANTIFIED`: Core inclusion is mandatory but no defensible sizing input exists;
- `NOT_REQUIRED`: the project is not Core-eligible, is cancelled, or is already included in baseline.

## 6. Orchestration

Target canonical order:

`RESEARCH_LOOP`
→ `CAPACITY_COMMITMENT_GATE`
→ `EVIDENCE_TO_ASSUMPTION_BRIDGE`
→ `SCENARIO_BUILD`

The gate runs after recovery has finalized the Evidence Ledger and before the LLM proposes assumptions.

For non-capacity routes it ends as `SKIPPED_NOT_APPLICABLE` without calling a loader.

For a capacity route:

- no typed project loader → `NOT_IMPLEMENTED`;
- missing segment/project coverage → blocked or recovery;
- unresolved `LAND_CONTROL` → `RECOVERY_REQUIRED`; absence is not no contract;
- verified `LAND_CONTROL` + unknown baseline treatment → `RECOVERY_REQUIRED`;
- verified `LAND_CONTROL` + already in baseline → pass, no incremental uplift;
- verified `LAND_CONTROL` + incremental + disclosed/bounded inputs → pass with mandatory Core-consumption obligation;
- verified `LAND_CONTROL` + incremental + no sizing input → `RECOVERY_REQUIRED`, never silent zero expansion;
- cancelled project → pass with the expansion path disabled and cancellation preserved as a kill condition.

## 7. Core, Bull and PER semantics

### Core DCF

Core includes existing operations plus a conservative path for every land-controlled project proven incremental to the baseline. The path must include corresponding CAPEX and ramp constraints. Land control does not authorize full nameplate utilization or peak margin.

### Verified Bull

Bull may use a higher, separately evidenced utilization/ramp outcome. It cannot repeat the same committed capacity as an independent SOTP option.

### PER

Core Fundamental PER must share the Core DCF capacity path. The existing separate boolean `expansion_is_committed_or_preinvested` must be replaced or bound to the shared assessment hash. Expansion-Adjusted PER is reserved for additional evidence-backed upside above the conservative Core path, not for capacity already required in Core.

## 8. Enforcement points

### Collection

Add canonical metrics:

- `expansion_land_control`;
- `expansion_baseline_inclusion`;
- `expansion_capacity_committed`;
- `expansion_site_area`;
- `expansion_capex_committed`;
- `expansion_ramp_date`;
- `expansion_equipment_commitment`;
- `expansion_cancelled`;
- `no_active_capacity_expansion`.

### LLM Staff

The Staff context receives the typed assessment. If Core inclusion is mandatory, a Bridge bundle without the required capacity/CAPEX/ramp economic path is invalid.

### Scenario Build

Scenario Build compares compiled assumptions with the assessment. A land-controlled incremental project cannot bind a Core scenario that assumes zero or omits the required capacity path.

### DCF and PER

Both engines consume the same `capacity_commitment_assessment_hash` and economic path IDs.

### Audit

Add blocking checks:

- `material_capacity_evidence_consumed`;
- `capacity_commitment_hash_binding`;
- `core_capacity_floor_respected`;
- `baseline_capacity_not_double_counted`;
- `dcf_per_capacity_consistency`;
- `capacity_double_count`.

## 9. Non-goals

The gate does not:

- infer exact capacity from a land contract alone;
- treat management aspiration as land control;
- assume that construction or commissioning proves every other project gate;
- convert project-gate completion ratios into execution probabilities;
- assign an uncalibrated probability to construction success;
- lower WACC because a site was purchased;
- add the same expansion through Core FCF, PER duration and SOTP option simultaneously.

## 10. Regression requirements

At minimum, tests must prove:

1. verified `LAND_CONTROL` makes incremental Core inclusion mandatory;
2. an announcement alone does not;
3. exact capacity is not required for eligibility;
4. missing quantification after eligibility causes recovery, not zero expansion;
5. External/Street commentary cannot verify land control pre-freeze;
6. cancellation disables only the matching project;
7. multiple projects are assessed independently;
8. already-in-baseline capacity is not added again;
9. unknown baseline treatment causes recovery;
10. non-capacity segments are skipped without invoking a loader;
11. DCF and PER cannot consume different assessments;
12. committed CAPEX cannot be omitted or deducted twice;
13. the assessment is hash-bound to the frozen Evidence Ledger.
