# Capacity Commitment Gate

Status: implemented and wired into the canonical 33-stage `LIVE_PRIMARY` workflow.

## Incident

The first Sanil Electric analytical-equivalent valuation excluded a company-disclosed second-factory site acquisition from Core because the exact incremental revenue capacity was not disclosed. That incorrectly collapsed three different questions:

1. **Eligibility** — has the company made a sufficiently committed investment?
2. **Incrementality** — is the project outside the current operating/financial baseline?
3. **Magnitude** — what capacity, CAPEX and ramp can be disclosed or conservatively bounded?

A signed site purchase/lease contract or completed acquisition is sufficient to verify canonical `ProjectGate.LAND_CONTROL`. It does not prove exact output, utilization, margin or timing. Once LAND_CONTROL is verified and the project is outside the baseline, a zero-expansion Core is no longer valid: Core must include a conservative capacity/CAPEX/ramp path or the run must stop for recovery.

## Root cause

The repository already contained the correct concepts, but they were disconnected:

- Signal Intelligence had project-scoped `ProjectGateSet` and `LAND_CONTROL`.
- The v0.4 methodology and PER engine referred to committed/pre-invested capacity.
- The Bridge Analyst did not receive a typed project assessment.
- Scenario and valuation did not require every material project to be consumed.
- PER used a separate evidence boolean rather than the same Core assessment.
- Audit verified included assumptions but did not detect omitted material expansion evidence.

The failure was therefore an orchestration and omission-control defect, not a missing valuation idea.

## Canonical placement

No new public stage name is added. The gate is composed inside the existing canonical stages:

```text
RESEARCH_LOOP
→ EVIDENCE_TO_ASSUMPTION_BRIDGE
   1. strict Capacity Commitment Gate
   2. LLM Bridge Analyst with typed assessment
→ SCENARIO_BUILD
   1. capacity Bridge-consumption gate
   2. deterministic Assumption Compiler / scenario binding
   3. capacity scenario binding
→ DETERMINISTIC_VALUATION
   1. deterministic valuation
   2. DCF fingerprint
   3. capacity valuation binding
→ HIERARCHICAL_WARRANTED_PER
   1. Core / Expansion / residual PER
   2. capacity PER binding
→ DCF_PER_ASSUMPTION_CONSISTENCY_GATE
   1. canonical DCF–PER consistency
   2. capacity assessment consistency
→ AUDIT_GATE
   1. capacity omission/double-count audit
   2. generic audit and Intrinsic Freeze hash chain
```

Non-capacity routes emit a typed not-applicable assessment. A LIVE_PRIMARY audit without any capacity assessment blocks because that means the gate was bypassed.

## Typed contract

### Input

`CapacityCommitmentInput` provides one `CapacitySegmentCommitmentInput` for every `capacity_manufacturing` segment. Each segment supplies either:

- project-scoped `CapacityProjectBinding` objects; or
- explicit official Evidence that no active expansion exists.

Each project carries:

- `project_id` and `segment_id`;
- canonical `ProjectGateSet`;
- baseline inclusion status and Evidence;
- active/cancelled disposition and Evidence;
- capacity, site-area, committed-CAPEX, ramp-date and equipment Evidence IDs.

Project identity is never inferred from prose or URLs.

### Output

`CapacityCommitmentAssessment` freezes:

- verified project gates;
- LAND_CONTROL eligibility;
- baseline inclusion;
- disposition;
- Core inclusion obligation;
- quantification status;
- recovery requirement;
- qualifying Evidence IDs;
- a stable assessment hash.

Quantification statuses:

- `DISCLOSED` — positive committed capacity is disclosed;
- `BOUNDED_INPUTS_AVAILABLE` — exact capacity is absent but site area and/or committed CAPEX permits bounded derivation;
- `UNQUANTIFIED` — Core inclusion is mandatory but sizing is not defensible, so the run stops;
- `NOT_REQUIRED` — cancelled, already in baseline or not LAND_CONTROL-eligible.

## Collection contract

`capacity_manufacturing` requires explicit records for:

- `expansion_land_control`
- `expansion_baseline_inclusion`
- `expansion_capacity_committed`
- `expansion_site_area`
- `expansion_capex_committed`
- `expansion_ramp_date`
- `expansion_equipment_commitment`
- `expansion_cancelled`
- `no_active_capacity_expansion`

Providers must emit explicit status/absence evidence rather than convert `NOT_OBSERVED` into `NO_EVENT`.

Metric dimensions are enforced before an input can qualify:

- site area → `AREA` (`sqm`, `pyeong`)
- committed CAPEX → `MONEY`
- committed capacity → approved operating-capacity dimension (`POWER`, `COUNT`, `MASS`, or evidenced economic capacity in `MONEY`)
- ramp date and typed status fields → `DIMENSIONLESS` with role-specific semantic validation

A cancellation token cannot masquerade as land control or equipment commitment.

## Bridge completeness

Every Core-required project must consume three distinct Bridge roles:

1. `capacity` — conservative output/revenue ceiling uplift;
2. `capex` — expansion investment burden;
3. `ramp` — timing, utilization or qualification delay.

The three roles share one project-economic-path root but use distinct role paths:

```text
capacity_project:<project_id>:capacity
capacity_project:<project_id>:capex
capacity_project:<project_id>:ramp
```

Different projects require different roots. Each Bridge must consume Evidence authorized by the frozen project assessment. Missing role, missing Evidence, wrong unit, stale assessment or changed economic path blocks the run.

## Scenario and valuation binding

All mandatory role assumptions must compile exactly once into one Core scenario. The bound Core scenario must retain them, and the deterministic valuation result must expose all role economic paths it consumed.

This does not invent output from land area. It guarantees that the selected evaluator either uses a defensible bounded expansion model or fails closed instead of silently retaining zero expansion.

## PER semantics

Core Fundamental PER shares the same DCF growth, margin, reinvestment and duration fingerprint. Evidence already required to put committed capacity into Core cannot be reused to open Expansion-Adjusted PER. Expansion PER is reserved for incremental upside above the conservative Core path and needs separate Evidence.

## Audit and Freeze binding

The capacity audit adds six blocking checks:

- `material_capacity_evidence_consumed`
- `capacity_commitment_hash_binding`
- `core_capacity_floor_respected`
- `baseline_capacity_not_double_counted`
- `dcf_per_capacity_consistency`
- `capacity_double_count`

The capacity audit hash includes the exact assessment, Bridge-consumption, scenario-binding, valuation-binding and applicable PER-binding hashes. Generic Audit includes this hash, so Intrinsic Freeze is transitively bound to the capacity decision.

## Sanil interpretation

For Sanil Electric, a signed/completed second-factory site acquisition and committed expansion investment satisfy Core eligibility. Exact Street-estimated revenue CAPA remains inadmissible before Freeze unless independently verified. The correct response to an undisclosed exact CAPA is conservative bounded quantification or recovery, not a zero-expansion Core.

## Non-goals

The gate does not:

- infer exact capacity from land control alone;
- treat management aspiration as a contract;
- convert project-gate completion into probability;
- lower WACC because land was acquired;
- omit CAPEX or ramp constraints;
- add the same expansion through Core FCF, SOTP option and PER duration;
- allow Street/current-price evidence to open the gate before Freeze.

## Regression requirements

Tests cover:

1. LAND_CONTROL makes incremental Core inclusion mandatory;
2. announcement alone does not;
3. exact capacity is not required for eligibility;
4. unquantified eligible projects recover rather than default to zero;
5. source-layer and project identity enforcement;
6. project-scoped cancellation;
7. multiple independent projects;
8. baseline double-count prevention;
9. metric-specific dimensions and semantic status validation;
10. full capacity/CAPEX/ramp Bridge coverage;
11. scenario, valuation and PER hash binding;
12. Core Evidence cannot be reused as Expansion PER;
13. audit omission and double-count checks;
14. canonical adapter order in LIVE_PRIMARY.
