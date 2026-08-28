# Hierarchical Probability Calibration v2

Status: DESIGN

## 1. Problem

The current production calibration contract treats `forecast_class|horizon` as an isolated cohort. That is safe but data-hungry: if industry, sub-industry, business model, or event type is split into separate cohorts, every leaf needs its own large resolved history.

This design replaces leaf-only calibration with hierarchical partial pooling while preserving the existing no-look-ahead, append-only, first-seen, chronological OOS, hash-chain, and certificate gates.

## 2. Principle

Calibrate reusable economic risk events, not company-specific Down/Core/Bull labels.

A company scenario probability is assembled from calibrated factor probabilities and scenario logic after calibration. Historical data is collected once into a shared event ledger and reused across compatible hierarchy nodes.

Hierarchy:

1. GLOBAL_EVENT — reusable event class across companies
2. ECONOMIC_ARCHETYPE — economic DNA such as capacity_manufacturing or contracted_backlog
3. INDUSTRY_FAMILY — broad industry family
4. SUB_INDUSTRY — optional narrow specialization
5. COMPANY — current company evidence update, never an independent production-calibration cohort by default

Example for SK hynix:

`margin_compression|12m`
→ GLOBAL_EVENT
→ capacity_manufacturing
→ semiconductor
→ memory
→ SK hynix current evidence

## 3. What is calibrated

Reusable event classes should be binary or bounded outcomes with stable definitions. Initial canonical set:

- revenue_growth_miss
- margin_compression
- cash_conversion_miss
- capex_overrun
- working_capital_deterioration
- utilization_drop
- price_decline
- capacity_ramp_delay
- qualification_delay
- backlog_conversion_miss

Industry modules may register extra event classes, but company-specific Bull/Base/Down labels are not valid calibration event classes.

## 4. Shared data model

One immutable forecast/outcome event is stored once. Classification metadata attaches the same event to eligible hierarchy nodes.

Required metadata:

- event_key
- forecast_class
- horizon
- company_id
- economic_archetypes[]
- industry_family
- sub_industry (optional)
- issued_at
- first_seen_at
- evaluation_deadline
- probability
- outcome
- outcome first_seen_at
- source/evidence lineage

No child node receives a copied event. Node membership is an index/view over the same canonical event identity.

## 5. Partial pooling

Each node produces a posterior calibrated probability distribution. A child does not replace its parent until the child has enough information.

For an event probability `p`, use a beta-binomial empirical-Bayes shrinkage layer for binary event classes:

`child_p = (successes_child + strength_parent * parent_p) / (n_child + strength_parent)`

where `strength_parent` is versioned and derived only from training data/OOS optimization, never tuned to a target company's current valuation.

Interpretation:

- n_child = 0: child equals parent
- small n_child: child is mostly parent
- large n_child: child converges to its own empirical result

Probability forecasts with multiple displayed bands keep the existing reliability/Brier/ECE machinery. The hierarchical posterior adjusts the mapping/base-rate layer, not the immutable raw forecast probabilities.

## 6. Promotion model

Replace the single `200 events per leaf` rule with two separate gates.

### 6.1 Root/global calibration gate

The reusable event class must first have a strong global certificate.

Default starting thresholds:

- resolved events >= 200
- companies >= 20
- quarters >= 8
- OOS windows >= 2 and positive Brier Skill Score
- ECE <= 0.08
- ambiguous/censored <= 10%

These are equivalent in spirit to v1 and protect the common prior.

### 6.2 Child specialization gate

A child node can specialize without independently reaching 200 observations.

Default starting thresholds:

- node resolved events >= 30
- companies >= 5
- quarters >= 4
- effective sample size after shrinkage >= 50
- OOS evidence must not be worse than parent beyond tolerance
- posterior shift from parent is capped until evidence strength rises

A child below these thresholds still receives a valid inherited probability from its nearest certified ancestor, marked `INHERITED`, not `CALIBRATED_LOCAL`.

## 7. Node states

- UNCALIBRATED — no certified ancestor and insufficient own data
- INHERITED — probability authorized by a certified ancestor; no local specialization
- SHRUNK — local data exists and is partially pooled with parent
- CALIBRATED_LOCAL — local specialization passes its gate
- DEGRADED — previously local-calibrated node fails current OOS gate

Only GLOBAL/LOCAL certificates and explicitly hash-bound inherited/shrunk certificates may authorize numeric weighting. The certificate records every ancestor and weight used.

## 8. Certificate lineage

A hierarchical certificate must contain:

- event class + horizon
- target hierarchy path
- selected ancestor chain
- node dataset hashes
- node snapshot hashes
- shrinkage/mapping version
- parent strength parameters
- effective sample size
- final calibrated mapping/probability
- status: INHERITED / SHRUNK / CALIBRATED_LOCAL / DEGRADED

The final certificate hash enters the existing scenario hash chain. Any parent or child calibration update therefore invalidates a stale in-flight certificate.

## 9. Scenario assembly

Do not calibrate Down/Core/Bull frequency directly.

Scenario probabilities are generated after factor calibration from a deterministic scenario-event graph.

Example:

Down may depend on:
- price_decline
- utilization_drop
- margin_compression
- cash_conversion_miss

Bull may depend on:
- price persistence
- qualification success
- capacity tightness persistence

The scenario assembler must declare dependence assumptions. Naive multiplication of correlated factors is forbidden. Initial implementation should support:

1. mutually exclusive state table when the industry module can define one;
2. bounded copula/correlation matrix when enough history exists;
3. conservative Fréchet bounds when dependence is unknown.

## 10. Data collection architecture

Collect facts once, derive event outcomes many times.

Shared normalized fact lake:

- revenue
- operating income / margin
- CFO
- CAPEX
- FCF
- inventory
- receivables
- debt/net cash
- capacity/utilization where disclosed
- backlog/order metrics where disclosed

Industry-specific collectors add only genuinely industry-specific facts such as memory ASP, HBM mix, clinical milestones, reserves, RPO, or regulatory rate base.

The same DART/SEC filing should never be downloaded separately for every calibration module.

## 11. Leakage and contamination rules

Preserve all v1 protections:

- publication timestamp, not period-end date, is the knowledge cutoff
- `first_seen_at` is mandatory for backfilled revisions/outcomes
- future-observed Evidence is forbidden
- partially matured issuance quarters are excluded from OOS scoring
- revisions remain append-only and terminal-revision aware
- current market price and target price are never calibration outcomes
- target-company holdout contamination must be explicit and excluded

New v2 rule:

- hierarchy labels themselves are time-versioned. A company cannot be retrospectively reclassified using knowledge unavailable at the historical cutoff unless the mapping version explicitly permits a static taxonomy mapping.

## 12. Migration from v1

Do not delete the current `ProbabilityCalibrationLedger`, `CalibrationSnapshot`, or certificate gate.

Add a new layer around them:

- `CalibrationHierarchyNode`
- `CalibrationHierarchyRegistry`
- `HierarchicalCalibrationSnapshot`
- `HierarchicalCalibrationCertificate`
- `HierarchicalCalibrationResolver`

V1 single-cohort snapshots remain valid leaves/root snapshots and regression fixtures continue to work.

The existing `probability_calibration_load_adapter` should accept either a v1 certificate or a v2 hierarchical certificate through a typed protocol, not a loose union of dictionaries.

## 13. Initial hierarchy registry

Start with economic archetype, not GICS-style sector labels.

For semiconductor examples:

- GLOBAL_EVENT
  - capacity_manufacturing
    - semiconductor
      - memory
      - foundry
      - equipment
      - materials_parts
      - osat_test

A company may belong to multiple economic archetypes by segment. Calibration membership therefore follows the segment/event path used by the forecast, not a single company-wide sector tag.

## 14. Why this solves the data problem

Under v1, 10 sub-industries × 200 resolved events implies roughly 2,000 leaf observations before specialization.

Under v2, a common event class can obtain its root certificate from >=200 diverse observations once. A sub-industry can begin specializing around 30 local observations while inheriting the statistically validated parent prior. The marginal data requirement therefore grows with genuine differences, not with every taxonomy label.

## 15. Implementation waves

### Wave A — contracts and registry
- hierarchy node/path schema
- event-class registry
- time-versioned membership
- v2 policy config and validators
- no runtime probability changes yet

### Wave B — hierarchical snapshot math
- beta-binomial shrinkage for binary events
- effective sample size
- parent fallback
- node status transitions
- chronological OOS comparison against parent

### Wave C — certificate and runtime binding
- hierarchical certificate
- hash-chain integration
- probability adapter compatibility
- scenario binding authorization

### Wave D — scenario-event graph
- factor-to-scenario deterministic graph
- dependence declarations
- bounded aggregation
- no direct historical Down/Core/Bull frequency calibration

### Wave E — data-lake adapters
- normalize existing DART/SEC facts once
- derive reusable event outcomes
- attach hierarchy membership views
- migrate semiconductor historical work into the shared event dataset

## 16. Acceptance criteria

V2 is accepted only if all are true:

1. a child with zero data exactly inherits its certified ancestor;
2. a small child sample cannot swing probability materially without sufficient evidence;
3. a large coherent child sample can override the parent;
4. OOS deterioration prevents local promotion;
5. the same event identity is not double-counted through parent and child;
6. historical replay reproduces the same hierarchy path and probability;
7. v1 regression fixtures remain unchanged;
8. scenario weighting remains impossible without a hash-bound valid certificate;
9. no market/Street outcome is introduced pre-Freeze;
10. PM/status synchronization and existing provenance/hash-chain gates remain intact.
