# Signal Intelligence Layer v1.1

Status: canonical v0.5.x signal-intelligence contract integrated with the Control Plane and Unit Contract Registry.

## 1. Why this exists

Industry reports describe the world, but many investable changes appear earlier in operational traces: procurement, permits, interconnection queues, hiring, patents, credit trading, customs, rail/port activity, clinical registries, and facility-level remote sensing.

The system must not treat all of these as Evidence of the same kind. `SignalClass` is orthogonal to `KnowledgeLayer`: the former says *what economic process is being measured*; the latter says *what source/evidence role the observation can play*.

## 2. Canonical signal classes

- `PROJECT_REALIZATION`: project moves from announcement toward revenue.
- `PROCUREMENT_PIPELINE`: planned procurement -> bid -> award -> contract -> delivery.
- `REGULATORY_PROGRESS`: docket, permit, approval, compliance, enforcement.
- `PHYSICAL_ACTIVITY`: output, utilization, orders, shipments, inventory.
- `TRADE_LOGISTICS`: customs, rail, port, vessel, freight movement.
- `TECHNOLOGY_INNOVATION`: patents, citations, legal status, technical publications.
- `LABOR_CAPACITY`: vacancies, skills, hiring, staffing, wage pressure.
- `CREDIT_FINANCING`: bank standards, bond spreads, refinancing availability, collateral conditions.
- `OWNERSHIP_BEHAVIOR`: insider transactions, fund holdings, capital return behavior.
- `MARKET_POSITIONING`: short interest, securities lending, options/flow positioning.
- `CLINICAL_REGULATORY`: clinical-registry changes, FDA approvals/recalls/adverse events.
- `REMOTE_SENSING`: satellite/site observations of construction or production activity.
- `SUPPLY_CHAIN_NETWORK`: product similarity, input-output topology, supplier/customer links.
- `CONSUMER_DEMAND`: spend, traffic, downloads, transaction panels.

## 3. Project-realization GateSet

Do not force all projects through one universal linear state machine. Land, financing, permits, offtake and grid access can be secured in different orders depending on jurisdiction and project structure.

The canonical representation is an independent `ProjectGateSet` with required gates selected for the project:

- `ANNOUNCEMENT`
- `LAND_CONTROL`
- `FINANCING`
- `PERMIT_APPLICATION`
- `PERMIT_APPROVAL`
- `OFFTAKE_CONTRACT`
- `GRID_UTILITIES`
- `CONSTRUCTION`
- `COMMISSIONING`
- `REVENUE`

Each verified gate requires its own evidence. `realization_maturity` is only the fraction of required gates verified; it is **not** an execution probability. Probability remains a separately calibrated object.

Legacy linear enums in `signal_intelligence.py` and `broker_research.py` exist only for regression/backward compatibility. Adapters translate each legacy stage to the single canonical gate it actually proves; a later legacy stage does not automatically prove every earlier gate.

## 4. Latency contract

Every signal must distinguish:

- `event_time`: when the economic event occurred.
- `effective_as_of`: period the observation measures.
- `published_at`: when the source made it public.
- `first_seen_at`: when PRISM first observed it.
- `revised_at`: when the source changed the observation.
- `expected_reporting_lag_days`: normal publication lag.

Backtests and 'what was knowable then' analysis use `first_seen_at`, never a later revised timestamp. Revision history is immutable.

## 5. Negative-evidence gate

Absence of a record is evidence only when all are true:

1. source coverage is known to include the relevant entity/event;
2. reporting is mandatory or near-complete;
3. expected lag has elapsed;
4. the endpoint/source is healthy;
5. there is no known alternate filing channel.

Otherwise record `NOT_OBSERVED`, not `NO_EVENT`.

## 6. Representativeness gate

Before a signal changes a mechanism or assumption, record:

- coverage share of the economic activity;
- selection bias / panel bias;
- duplicate risk;
- granularity mismatch;
- lead/lag relationship;
- historical mapping stability;
- whether the metric changed definition.

Alternative data normally creates a `VERIFICATION_REQUEST`; it does not cross the Evidence-to-Assumption bridge by itself.

## 7. Market-data role split

`target equity price` is not the same thing as all market data.

### Financing market data -- pre-freeze permitted with a Bridge
Examples: sovereign risk-free curve, target bond yield/spread, loan pricing, FX, commodity prices where economically relevant. These may inform WACC, funding and operating assumptions without using the target equity price.

### Target-equity valuation reference -- post-freeze only
Current share price, market capitalization, target-company consensus target, target PER/PBR, rating and target-price-derived assumptions remain quarantined until `INTRINSIC_VALUE_FREEZE`.

### Positioning market signals -- monitoring/post-freeze
Short interest, securities lending, options positioning and investor flows do not modify intrinsic value in the same run. They are positioning/catalyst/market-confirmation objects.

## 8. Dynamic Economic Peer Graph

Static industry codes are only a prior. Economic Twins should use a dynamic graph built from:

- product/business-description text;
- segment/end-market mix;
- supply-chain topology;
- patent/technology similarity;
- revenue model and capital intensity;
- customer concentration and contract structure.

Text-based product similarity is academically grounded by Hoberg-Phillips/TNIC. PRISM uses the idea as a peer-discovery input, not as a copied proprietary dataset.

The graph supplies candidate peers to Hierarchical Beta and Hierarchical Warranted PER. Final peers still require auditable risk-driver checks.

## 9. Project-realization stack

For infrastructure/capacity projects, combine independent evidence layers rather than assuming a fixed order:

`land | financing | permit | offtake | interconnection/utilities | construction | physical confirmation | commissioning`

The Control Plane selects which gates are required for that project. A project may be large on paper while key gates remain unresolved. Announcement size alone never becomes project-value quantity or revenue.

If an execution probability is used, it must come from an explicitly calibrated probability process; GateSet completeness by itself is not a probability model.

## 10. Signal-to-valuation rules

- Procurement plan: demand candidate only.
- Award/contract: stronger funded-demand evidence, subject to cancellation/termination terms.
- Permit grant: execution/timing evidence, not revenue itself.
- Interconnection agreement: Time-to-Power evidence, not commercial operation.
- Patent count: innovation activity only; no direct revenue premium.
- Patent legal status/citations/family breadth: technology-option verification input.
- Hiring: capacity/intention signal; duplicate and outsourcing bias checked.
- Bond spread/TRACE: marginal cost-of-debt/credit-state evidence when the security maps to the target.
- Short interest/options: post-freeze positioning only.
- Insider Form 4: behavior signal, not intrinsic value evidence.
- Remote sensing: physical verification request unless calibrated against ground truth.

## 11. Cross-signal corroboration

Corroboration is strongest when source families and measurement processes differ, e.g.:

`procurement award + permit + satellite construction + company contract liability`

Four broker reports citing one underlying dataset count as one underlying evidence family. Likewise, multiple websites mirroring the same government database do not create independent confirmation.

## 12. Promotion gate

A new signal family becomes a canonical industry-module input only after:

1. source contract and license reviewed;
2. latency and revision behavior measured;
3. representativeness tested;
4. at least one historical lead-lag mapping established;
5. false-positive/false-negative modes documented;
6. target-company price leakage audit passed;
7. regression fixture added.
