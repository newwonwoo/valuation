# PRISM Unit Contract & Impact Map

Status: canonical maintenance record  
Source of truth: `config/unit_contract_registry.yaml`  
Runtime lookup: `src/valuation_engine/unit_contracts.py`

## 1. Why this exists

The workflow map says **when** a unit runs. Decision Impact says **what it changed in one run**. This registry answers the maintenance question in between:

> What does this unit consume, what does it emit, who consumes that output, and through which allowed path may it affect the final conclusion?

Without this layer, a scanner can keep collecting information without a downstream consumer, or a module can start changing a variable it was never authorized to change.

The canonical separation is:

```text
Workflow Map
  WHEN does it run?
        ↓
Unit Contract & Impact Registry
  WHAT goes in / WHAT comes out / WHO consumes it / WHAT may it affect?
        ↓
Run ModuleImpactTrace
  WHAT did it actually affect this time?
        ↓
Decision Impact / Sensitivity History
  HOW MUCH did it matter and was the research effort worth it?
```

## 2. The maintenance contract

Every canonical unit must declare:

- `unit_id` and `unit_type`
- implementation status
- workflow stages
- purpose
- inputs
- outputs
- downstream consumers
- allowed effect types
- final outputs it may reach
- canonical implementation/document references
- forbidden effects

A new scanner, gate, engine or LLM role without this contract is not eligible for normal Control Plane deployment.

## 3. Standard unit types

`doctrine`, `controller`, `source_adapter`, `normalizer`, `router`, `scanner`, `gate`, `llm_role`, `bridge`, `compiler`, `scenario_engine`, `risk_engine`, `valuation_engine`, `aggregator`, `audit`, `market_layer`, `monitor`, `learning`, `reporter`, `governance`.

Do not call every component a generic “module”. The type tells maintainers what authority the component has.

## 4. Standard effect types

A unit may only produce effects declared in its contract:

- `evidence_effect`: changes evidence coverage/quality/provenance
- `hypothesis_effect`: creates, challenges or resolves hypotheses
- `routing_effect`: changes stage/module/scanner/revalidation routing
- `assumption_effect`: changes a compiled economic input through the authorized Bridge/Compiler path
- `timing_effect`: changes timing or realization schedule
- `probability_effect`: changes an event/distribution candidate through the calibrated probability path
- `method_effect`: changes the permitted or selected valuation method
- `value_effect`: changes deterministic valuation output
- `guardrail_effect`: blocks an invalid/prohibited state
- `reporting_effect`: changes only post-computation presentation/comparison

If an actual run shows an effect type not declared here, that is an architecture violation even when the numeric result looks reasonable.

## 5. Forward and reverse lookup

### Forward question

> If `UPSTREAM_FUNDING_SCAN` changes, what downstream units can be affected?

Use `UnitContractRegistry.forward_dependencies()`.

Conceptually:

```text
UPSTREAM_FUNDING_SCAN
→ EVIDENCE_TO_ASSUMPTION_BRIDGE
→ ASSUMPTION_COMPILER
→ WACC_ENGINE / SCENARIO_ENGINE / DETERMINISTIC_VALUATION
→ AUDIT_GATE
→ INTRINSIC_FREEZE
→ post-freeze comparison
```

### Reverse question

> Why did WACC change?

Use `UnitContractRegistry.reverse_dependencies()` and then the run-specific Evidence/Bridge traces.

```text
WACC_ENGINE
← HIERARCHICAL_BETA_ENGINE
← UPSTREAM_FUNDING_SCAN
← ASSUMPTION_COMPILER
← EVIDENCE_TO_ASSUMPTION_BRIDGE
← evidence / signals / company primary
```

Static dependency tells us **where to look**. Run trace tells us **what actually happened**.

## 6. Expected vs actual impact audit

The static registry is the expected design. `ModuleImpactTrace` is the observed run.

```text
Expected contract
        ↓
Actual ModuleImpactTrace
        ↓
Compare
├─ expected path observed     → normal
├─ expected path not observed → low-impact / disconnected / research-efficiency review
└─ undeclared path observed   → architecture violation
```

Examples:

### Backlog scanner collected evidence but changed nothing

Expected:

```text
backlog evidence → quantity/timing Bridge → compiled assumption → value
```

Actual:

```text
backlog evidence → no Bridge → no assumption/value effect
```

This is not automatically wrong, but repeated high-cost no-impact runs are candidates for conditional or sampled deployment.

### Patent scanner changes margin directly

If its contract only allows evidence/hypothesis effects, a direct `assumption_effect` is an undeclared effect and should be stopped before valuation.

### Audit has zero value delta

That does not make Audit useless. If removing Audit permits price leakage or double counting, it is `GUARDRAIL_CRITICAL` and remains mandatory.

## 7. Source-of-truth rules

1. `config/unit_contract_registry.yaml` is the static unit-contract truth.
2. Existing unit-specific registries remain authoritative for their internal details. This registry links them; it does not copy all their contents.
3. `ModuleImpactTrace` records actual run effects and must not overwrite the static contract.
4. `decision_impact.py` measures materiality and research efficiency; it must not redefine unit authority.
5. Documentation is explanatory. If prose conflicts with the YAML contract or higher Doctrine, the higher canonical source wins.

## 8. Change protocol

When changing or adding a unit:

1. update/add the unit contract;
2. identify all input/output/consumer changes;
3. check forward and reverse dependencies;
4. declare any new effect type explicitly;
5. add/adjust run impact tracing;
6. add sensitivity/counterfactual coverage when the unit can influence decisions or value;
7. run the registry validator and full regression suite;
8. if the change creates a new canonical mechanism, follow Module Promotion and explicit approval rules.

A code change that alters outputs or downstream consumers without updating this registry is an incomplete maintenance change.

## 9. Relationship to research efficiency

The purpose of the registry is not just documentation. It lets the Control Plane distinguish four cases:

```text
A. Applicable + connected + material
   → keep / deepen

B. Applicable + connected + low observed materiality
   → conditional / sample based on history

C. Applicable + no downstream connection
   → wiring defect or low-value research candidate

D. Not applicable + research performed
   → avoidable waste
```

This prevents the system from confusing “more research” with “better valuation”.

## 10. Validation

Run:

```bash
PYTHONPATH=src python scripts/validate_unit_contract_registry.py
PYTHONPATH=src pytest -q
```

The validator fails on duplicate IDs, unsupported unit/effect types, missing contracts, and unresolved unit-consumer references except explicit virtual workflow boundaries.
