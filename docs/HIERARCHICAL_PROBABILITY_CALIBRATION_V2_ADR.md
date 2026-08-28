# ADR — Hierarchical probability calibration replaces leaf-only sample accumulation

Decision: ACCEPTED FOR DESIGN

## Decision

Do not require every industry/sub-industry leaf to independently accumulate the full production sample threshold before it can contribute to probability calibration.

Retain the strict root calibration gate for reusable event classes, then allow child nodes to inherit or partially pool from their nearest certified ancestor. Local specialization requires its own smaller breadth/OOS gate and may never be tuned against current market price or a target valuation.

## Rationale

The current `forecast_class|horizon` cohort contract is safe but scales poorly. Splitting ten economically meaningful child groups and requiring 200 resolved events per child creates roughly 2,000 leaf observations even when most statistical information is shared.

Economic-archetype routing already exists in the repository and is a better first specialization axis than sector labels. The hierarchy therefore follows reusable event class → economic archetype → industry family → optional sub-industry → current-company evidence.

## Non-decisions

- This ADR does not weaken first-seen or publication-time anti-leakage rules.
- It does not permit post-hoc historical forecasts.
- It does not use current price, Street target price, or valuation error as calibration outcomes.
- It does not calibrate company-specific Down/Core/Bull labels directly.
- It does not authorize production weighting until a hash-bound hierarchical certificate exists.

## Compatibility

Existing v1 ledger, dataset declaration, snapshot, certificate and scenario-binding tests remain valid. V2 is an additive layer that may consume v1 node snapshots.
