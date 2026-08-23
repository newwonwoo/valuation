# Generic Runtime Governance — Doctrine Coverage, Decision Impact, Audit and Freeze

Status: canonical runtime contract for `PRIMARY_SHADOW` and future `LIVE_PRIMARY` runs.

## 1. Purpose

The generic runtime must not rely on tests, callers or an LLM to inject a hand-written
`DoctrineCoverageEntry` tuple. Coverage is generated from:

1. the canonical `Unit Contract & Impact Registry`;
2. the actual stage sequence;
3. the actual terminal `StageTrace` records;
4. the required/optional stage contract.

The runtime order is:

```text
completed pre-audit stages
→ generated pre-audit Doctrine Coverage
→ module counterfactual / guardrail impact record
→ Generic Intrinsic Audit
→ rebuilt final Doctrine Coverage
→ atomic Intrinsic Freeze Token
→ post-freeze Street / market access
```

## 2. Why coverage is split in two

Audit needs to verify that every upstream unit left a terminal trace. Audit itself cannot be
marked PASS before it runs. Therefore one mutable coverage object would create a circular
contract.

The Control Plane uses two immutable snapshots:

### Pre-audit snapshot

Contains all Unit Contracts mapped to stages completed before `AUDIT_GATE`, plus the GLOBAL
Doctrine and Control Plane units. It is the coverage input to Generic Audit.

### Final freeze snapshot

Rebuilt after `AUDIT_GATE` passes. The passed Audit trace covers both `DECISION_IMPACT` and
`AUDIT_GATE` according to their Unit Contracts. `INTRINSIC_FREEZE` is marked prospectively PASS
only inside the atomic token-issuance operation; token validation remains the authority.

## 3. Decision Impact execution

`AUDIT_GATE` is the orchestration boundary for two distinct units:

```text
DECISION_IMPACT
→ AUDIT_GATE
```

Decision Impact runs first and cannot mutate the baseline intrinsic result. It derives active
units from generated pre-audit coverage and the Unit Contract Registry.

For each active unit:

- a supplied deterministic counterfactual runner produces a measured ablation;
- a supplied guardrail probe may establish guardrail criticality;
- an applicable unit without a reproducible counterfactual becomes `NOT_MEASURABLE`;
- a non-applicable unit becomes `NOT_APPLICABLE`;
- exceptions become explicit `FAILED` observations.

`NOT_MEASURABLE` is never interpreted as zero impact. Counterfactual failures are visible and
may block research-efficiency promotion, but do not rewrite the already calculated intrinsic
value.

## 4. Freeze requirements

The generic Freeze requires all of the following:

- `audit_passed`;
- `decision_impact_completed` — an impact record exists, even if some units are explicitly
  `NOT_MEASURABLE`;
- assumption, valuation, audit, industry and source snapshot hashes;
- complete generated final Doctrine Coverage;
- no unresolved blocking coverage entry.

Market access remains impossible without a token for the same `run_id`.

## 5. Runtime artifacts

The orchestration context exposes:

- `pre_audit_doctrine_coverage`
- `pre_audit_expected_unit_ids`
- `decision_impact_result`
- `decision_impact_batch`
- `decision_impact_hash`
- `module_impact_assessments`
- `research_loadout_recommendations`
- `retirement_review_candidates`
- `runtime_doctrine_coverage`
- `runtime_expected_unit_ids`
- `intrinsic_freeze_token`

The static Unit Contract Registry remains expected-design truth. The above artifacts are
run-specific actual truth.

## 6. Maintenance rules

- Adding a stage without a Unit Contract mapping may leave only the GLOBAL units in coverage;
  add or revise the canonical Unit Contract before claiming the stage governs value.
- Adding a value-affecting unit without a counterfactual adapter is allowed only as an explicit
  `NOT_MEASURABLE` state; it cannot be called zero-impact.
- Retirement recommendations require repeated measured history. A single run or a missing
  counterfactual is insufficient.
- Audit and Freeze may never be ablated by mutating the canonical run. Use isolated guardrail
  probes.
- Manually supplied legacy `doctrine_coverage` fields are not used as Freeze authority; generated
  runtime coverage is authoritative.
