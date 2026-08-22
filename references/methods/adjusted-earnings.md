# Adjusted Earnings / EPS Normalization

> **Operator supplement to the EPS Quality Gate.** Canonical PER logic is `docs/V04_ROCKETSLA_EXTENSION.md` + `src/valuation_engine/per.py`. If normalized EPS cannot be supported, PER is blocked rather than repaired with an arbitrary adjustment.

Reported net income is not automatically an economic forward-EPS denominator. Normalize only with evidence and keep every adjustment auditable.

## Review categories

- asset disposals and discontinuations,
- material non-recurring impairments or reversals,
- abnormal tax items, tax credits, deferred-tax recognition and loss carryforwards,
- derivative/FX marks that are genuinely non-recurring versus recurring hedging economics,
- acquisition accounting and purchase-price-amortization effects,
- restructuring charges that are truly exceptional versus recurring operating costs,
- stock-based compensation economics and dilution,
- subsidies/credits whose continuation is not supported,
- non-controlling interests and attributable earnings,
- capitalized development or other accounting choices that alter comparability.

## Rules

1. **Do not automatically remove a line item because it is non-cash.** Recurring impairments, hedging costs, restructuring or stock compensation can be economic costs.
2. **Normalize tax with current evidence.** Do not hardcode a single statutory rate. Use the applicable marginal/blended rate and explicitly model structural credits or jurisdiction mix when material.
3. **Match numerator and share count.** EPS must use earnings attributable to the relevant common shareholders and a diluted share count consistent with the dilution analysis.
4. **Peak-cycle earnings are not “normalized” merely because they are reported.** Cycle normalization is a separate judgment and must reconcile with the DCF worldview.
5. **Do not borrow Street EPS before `INTRINSIC_VALUE_FREEZE`.** Street estimates remain post-freeze comparison objects unless independently rebuilt from primary evidence in a new run.

## Required record

For each adjustment record `reported_value`, `adjustment`, `normalized_value`, source IDs, rationale, recurrence assessment, confidence, and `economic_path_id` where the item can affect more than one valuation channel.
