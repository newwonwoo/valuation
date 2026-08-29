# Stage Capability Receipts

`src/valuation_engine/stage_capability.py` ·
`config/stage_capability_declarations.yaml` ·
`scripts/validate_stage_capability.py`

## The asymmetry this closes

PRISM already refuses a number with no receipt. An `EvidenceRecord` needs a
source ref, a source layer, a first-seen time and a hash before it can become a
valuation input. An execution family whose `canonical_refs` name a file that does
not exist fails `validate_module_registries.py`.

It did not apply that rule to its own capability claims.
`config/live_primary_readiness.yaml` carried one hand-written status word per
stage, and `live_readiness.load_live_primary_readiness` checked only three
things: that every canonical stage had a row, that the word was a valid enum
member, and that the reason string was non-empty. **A stage could claim
`LIVE_READY` with the reason "it works" and pass CI.**

That word also collapsed three different questions into one:

| Axis | Question | Who answered it before |
|---|---|---|
| **Contract** | Does a type or protocol exist for this stage's provider? | `RUNTIME_READY` |
| **Implementation** | Does *this repository* supply a company-neutral implementation of it? | `LIVE_READY` |
| **Cold execution** | Does the stage run for a company with no hand-written module here? | Nobody |

`render_project_status.py` then counted `LIVE_READY + RUNTIME_READY` as ready and
`ADAPTER_REQUIRED + SHADOW_ONLY + CONDITIONAL_NOT_IMPLEMENTED` as gaps. Since no
stage carried any of those three words, the rollup printed **`Explicit runtime
gaps: 0`** while nine required provider slots had no implementation at all —
including `intelligence_officer`, whose own readiness reason said
*"model/provider remains injectable"*.

The claim was true at the level it was measured and false at the level it was
read.

## How a claim is earned now

Each stage declares symbols, not a status:

```yaml
RESEARCHER_A:
  provider_slot: intelligence_officer
  contract: valuation_engine.live_runtime:IntelligenceOfficer
  generic_implementation: null
  note: >-
    IntelligenceOfficer is a Callable type alias. No model call exists anywhere
    in the package, so hypotheses are hand-written per company.
```

Every non-null reference is imported. A reference naming a module that will not
import, or a symbol that is not there, is an **error** — not a pass. A reference
into a module on the `company_bound_modules` list resolves to *absent*, because
hand-written per-company code is the work this probe exists to make visible, not
a capability the engine has.

A declaration can therefore **understate** what exists — writing `null` where an
implementation is available — but it cannot **overstate** it.

Status is then derived:

| Derived | Contract | Implementation | Cold execution | Counts as ready |
|---|---|---|---|---|
| `COLD_PROVEN` | ✓ | ✓ | ran | ✓ |
| `IMPLEMENTED` | ✓ | ✓ | not proven | ✓ |
| `PROVIDER_REQUIRED` | ✓ | — | — | ✗ |
| `UNDECLARED` | — | — | — | ✗ |

## The cold-start probe

`probe_cold_start` asks the question a SaaS has to answer: *could this repository
value a company it has never seen?*

It is answered before any network call, because the first thing a cold start
needs is a full set of providers. Any slot in `REQUIRED_PROVIDER_SLOTS` with no
company-neutral implementation makes `LivePrimaryProviders.validate()` fail, the
run never starts, and every stage is honestly `UNREACHED`.

When every required slot *is* filled, the probe deliberately reports
`NOT_PROBED` rather than success. A provider existing is not a run completing;
only an executed cold run may set `PROVEN`. The gap closing turns this into a
demand for a real run instead of a free pass.

Today it reports:

```
cold start: BLOCKED — LivePrimaryProviders cannot be assembled for an unseen
company: no company-neutral implementation for bridge_analyst, freshness_loader,
industry_dna_router, industry_snapshot_loader, intelligence_officer,
red_team_officer, scanner_runners, segment_decomposer,
valuation_plan_inputs_loader
```

## What the gate refuses

`scripts/validate_stage_capability.py` fails when:

- a stage declares `LIVE_READY`, `RUNTIME_READY` or `PARTIAL_LIVE` while the
  probe derives `PROVIDER_REQUIRED` or `UNDECLARED`; or
- a stage declares `PROVIDER_REQUIRED` while its implementation symbol resolves —
  a stale pessimistic claim is also a wrong claim.

It runs in CI next to the other registry validators.

## Effect on the numbers

`PROVIDER_REQUIRED` was added to `LiveReadinessStatus` and counts as an
unresolved gap. Ten stages moved onto it, and `PROJECT_STATUS.md` changed from

```
- `LIVE_READY` or `RUNTIME_READY`: 29/33
- Explicit runtime gaps: 0
```

to

```
- `LIVE_READY` or `RUNTIME_READY`: 20/33
- Explicit runtime gaps: 10
- `PROVIDER_REQUIRED` (typed contract, no company-neutral implementation): 10/33
- Cold-start proven stages: 0/33 — cold start blocked; no company-neutral
  provider for `bridge_analyst`, … `valuation_plan_inputs_loader`
```

The engine did not get worse. The number stopped lying.

## The ten stages

| Stage | Empty provider slot |
|---|---|
| `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT` | `industry_snapshot_loader` |
| `SOURCE_FRESHNESS_PRECHECK` | `freshness_loader` |
| `SEGMENT_DECOMPOSITION` | `segment_decomposer` |
| `INDUSTRY_DNA_ROUTE` | `industry_dna_router` |
| `ROCKET_INSIGHT_SCAN` | `scanner_runners` |
| `UPSTREAM_FUNDING_SCAN` | `funding_scanner` (optional slot) |
| `RESEARCHER_A` | `intelligence_officer` |
| `BLIND_RED_TEAM_B` | `red_team_officer` |
| `EVIDENCE_TO_ASSUMPTION_BRIDGE` | `bridge_analyst` |
| `DETERMINISTIC_VALUATION` | `valuation_plan_inputs_loader` |

Closing any one of them is a symbol change in
`config/stage_capability_declarations.yaml` that the probe will verify, and the
status follows automatically.

## Tests

`tests/test_stage_capability.py` — every declared symbol imports; a missing
symbol, an unimportable module and a malformed reference are errors; a
company-bound module never satisfies an axis; the derived ladder; the cold-start
probe naming its missing slots and refusing to claim success when they are
filled.

`tests/test_live_readiness.py` — readiness and the capability probe must agree in
both directions, so the registry cannot drift back into optimism.
