# Calibration-Certified rNPV Evaluator v1.0

Status: exact `PARTIAL_LIVE` evaluator for a single calibrated binary success event within a probabilistic pipeline asset.

## 1. Scope

The evaluator is registered only through an exact:

`probabilistic_pipeline / rnpv / <version>`

It does not infer clinical phase, invent historical success tables or fall back to a generic biotech formula.

This first evaluator supports one binary event probability. Multi-stage clinical event trees remain a separate future evaluator.

## 2. Cash-flow split

For each year 0...N the scenario provides two compiled money paths:

- `unconditional_cashflow_year_t`: costs/cash flows paid regardless of eventual success at that point in the modeled plan;
- `contingent_cashflow_year_t`: cash flow that occurs only if the calibrated success event occurs.

The value kernel is:

`Σ [ unconditional_t + p(success) × contingent_t ] / (1 + discount_rate)^t`

Development cost is therefore not mechanically multiplied by the same commercialization success probability.

## 3. Probability authority

The success probability must satisfy **both** conditions:

1. the `CompiledAssumption` carries `CalibrationStatus.CALIBRATED`;
2. the runtime has a hash-bound `CalibrationCertificate` for the exact registration cohort.

The enum alone is not sufficient.

The certificate snapshot hash and cohort key enter `SegmentValuation.economic_path_ids`, making calibration history part of the value trace and Decision Impact graph.

Unresolved rNPV probabilities must remain strictly between 0 and 1. Once an event is resolved, the asset should be rerouted/recompiled rather than pretending a resolved event is still an uncertain rNPV input.

## 4. Discount rate and double-risk auditability

The first implementation uses same-run live WACC for the unlevered expected cash-flow path. The evaluator also carries both Beta and WACC snapshot paths.

This does **not** assert that probability adjustment and WACC can never overlap economically. Asset-specific success risk belongs in the calibrated probability; systematic financing/business risk belongs in the discount-rate path. Because both paths are explicit, Red Team/Audit/Decision Impact can detect a future case where the same risk is embedded in both.

Alternative risk-adjusted discounting requires another versioned exact evaluator or policy; callers cannot inject an arbitrary rate directly.

## 5. Market isolation

The runtime loader rejects target current price, market capitalization, target price, target multiple, consensus target and Street reference fields before intrinsic freeze.

A market-implied probability cannot become an rNPV input in the same intrinsic run.

## 6. SOTP boundary

The evaluator returns enterprise value. Ownership, cash/debt, NCI, licensing attribution and parent claims remain explicit SOTP/EV→Equity adjustments.

Gross asset rNPV must not be presented as company equity value when economic ownership is less than 100%.

## 7. Remaining pipeline work

The following require distinct exact contracts:

- multi-stage Safety → PK/PD → target engagement → patient PoC → dose-response → confirmatory event trees;
- indication-specific or geography-specific branching;
- milestone/royalty/licensing waterfall economics;
- patent expiry and launch-curve-specific commercial modules when not already compiled into the contingent cash-flow path.
