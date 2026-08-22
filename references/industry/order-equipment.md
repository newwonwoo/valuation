# Order / Backlog Equipment — Operator Supplement

> Canonical route is usually `contracted_backlog + capacity_manufacturing`, but the exact route is evidence-driven. This file does not override `config/archetype_module_registry.yaml` or `config/sector_adapter_registry.yaml`.

## Do not equate backlog with one-year revenue

Revenue recognition, delivery/installation milestones, lead time, cancellations, customer acceptance and slot availability determine backlog conversion. Short-cycle equipment can convert rapidly; transformers, turbines, defense/aerospace and other long-cycle equipment can carry multi-year backlog.

Required evidence includes orders, backlog, backlog age, cancellation terms, revenue-recognition policy, customer advances/contract liabilities, lead time, effective capacity/slots, utilization and customer concentration.

## Customer advances / contract liabilities

`customer advances ÷ backlog` can be a useful **screening indicator of funding/commitment**, but fixed cutoffs are not calibrated probabilities and must not mechanically select a scenario.

Valuation treatment follows `docs/V04_ROCKETSLA_EXTENSION.md` §3 and `src/valuation_engine/wacc.py`:

`Customer Advances ↑ → NWC need ↓ → external funding need ↓ → FCFF / invested-capital economics improve` **first**.

A lower WACC is a separate second-order claim and requires recurring/structural advances plus independent evidence of improved leverage, coverage, borrowing cost/spread, liquidity or refinancing risk. The same advance benefit cannot be fully capitalized in both FCFF/ROIC and WACC without distinct economic paths.

Also inspect prepayment discounts, refund/cancellation rights, delay penalties, performance guarantees and fixed-price inflation exposure.

## Cycle / terminal treatment

Do not capitalize peak backlog, peak utilization or peak margin forever. Explicit forecasts may reflect the current cycle; convergence and terminal economics must normalize price/utilization/margin/reinvestment consistently. See `references/methods/cycle-normalization.md` and the DCF–PER consistency gate.

## Additional checks

- backlog reference date may differ from financial-statement date,
- disclosed backlog may exclude parts/service/short-lead orders,
- customer CAPEX is not the same as the supplier's funded order,
- qualification/design-win evidence is not a purchase order,
- service/aftermarket revenue may have a different archetype from new equipment.
