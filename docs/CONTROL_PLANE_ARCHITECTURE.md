# PRISM Valuation Control Plane — Canonical Architecture v1.2

Status: canonical control-plane contract.

## 1. Constitutional chain of authority

`Doctrine → Control Plane → Knowledge + LLM Staff → Evidence/Hypothesis/Bridge Proposal → Assumption Compiler → Deterministic Engines → Audit → Intrinsic Freeze → Street/Market → State/Learning`

No component may silently bypass the component immediately below it.

### Doctrine
`SKILL.md`, `AGENTS.md`, V04/V05 contracts and policy registries define rules. They do not perform analysis or valuation arithmetic.

### Control Plane
The Control Plane selects mission mode, execution mode, current stage, mandatory modules/scanners, required evidence, recovery path, access permissions and whether a stage may advance. It does not interpret business evidence or calculate value.

### LLM Staff
The LLM is an analytical staff and recovery designer. Its only canonical actions are:

`OBSERVE → REASON → PROPOSE → RECOVER → DESIGN → ASK`

The LLM may interpret documents, propose Industry DNA, discover missing evidence, formulate hypotheses/counter-theses, propose Economic Twins, propose scanners, propose Bridge logic, reconcile definitions, design proxies/ranges/alternate methods, and design a missing reusable capability.

The LLM may not commit a compiled assumption, perform authoritative valuation arithmetic, authorize a stage, issue an Intrinsic Freeze Token, or mutate the canonical system without explicit user authorization and the ordinary validation/promotion gates.

### Assumption Compiler
The Compiler is the border between reasoning and numbers. It validates provenance, source placement, unit, period, scope, conflicts, transforms, economic paths and current-price leakage, then deterministically commits only valid assumptions.

### Deterministic Engines
Evaluators, Beta, WACC, Warranted PER, SOTP/rNPV/NAV and calibrated probability engines calculate from compiled inputs only. They do not interpret news or fetch market price.

### Audit / Freeze
Audit is independent from the Control Plane. The Control Plane cannot waive a blocking audit. Only an audit-passed run with complete doctrine coverage and a replay-valid Evidence→Assumption→Scenario→Valuation hash chain can receive an Intrinsic Freeze Token. Street/target/current-price loaders require that token.

## 2. No-silent-skip doctrine coverage

Every applicable module, scanner and gate must end with one explicit terminal trace:

- `PASS`
- `WARNING`
- `BLOCKED`
- `SKIPPED_NOT_APPLICABLE` with reason
- `NOT_IMPLEMENTED` with reason
- `RECOVERED`
- `AWAITING_USER_DECISION`

Blank/implicit omission is forbidden.

This is the machine-readable answer to: "Did we actually inspect every relevant piece of doctrine for this company?"

## 3. None / blocked-candidate recovery

`None`, missing data, a failed valuation method or an unsupported model is not an immediate `VALUATION_BLOCKED`.

Canonical recovery order:

1. `RESEARCH` — find direct primary/independent evidence.
2. `RECONCILE` — resolve definition, period, scope or accounting-basis mismatch.
3. `DERIVE` — compute through a valid economic/accounting identity.
4. `PROXY` — propose a measurable proxy with representativeness caveats.
5. `ALTERNATE_MODEL` — use another already-canonical method allowed by Industry DNA.
6. `BOUNDED_ESTIMATE` — propose an auditable range rather than a fabricated point.
7. `PARTIAL_VALUATION` — value supported segments and label the remainder `UNVALUED_NOT_ZERO`.
8. `CAPABILITY_DESIGN` — identify a genuine reusable system gap.
9. `VALUATION_BLOCKED` — only after the above paths are exhausted or a non-recoverable safety/audit gate fails.

Reasoned estimates and proxies are not Evidence. They remain proposals until the Compiler can validate their inputs and registered transform.

## 4. Capability-gap lifecycle

A `CAPABILITY_GAP` is different from an `EVIDENCE_GAP`.

The LLM may produce a Build Proposal only if all are true:

- existing capability was genuinely exhausted;
- the missing capability is material to the current analysis;
- it is reusable beyond the single case;
- an explicit input/output contract can be designed.

The Build Proposal must state inputs, outputs, affected components, failure modes and validation plan. The LLM then asks the user whether to build it.

User approval authorizes implementation work only. It does not automatically make the new capability canonical.

`USER_APPROVED_BUILD → prototype → unit tests → regression → adversarial/Red Team → shadow use where applicable → explicit canonical promotion`

If the user declines, the system records the unresolved capability and continues with partial/unvalued treatment where valid. It never pretends the missing capability exists.

## 5. Partial valuation

`METHOD FAIL != VALUATION FAIL`.

If a material segment cannot be valued but other segments can be independently valued, the report may emit `PARTIAL_INTRINSIC`:

- quantified segment subtotal only;
- coverage by revenue/EBITDA/other relevant denominator where known;
- unvalued segments as `UNVALUED_NOT_ZERO`;
- no claim that the quantified subtotal is the full fair value;
- no whole-company Street/current-price gap comparison against that partial subtotal.

A blocking dependency shared by all segments still blocks the entire valuation.

## 6. Intrinsic Freeze Token

The token binds:

- run ID;
- frozen Evidence Ledger snapshot hash;
- compiled-assumption-set hash;
- valuation-output hash;
- audit hash;
- industry-knowledge snapshot hash;
- source-watch snapshot hash.

Before Freeze, Audit independently replays the Evidence Ledger snapshot, every compiled Evidence-input hash, the Compiled Assumption Set hash, Bound Scenario Set hash and valuation hash. A same-run Ledger or semantic-assumption mutation therefore blocks Freeze rather than being hidden behind a previously computed digest.

Street and target-equity market access must not be available without a valid token for the same run.

A Street-discovered fact may create a verification request, but cannot mutate that frozen run. Verified new evidence starts a new run.

## 7. Separation of roles

- Knowledge authority and Signal Class remain separate.
- Mandatory scanners come from deterministic Industry DNA/module contracts.
- Optional scanner activation is a typed Module Plan field and is not inferred from generic active research-unit IDs.
- The LLM may propose reinforcement scanners for unknown-unknowns.
- Scanner proposals cannot bypass source-placement or Evidence→Bridge requirements.
- Control Plane cannot calculate.
- LLM cannot commit.
- Compiler cannot invent evidence.
- Evaluator cannot interpret sources.
- Audit cannot be overridden.

## 8. Executable orchestration boundary

`src/valuation_engine/orchestrator.py` is the generic execution shell for `PRIMARY_SHADOW` and `LIVE_PRIMARY`. `src/valuation_engine/live_runtime.py::run_prism()` is the canonical `LIVE_PRIMARY` assembly over that same 33-stage Control Plane; there is no second live orchestrator and no automatic fallback to shadow or legacy execution.

The orchestrator:

- loads or accepts the canonical V05 stage order;
- loads the same registry's five-gate reporting partition, emits one compact summary only when a gate completes or terminates blocked, and records reporter-delivery failures without allowing them to mutate valuation state;
- records an explicit terminal trace for every attempted stage;
- fails closed when a required stage adapter is absent;
- records unsupported optional capability as `NOT_IMPLEMENTED`, never as a silent success;
- treats the run context as append-only so a later stage cannot silently rewrite an upstream output;
- issues the Intrinsic Freeze Token only from audit-passed, doctrine-covered and hash-bound data;
- refuses all post-freeze stages until that token exists and validates for the same run.

`LEGACY_REGRESSION` remains isolated in `workflow.py`; it is not routed through the canonical Control Plane. `PRIMARY_SHADOW` remains a regression/integration mode. `LIVE_PRIMARY` uses the same shell with typed source/provider contracts and fails closed for any required provider or exact evaluator that is unavailable for the selected route.

A missing live adapter or source is a visible capability state, not permission to fall back to the old keyword router, a generic DCF, fabricated evidence, or a market-anchored estimate.

Routine operator output is therefore five major-gate summaries rather than 33 progress lines. The full stage trace remains the authoritative audit appendix. The verified final result report also records the editorial delivery policy: 6–8 pages for the decision-facing body, 3–4 pages for the audit appendix and 12 pages maximum combined.
