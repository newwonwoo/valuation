# PRISM Production Probability History

This runbook operates the delayed-label history used by production probability
calibration. It does **not** manufacture a calibrated cohort. It records a
forecast before resolution, later records the first-seen primary-source outcome,
and preserves both events in a single-writer hash chain.

## Security and chronology contract

- The history path must be absolute and live on persistent storage.
- Mutation requires POSIX `flock`; one lock file serializes writers.
- The journal and lock file are owner-private (`0600`).
- Every JSONL row has a sequence number, previous-event hash and event hash.
- The writer, not the operator, sets `first_seen_at` to the actual append time.
- A forecast entered late remains first-seen late. It cannot appear in an earlier
  replay merely because its `issued_at` was backdated.
- A forecast revision must use `--supersedes-id` and preserve the original event
  and resolution contract. Resolved or already-superseded revisions cannot be
  rewritten.
- Binary outcomes (`occurred` / `not_occurred`) require primary outcome Evidence
  IDs. Outcome records are immutable once appended.
- Export writes a materialized `ProbabilityCalibrationLedger` payload atomically
  and can never overwrite the append-only journal.

## 1. Initialize

```bash
prism-probability-history init \
  --history /persistent/private/prism-probability/history.jsonl
```

Initialization is idempotent. Existing history is fully validated before it is
reported as initialized.

## 2. Append a pre-resolution forecast

```bash
prism-probability-history append-forecast \
  --history /persistent/private/prism-probability/history.jsonl \
  --forecast-id F-2026-000001-MARGIN-01 \
  --event-key EVT:000001:FY2026:margin-pressure \
  --hypothesis-id H-MARGIN-PRESSURE \
  --company-id 000001 \
  --forecast-class kr-listed-margin-pressure \
  --horizon 12m \
  --event-definition 'FY2026 operating margin falls below 8 percent' \
  --issued-at 2026-09-01T00:00:00+00:00 \
  --evaluation-deadline 2027-03-31 \
  --probability 0.40 \
  --displayed-band '30-50%' \
  --evidence-snapshot-hash '<64-char Evidence snapshot SHA-256>' \
  --model-version probability-v3 \
  --resolution-rule 'Use the first annual filing after FY2026 close' \
  --resolution-source-policy 'REALIZED_OR_FILING primary Evidence only'
```

The response returns the event hash, journal hash, head hash and cohort counts.
Do not edit those fields into the journal by hand.

A later forecast revision uses a new `--forecast-id` and adds:

```bash
--supersedes-id F-2026-000001-MARGIN-01
```

The writer refuses revisions that change the company, hypothesis, event
definition, deadline or resolution contract.

## 3. Resolve from first-seen primary Evidence

```bash
prism-probability-history append-outcome \
  --history /persistent/private/prism-probability/history.jsonl \
  --forecast-id F-2026-000001-MARGIN-01 \
  --observed-at 2027-03-20T00:00:00+00:00 \
  --outcome occurred \
  --evidence-id EVIDENCE:DART:000001:FY2026:MARGIN \
  --resolver-id production-resolver \
  --rationale 'The first-seen FY2026 annual filing reports margin below the declared threshold.'
```

Valid states are `occurred`, `not_occurred`, `censored` and `ambiguous`.
`censored` and `ambiguous` remain non-binary outcomes and do not silently become
successes or failures.

## 4. Validate and inspect

```bash
prism-probability-history validate \
  --history /persistent/private/prism-probability/history.jsonl

prism-probability-history summary \
  --history /persistent/private/prism-probability/history.jsonl
```

Validation replays the full journal through the engine's existing
`ProbabilityCalibrationLedger`. A sequence gap, hash mismatch, changed payload,
invalid supersession, duplicate forecast or rewritten outcome fails closed.

## 5. Export an engine ledger snapshot

```bash
prism-probability-history export \
  --history /persistent/private/prism-probability/history.jsonl \
  --output /persistent/private/prism-probability/ledger.json
```

The export is a normal `ProbabilityCalibrationLedger.to_payload()` document and
can be loaded with `ProbabilityCalibrationLedger.from_payload()`. Its response
binds the export to the source journal SHA-256 and head-event hash.

## Completion boundary

This tool completes the **production capture and delayed resolution mechanism**.
It does not close `CAL-PRODUCTION-COHORT-003` by itself. That milestone remains
blocked until declared cohorts naturally accumulate enough independent,
pre-resolution forecasts and later first-seen resolved outcomes to satisfy the
existing calibration policy thresholds. Synthetic, post-hoc or research-only
history must never be promoted into the production journal.
