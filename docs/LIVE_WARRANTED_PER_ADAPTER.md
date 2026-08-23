# LIVE Hierarchical Warranted PER Adapter v1.0

Status: typed `PARTIAL_LIVE` contract for `HIERARCHICAL_WARRANTED_PER`.

## 1. Purpose

The deterministic PER mathematics already exist in `per.py`. The live adapter governs which compiled operating assumptions, WACC output, expansion Evidence and peer residuals may reach the three-layer Warranted PER engine.

`Compiled Assumption Set + live Cost of Equity + DCF fingerprint + committed expansion Evidence + peer fundamental residual hierarchy → Core / Expansion / Market-Realization PER`

The adapter does not accept target-company Street EPS, target PER, target price or current price before Intrinsic Freeze.

## 2. Applicability

PER is applicable only when the target has positive, economically normalized forward EPS and the routed Module Requirement Plan permits Warranted PER.

If PER is not suitable, the stage returns `SKIPPED_NOT_APPLICABLE` with a reason. It does not invent a denominator or substitute a sales multiple under the PER label.

## 3. Core Fundamental PER

Core PER reads only from `CompiledAssumptionSet`:

- normalized forward EPS;
- explicit growth path;
- FCFE/EPS conversion path;
- terminal growth;
- terminal ROE;
- margin path;
- reinvestment path.

Cost of Equity comes from `LiveWACCStageResult`; a PER loader cannot override it.

When Core DCF is used, the adapter requires an `EconomicAssumptionFingerprint` and validates exact consistency in:

- growth rates;
- margin path;
- reinvestment path;
- growth duration.

A mismatch blocks the stage. Core PER cannot silently extend a growth path or normalize margins differently from DCF.

## 4. Expansion-Adjusted Fundamental PER

Expansion PER is optional and separate from Core.

It requires:

- a separate compiled assumption-key contract;
- active Evidence IDs proving committed or pre-invested expansion;
- an explicit rationale.

A plan, aspiration or broker forecast without committed/pre-invested Evidence cannot activate Expansion PER. The adapter does not average Core and Expansion outputs.

## 5. Market-Realization PER

Market-Realization PER uses peer **residual** premiums:

`ln(peer market forward PER / peer fundamental warranted PER)`

The residual hierarchy must be exactly:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`.

Each level requires selection rationale and active Evidence IDs. L4 requires explicit fundamental features such as growth duration, ROIC/ROE, reinvestment, FCF conversion, visibility, cyclicality, leverage or dilution.

Rules:

- the target company cannot enter its own peer pool;
- a peer cannot appear in multiple hierarchy levels;
- peer observations must use one normalized as-of date;
- market and fundamental peer PER values require separate source/model references;
- raw peer PER is not averaged;
- target-company market price or target multiple is never used to tune the residual.

Market-Realization PER is reported as a separate layer. It does not replace Core Fundamental PER.

## 6. Pre-freeze isolation

The adapter rejects contexts containing target-company:

- current market price or market capitalization;
- target price;
- consensus target or target multiple;
- target-company consensus EPS;
- target-company Street reference.

Peer market multiples are permitted only as a calibration input to residual pooling under their typed source contract.

## 7. Outputs

The stage emits:

- Core Fundamental PER;
- optional Expansion-Adjusted Fundamental PER;
- optional Market-Realization PER;
- Core and Expansion economic fingerprints;
- peer-selection and expansion Evidence IDs;
- source references;
- deterministic snapshot hash bound to the Compiled Assumption Set and live WACC snapshot.

The outputs feed cross-method audit and final reporting. They do not mutate the compiled assumptions or frozen intrinsic run.

## 8. Readiness boundary

The stage is `PARTIAL_LIVE`:

- typed compiled-assumption, WACC, DCF-consistency, expansion-Evidence and residual-pooling contracts are implemented;
- universal normalized-EPS/accounting adapters and live peer residual data providers are not embedded for every jurisdiction/industry;
- peer Economic-Twin selection remains Evidence-backed judgment;
- some routed businesses correctly remain `NOT_APPLICABLE` for PER.

Promotion to `LIVE_READY` requires reusable EPS-normalization and peer-residual providers with freshness, entitlement, calibration and regression coverage across the intended universe.
