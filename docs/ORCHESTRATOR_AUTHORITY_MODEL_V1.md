# PRISM Orchestrator Authority Model v1

## Objective

LIVE_PRIMARY must be an orchestrator-controlled execution, not an LLM-led workflow. A valid investment result is canonical only when the strict entrypoint executes the full stage chain, the Audit/Freeze lineage is intact, and an execution attestation binds the complete stage receipt chain.

## Authority hierarchy

1. **Control Plane / Orchestrator** — sole LIVE_PRIMARY execution authority and stage owner.
2. **RocketTesla Context Engine** — deterministic pre-LLM scanner-routing authority compiled from Industry DNA and the typed Module Requirement Plan.
3. **LLM Staff** — observe, reason, challenge and propose only. No commit, valuation, probability, recovery authorization, audit, freeze, market loading or canonical publishing authority.
4. **Deterministic Compiler / Engines** — own assumption compilation, scenario mechanics, probability mathematics, valuation mathematics and consistency checks.
5. **Audit / Freeze** — deterministic final authorization boundary before any target Street or current-market comparison.
6. **Post-Freeze layer** — consumes the frozen intrinsic result; it cannot mutate that run's intrinsic inputs.

## Canonical execution path

`valuation_engine.strict_live_runtime.run_prism`

is the canonical LIVE_PRIMARY entrypoint.

The historical `valuation_engine.live_runtime.run_prism` remains available for regression compatibility only. Its result is not execution-attested and therefore cannot be presented as a canonical LIVE investment result.

Canonical flow:

`User Mission`
→ `Strict LIVE Entrypoint`
→ `Orchestrator Stage Authority`
→ `Industry DNA / Module Requirement Plan`
→ `RocketTesla Context Engine`
→ `Evidence Ledger`
→ `LLM Proposal Stages`
→ `Deterministic Bridge Validation / Assumption Compiler`
→ `Scenario / Probability / Valuation Engines`
→ `Audit`
→ `Intrinsic Freeze`
→ `Street / Market Comparison`
→ `Persistence / Final Report`
→ `Execution Attestation`

## RocketTesla Context Engine

RocketTesla is not an optional LLM thought pattern. It is the canonical context-routing layer before Researcher A.

The engine compiles a `RocketContextPlan` from `ModuleRequirementPlan`:

- mandatory scanner IDs,
- declared optional scanner IDs,
- explicitly activated optional scanners,
- deterministic execution order,
- plan hash.

Rules:

- Mandatory scanners cannot be silently skipped.
- Optional scanners cannot be activated unless declared by the typed plan.
- LLM Staff cannot add, remove or reorder scanner runners.
- Scanner findings are handed to LLM Staff only after deterministic dispatch.
- Target market-comparison Evidence remains forbidden pre-freeze.

## LLM proposal boundary

Actual external LLM callbacks run under `RuntimeActor.LLM` via `llm_proposal_scope()`.

Protected deterministic functions call `forbid_llm_decision(...)` before committing a decision. In particular an LLM callback cannot:

- call the assumption compiler,
- run Probability Engine v3,
- run continuous financial-path Monte Carlo,
- bind probabilities into compiled assumptions,
- emit canonical valuation/freeze/audit/market decisions through LLM proposal stages.

The owning orchestrator stage remains bound while the nested actor is temporarily narrowed to LLM.

## Stage output authority

Before an adapter result is committed to canonical context, the authority wrapper validates its output keys.

LLM proposal stages (`RESEARCHER_A`, `BLIND_RED_TEAM_B`, `RESEARCH_LOOP`) cannot emit deterministic decision domains such as compiled assumptions, valuation hashes, expected value, probability weighting authorization, audit hashes, freeze tokens or market/Street comparisons.

Any pre-freeze stage that attempts to emit target market/Street decision outputs is blocked.

## Recovery authority

Recovery providers run as proposal-only actors.

A provider setting an issue to `resolved=True` is not sufficient. If an original blocking Red-Team issue existed, the strict runtime requires:

1. every original blocker to remain identifiable,
2. the recovered proposal to mark the original blocker resolved,
3. explicit `recovery_resolution_evidence_ids`,
4. every cited Evidence ID to exist in the canonical EvidenceLedger,
5. no post-freeze market-comparison Evidence,
6. a deterministic `RecoveryResolutionReceipt` hash binding blocker IDs and Evidence lineage.

Without the receipt, the Recovery stage is blocked.

## Stage receipts and execution attestation

Every executed stage produces a `StageAuthorityReceipt` binding:

- run ID,
- stage ID,
- terminal status,
- committed output-key set.

A successful LIVE_PRIMARY result receives an `ExecutionAttestation` binding:

- run ID,
- execution mode,
- ordered stage receipt hashes,
- intrinsic freeze token hash,
- final stage.

The strict runtime persists `execution_attestation.json` alongside the immutable run artifacts. A result without this attestation is `non_canonical`.

## Decision owner matrix

| Decision | Owner | LLM role |
| --- | --- | --- |
| Industry/segment routing | deterministic router | none/proposal evidence only |
| RocketTesla scanner loadout | orchestrator + ModuleRequirementPlan | consume findings only |
| Hypothesis generation | LLM proposal | propose |
| Counter-thesis | LLM proposal | propose |
| Recovery research | LLM/provider proposal | propose evidence/resolution candidate |
| Recovery resolution | deterministic re-adjudication | no authorization |
| Bridge candidate | LLM proposal | propose |
| Assumption commit | deterministic compiler | forbidden |
| Scenario construction | deterministic engine | forbidden |
| Probability / Monte Carlo | deterministic engine | forbidden |
| Valuation math | deterministic engine | forbidden |
| Audit | deterministic audit | forbidden |
| Intrinsic freeze | Control Plane | forbidden |
| Target Street/current price | post-freeze loaders | no pre-freeze access |
| Canonical final result | strict entrypoint + attestation | forbidden to self-publish |

## Compatibility rule

Legacy modules remain callable for tests, research tooling and replay where explicitly intended. Their standalone output is not automatically a canonical LIVE result. Only the strict entrypoint can produce the execution attestation required for canonical investment reporting.
