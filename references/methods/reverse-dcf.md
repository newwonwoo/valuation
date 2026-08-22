# Reverse DCF / Market-Implied Expectations

> **Post-freeze only.** Reverse DCF is a market-comparison tool. Load target-company market price/EV only after `INTRINSIC_VALUE_FREEZE`; its result cannot mutate the same frozen intrinsic run.

The purpose is not to create another target price. It asks which operating assumptions the observed market value would require under the already-frozen economic model.

## Procedure

1. Freeze the intrinsic run and its industry-knowledge/source snapshots.
2. Load current target-equity market reference and build market EV/equity bridge.
3. Hold the frozen model structure constant.
4. Solve **one or a clearly identified small set of variables at a time** for the value implied by the market: revenue/volume, margin, growth duration, ROIC, reinvestment, utilization, project realization, or scenario probability.
5. Translate the solved value into physical/business units where possible.
6. Compare the market-implied requirement with primary evidence, module constraints and kill conditions.

## Interpretation

Report:
- market-implied requirement,
- frozen intrinsic assumption,
- evidence-supported range,
- what new evidence would validate or falsify the market requirement,
- any residual that cannot be explained by the selected variables.

Do not backsolve a convenient PER or probability and then feed it back into the intrinsic model. If reverse DCF reveals a plausible missing fact, verify it independently and start a **new** valuation run.
