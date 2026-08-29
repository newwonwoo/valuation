# Binary-Event Probability Route

`src/valuation_engine/binary_event_probability.py`

## The gap this closes

`probability_engine_v3` computes scenario probabilities from hierarchical
Bayesian event posteriors and a copula Monte Carlo over scenario rules. It was
complete and tested — and unreachable.

- `run_probability_engine_v3` had **no caller anywhere in `src/`**. Only tests
  invoked it.
- It is the **only** caller of `simulate_scenario_posterior` and
  `build_dynamic_hierarchical_posterior`, so both were stranded with it.
- Its output type, `ProbabilityEngineV3Result`, was not one of the snapshot
  contracts the SCENARIO_BUILD calibration socket accepts, so even a caller
  could not have delivered a probability into a run.

The continuous financial-path route (Route B) had all three: a caller, a sealed
snapshot type, and an issuance bridge to the canonical `CalibrationCertificate`.
The binary-event route (Route A) had none. This module supplies them, using the
same shape, so both routes enter the runtime through one socket.

```
binding + event Evidence -> run_probability_engine_v3 -> sealed snapshot -> .certificate() -> runtime socket
```

## What the binding declares

`BinaryEventCalibrationBinding` carries cohort identity (`cohort_key`,
`forecast_class`, `horizon`, `method_version`, `mapping_version`), the scenario
set, and the simulation controls (`credible_level`, draw counts, `seed`).

It deliberately does **not** carry the events. A binary-event cohort is fitted on
resolved outcomes supplied as Evidence-derived `ProbabilityEventInput`s, not read
from a frozen driver artifact the way the continuous route reads one. The
provider assembles the events; the binding names the cohort they belong to.

`build_binary_event_probability_snapshot` refuses a rule set that does not cover
exactly the bound scenarios, so a scenario silently dropped from the rules cannot
reach the runtime as a renormalised distribution over a smaller space.

## Normalisation and the credible interval

The engine normalises point probabilities across the scenario rules but reports
credible intervals as simulated. When the rules partition the event space the two
agree. When they do not, a normalised point can fall outside its own interval —
a real modelling defect, not a rounding artifact.

The snapshot records it as an integrity finding and seals as `DEGRADED` rather
than clamping the number. A `DEGRADED` snapshot reaches SCENARIO_BUILD as a
monitoring artifact, refuses to issue a certificate, and leaves scenario
probabilities descriptive.

A `DATA_BLOCKED` engine result raises `BinaryEventProbabilityBlocked` carrying
the engine's own violations, so the stage reports which event evidence failed
instead of a generic load failure.

## Socket changes

`probability_adapter` now names its accepted contracts once:

| Constant | Meaning |
|---|---|
| `CALIBRATION_SNAPSHOT_CONTRACTS` | Every snapshot type the SCENARIO_BUILD socket accepts — v1 single-cohort, v2 hierarchical, v3 binary-event, v3.2 continuous |
| `EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS` | The subset that binds as a frozen *external* probability source rather than authorising an Evidence-carried probability assumption path |
| `EXTERNAL_PROBABILITY_SNAPSHOT_KEYS` | The context key each external route publishes its snapshot under |

This is still an explicit whitelist, not a structural check: adding a probability
engine means adding its sealed snapshot type to one tuple. What changed is that
the socket and the compile/binding adapter no longer each carry their own copy of
the list, and neither one names a single route.

Each external route publishes under its own context key —
`continuous_probability_calibration_snapshot` and
`binary_event_probability_calibration_snapshot` — and the compile adapter
resolves whichever is present. No run's context grows a key because another route
exists, so the SK hynix run identifier and report hash are unchanged by this
work.

## Issuance

`BinaryEventProbabilityCalibrationSnapshot.certificate()` returns the canonical
`CalibrationCertificate` and calls `validate_for_weighting()` before returning
it, exactly as the v1, v2 and continuous snapshots do. A snapshot that is not
`CALIBRATED` raises `PermissionError` instead.

## Price isolation

`scripts/validate_probability_engine_v3_policy.py` now sweeps the bindings and
sealed snapshots of both routes for valuation-shaped field names, not only the
engine spec and result. A price, target, value or return field on the object that
carries probability into the runtime would re-open the circularity the route
exists to prevent.

## Tests

`tests/test_binary_event_probability.py` — sealing, certificate issuance, event
lineage, hash coverage, the socket accepting the snapshot and enforcing the
cohort, binding it as an external probability source into a scenario set, the
blocked and degraded paths, and a structural assertion that
`run_probability_engine_v3` now has a caller inside the package.
