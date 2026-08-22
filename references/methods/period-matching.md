# Period Matching & Denominator Discipline

> **Operator supplement.** This file does not override `SKILL.md`, `docs/V04_ROCKETSLA_EXTENSION.md`, `docs/V05_WORKFLOW_CONTRACT.md`, deterministic code, or module registries. If they conflict, the canonical contracts win.

Comparisons are invalid unless period, scope, accounting basis, and revision vintage are aligned first. This is a precondition for any ratio, growth, margin, working-capital, or ROIC interpretation.

## Required checks

1. **Flow vs flow:** compare revenue, profit, cash flow and expense to the same-length period, normally YoY or LTM-to-LTM. Do not compare a half-year flow directly with a full-year flow.
2. **Stock vs flow:** use average balance or another explicitly justified exposure measure when a point-in-time balance is divided by a period flow. This applies to receivable turns, inventory turns, invested capital and effective borrowing cost.
3. **Scope match:** consolidated vs separate, gross vs net, continuing vs discontinued operations, segment vs group, nameplate vs effective capacity must not be mixed.
4. **Growth capital vs operating capital:** do not interpret capital under construction, pre-ramp inventory or pre-revenue capacity as if it were mature operating capital without a Bridge.
5. **Definition match:** reconcile similarly named items in statements and notes. Contract liabilities, customer advances, deposits and deferred revenue are not interchangeable by label alone.
6. **Revision/vintage match:** preserve `effective_as_of`, `published_at`, `first_seen_at` and `revised_at`. A historical/backtest view may not use a revision before it was first observable.
7. **Seasonality check:** annualization is allowed only when seasonality is immaterial or explicitly normalized. Otherwise use LTM, seasonal comparables, or a period-specific model.

## Output contract

Before interpreting a ratio or change, record:
- numerator period/scope/definition,
- denominator period/scope/definition,
- annualization or averaging transform,
- source IDs and revision vintage,
- any unresolved mismatch.

A material unresolved mismatch blocks the affected claim or assumption rather than being averaged away.
