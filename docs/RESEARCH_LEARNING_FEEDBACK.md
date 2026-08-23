# Research Learning Feedback Loop

Status: canonical non-destructive learning contract for module-impact history and next-run research deployment.

## 1. Purpose

Decision Impact is useful only when the result survives the current run. This layer turns each
run's module-ablation record into an append-only learning history and feeds the latest scheduling
recommendation into the next `Module Requirement Plan`.

It does **not** allow historical correlations to rewrite the current intrinsic value, remove a
mandatory gate, or mutate a completed run.

```text
Run N Decision Impact
→ immutable per-run learning record
→ measured ModuleHistoryEntry cohort
→ next-run ResearchIntensity / LoadoutAction
→ LOAD_COMPANY_STATE
→ MODULE_REQUIREMENT_PLAN
→ non-destructive AdaptiveResearchLoadout
```

## 2. Persistence semantics

`ResearchLearningStore` writes one immutable JSON record per `ticker/run_id` under:

```text
learning/<ticker>/module-impact/<run_id>.json
```

The record preserves:

- baseline decision outcome;
- every module status (`MEASURED`, `NOT_MEASURABLE`, `NOT_APPLICABLE`, `FAILED`);
- measured impact assessment;
- research effort;
- joint-ablation output;
- loadout recommendations.

Only observations with an actual `ModuleImpactAssessment` enter the statistical prior history.
`NOT_MEASURABLE` is retained for audit but is never treated as zero impact.

## 3. Stage integration

### LOAD_COMPANY_STATE

`load_research_learning_adapter` loads:

- `module_impact_prior_history`;
- `prior_research_loadout_recommendations`;
- prior learning-record count.

Generic Decision Impact uses the persisted history when generating the current run's intensity
recommendation. Explicit call-site history overrides the persisted cohort for the same module to
avoid duplicate counting.

### MODULE_REQUIREMENT_PLAN

`module_requirement_plan_adapter` builds the canonical plan from Industry DNA, then overlays an
`AdaptiveResearchLoadout`.

The canonical plan remains unchanged. The overlay may classify optional work as:

- active;
- conditional;
- sampled;
- governance-review candidate;
- unchanged.

### SAVE_STATE

`save_research_learning_adapter` writes the current run's impact batch only after an Intrinsic
Freeze Token exists. A duplicate `run_id` fails closed.

## 4. Mandatory-unit protection

Canonical common-core units and mandatory scanners are always active. A historical
`PROPOSE_DOWNRANK` recommendation against a mandatory scanner creates a governance-review flag,
but the scanner remains in `active_units`.

```text
mandatory scanner + low historical impact
→ active in current run
→ governance review candidate
→ no automatic deletion
```

A user/governance decision and a canonical registry change are still required to remove or weaken
a mandatory requirement.

## 5. Trigger handling

Conditional modules are evaluated against the **current run's** trigger state. A module that was
active in the prior run does not remain active merely because the prior recommendation said
`ACTIVATE_IF_TRIGGERED`.

No trigger supplied means conditional/dormant, not active by default.

## 6. Maintenance boundaries

- Historical impact guides research scheduling, never intrinsic assumptions.
- A single low-impact run cannot produce automatic retirement.
- Missing counterfactual support cannot produce a down-rank.
- Joint-ablation materiality blocks retirement based on a misleading leave-one-out zero.
- Learning records are immutable; corrections require a new run/record.
- The Unit Contract Registry remains static expected-design truth. Learning history is empirical
  run history.

## 7. Canonical implementation

- `src/valuation_engine/research_learning.py`
- `src/valuation_engine/state_learning_adapter.py`
- `src/valuation_engine/adaptive_loadout.py`
- `src/valuation_engine/module_plan_adapter.py`
- `src/valuation_engine/audit_adapter.py`
