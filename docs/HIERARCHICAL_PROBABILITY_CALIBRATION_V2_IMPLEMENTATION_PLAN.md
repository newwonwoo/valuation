# Hierarchical Probability Calibration v2 — implementation plan

## Wave A — contracts

Add typed hierarchy identity and registry without changing live weighting behavior.

Planned modules:
- `src/valuation_engine/calibration_hierarchy.py`
- `config/calibration_hierarchy_registry.yaml`
- validator + focused tests

Core types:
- `CalibrationHierarchyLevel`
- `CalibrationHierarchyNode`
- `CalibrationHierarchyPath`
- `CalibrationEventClassification`
- `CalibrationHierarchyRegistry`

## Wave B — partial pooling math

Add deterministic binary-event shrinkage around existing node snapshots.

Core outputs:
- parent probability
- local empirical probability
- parent strength
- local resolved count
- effective sample size
- posterior probability
- posterior shift
- node state
- OOS delta vs parent

No scenario probability is generated in this wave.

## Wave C — certificate/runtime

Add:
- `HierarchicalCalibrationSnapshot`
- `HierarchicalCalibrationCertificate`
- typed certificate protocol accepted by probability adapter/scenario binding
- ancestor snapshot/dataset hashes in run hash chain

## Wave D — factor-to-scenario assembly

Add deterministic `ScenarioEventGraph`.

Rules:
- no direct historical Down/Core/Bull frequency
- no naive multiplication for correlated factors
- dependence contract required
- use conservative bounds when dependence is unknown

## Wave E — historical data migration

Reuse existing DART/SEC raw facts and metadata through one normalized fact layer.

The existing semiconductor collection becomes the first pilot dataset for reusable event factors, not a one-off SK hynix probability table.

## Merge discipline

Each wave is independently reviewable and must preserve:
- v1 calibration regressions
- provenance and first-seen boundaries
- chronological OOS behavior
- certificate/hash-chain integrity
- PM project-status synchronization
