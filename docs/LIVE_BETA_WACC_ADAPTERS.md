# LIVE Hierarchical Beta & WACC Adapters v1.0

Status: typed `PARTIAL_LIVE` contracts for `HIERARCHICAL_BETA_ESTIMATION` and `WACC_VALIDATION`.

## 1. Purpose

The deterministic Beta and WACC mathematics already exist in `risk.py` and `wacc.py`. The live adapter layer governs how real peer, market, credit and target-capital-structure inputs are allowed to reach those engines.

`Evidence-backed Economic-Twin universe → L1→L4 asset-beta pooling → one target relevering structure → currency-consistent Ke/Kd/WACC → terminal consistency`

The adapters do not fetch data themselves. Callers inject jurisdiction/source-specific loaders behind typed contracts so live transport, entitlements and credentials remain outside deterministic valuation code.

## 2. Live Hierarchical Beta contract

### Peer observations

Every comparable requires:

- stable peer ID;
- levered Beta;
- debt, equity and tax rate used for unlevering;
- benchmark/index;
- return frequency;
- estimation window;
- as-of date;
- source reference;
- estimation method;
- optional Beta standard error.

All peers in one run must use one normalized benchmark, frequency and estimation window. Inconsistent conventions fail closed rather than being averaged.

### Four-level universe

The universe must be exactly:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`

Each level requires a selection rationale and active Evidence IDs. L4 additionally requires explicit systematic-risk features such as end-market cyclicality, backlog duration, operating leverage, capital intensity, concentration, pricing power or qualification structure.

A peer may not be repeated across hierarchy levels because repeated inclusion would count the same market observation multiple times. The live adapter converts the four typed levels into the existing sequential partial-pooling engine.

### Target relevering

After asset Beta is fixed, the result is relevered once using one typed target capital structure. The live contract forbids target-current-market-cap backsolving before Intrinsic Freeze.

Allowed target-structure bases include:

- normalized peer market-value structure;
- an evidenced management target;
- a regulatory target;
- an evidenced long-run policy;
- a compiled scenario assumption.

The structure carries weights, tax rate, method, as-of date, source references and rationale.

## 3. Live WACC contract

WACC consumes the `LiveBetaStageResult`; the WACC loader cannot supply or override Beta.

Typed live inputs include:

- cash-flow currency;
- currency-matched risk-free observation;
- market-level ERP;
- exposure-adjusted country-risk premium and lambda when material;
- marginal/current pre-tax Cost of Debt;
- the same target capital structure and tax rate used in Beta relevering;
- optional evidenced additional risk premium;
- optional terminal growth and terminal ROIC;
- funding/credit Evidence IDs for downstream audit.

### Same-structure invariant

Beta relevering and WACC weighting must use identical equity/debt weights, tax rate and target-structure method. A mismatch blocks the stage.

### Currency invariant

Risk-free, ERP, country risk and marginal debt observations must match the modeled cash-flow currency. Nominal/real normalization remains an upstream source-loader responsibility and must be documented in methodology/source references.

### Additional risk premium

A positive additional premium requires an explicit non-generic basis and active Evidence IDs. A generic small-cap plug is not an allowed basis.

### Customer advances / funding

Upstream Funding may expose credit-improvement Evidence and `CustomerAdvanceCreditEvidence`. The live WACC adapter records whether all second-order credit conditions are satisfied, but **does not mechanically reduce WACC**.

Any actual WACC change must appear in independently observed/rebuilt marginal debt cost, capital structure, Beta or another separately evidenced input. This preserves the FCFF-first treatment and Double-Count Gate from V04.

## 4. Target-market isolation

The pre-freeze Beta/WACC adapters reject contexts containing target-company:

- current market price;
- target market capitalization;
- target price;
- consensus target/multiple;
- target-company Street reference.

Peer market observations used for systematic-risk estimation are permitted under their separate calibration/source contract; they cannot be used to reverse-engineer the target's intrinsic assumptions.

## 5. Outputs and hashes

### Beta output

- hierarchical asset-Beta estimate and posterior variance;
- target levered Beta;
- peer IDs and source refs;
- selection Evidence IDs;
- target-structure contract;
- deterministic Beta snapshot hash.

### WACC output

- Cost of Equity;
- after-tax Cost of Debt;
- WACC;
- optional terminal-consistency result;
- funding-credit candidate trace;
- source refs;
- deterministic WACC snapshot hash bound to the Beta snapshot.

These outputs feed deterministic valuation, Warranted PER and Audit; they are not LLM-authored assumptions.

## 6. Readiness boundary

The two stages are `PARTIAL_LIVE`:

- the typed contracts, validation, deterministic computation and fail-closed behavior are implemented;
- universal real-time peer-return, benchmark, ERP, sovereign-curve and company-credit providers are not embedded in the repository;
- Economic-Twin selection remains Evidence-backed judgment and must be supplied by a live loader/process;
- source-specific fixtures and calibration depth still vary by jurisdiction and company.

Moving either stage to `LIVE_READY` requires reusable live providers, freshness/version behavior, fixtures and historical validation across the intended coverage universe.
