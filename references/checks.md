# Screening Checks — Order/Backlog Equipment

> This is an operator checklist, not a universal scoring model. Other industries follow the v0.5.2 Industry DNA / module registries. Numeric bands below, where used, are screening heuristics rather than calibrated valuation probabilities.

## 1. Contract liabilities / customer advances

```
advance coverage = relevant customer advances or contract liabilities / comparable backlog
```

Interpret the ratio together with definition, scope, backlog age, cancellation/refund rights, lead time and revenue-recognition policy. Direction and contractual quality often matter more than a universal cutoff.

**Valuation sequence:** `docs/V04_ROCKETSLA_EXTENSION.md` §3 and `src/valuation_engine/wacc.py` are authoritative. Customer advances improve working-capital/external-funding/FCFF and invested-capital economics first. WACC may fall only after separate credit evidence confirms lower leverage/refinancing/liquidity risk. Audit the direct and indirect paths for double counting.

## 2. Receivable turns / collection days

Use period matching and average balances (`references/methods/period-matching.md`). Compare direction through time and against revenue growth, billing milestones and customer terms rather than treating a longer collection period as bad debt by itself.

## 3. Aging + allowance direction

Combine aging buckets, allowance roll-forward, write-offs/recoveries and customer concentration. Deteriorating age plus provisioning is stronger evidence than collection days alone; improvement plus allowance release can indicate normalization. Preserve alternative explanations until verified.

## 4. Debt / fixed-charge burden

Track net debt, gross debt, maturity wall, interest expense, interest coverage and **marginal/current borrowing cost**. Historical coupon or a single coverage threshold cannot replace the WACC/Funding validation engine.

Convertible/exchangeable securities require the contractual-path analysis in `references/methods/dilution.md`.

## 5. Customer concentration

Concentration is neither automatically good nor bad. Evaluate customer credit quality, advances/commitments, qualification/switching costs, pricing power, cancellation terms and whether a single failure can break the thesis.

Geographic revenue and the actual credit/contracting entity are separate concepts.

## 6. Guidance / plan changes

Track original guidance, revisions and realized delivery. A downgrade is material evidence, but the valuation response depends on which mechanism/assumption changed. Management guidance remains a plan, never realized evidence.
