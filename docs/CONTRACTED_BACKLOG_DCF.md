# Contracted-Backlog Driver DCF

Execution family `contracted_backlog_dcf` — `src/valuation_engine/backlog_evaluators.py`.

Binds `contracted_backlog/backlog_burn_dcf`. The pre-existing
`contracted_backlog/normalized_dcf` binding is unchanged and still resolves to
`explicit_fcff_dcf`; this family is an addition, not a replacement.

## Why a separate family

`explicit_fcff_dcf` accepts a finished FCFF path. For a contracted-backlog
business that discards the one structural fact that makes the archetype
forecastable: revenue is not a free assumption, it is drawn down from a stock of
signed orders that is replenished by new orders. Two constraints follow from the
identity and neither can be expressed when FCFF arrives pre-computed.

## Model

```
revenue(t)        = backlog_open(t) x burn_rate(t)
backlog_open(t+1) = backlog_open(t) + new_orders(t) - revenue(t)

FCFF(t) = revenue(t) x margin(t) x (1 - tax)
          + revenue(t) x depreciation_rate
          - revenue(t) x maintenance_capex_rate
          - max(0, revenue(t) - revenue(t-1)) x working_capital_rate

EV = SUM_t FCFF(t)/(1+w)^t + [FCFF(N)(1+g)/(w-g)]/(1+w)^N
```

Working capital is charged only on each year's revenue *increase*, so a one-time
ramp is not capitalised into the terminal value forever. `opening_revenue` is a
required input rather than an assumed zero, so the year-1 working-capital step is
evidenced.

## Enforced constraints

| Constraint | Rationale |
|---|---|
| `0 < burn_rate <= 1` | A year cannot recognise more revenue than the backlog standing at its start, so the order book can never be drawn negative. |
| `new_orders >= 0` | Orders are a gross inflow; cancellations belong in a superseding Evidence record, not a negative order. |
| final-year `book_to_bill >= floor` | A perpetual-growth tail asserts the final year's run rate continues forever. If the final year books fewer orders than it burns, the explicit period shrinks the backlog while the tail assumes growth. |
| `WACC > g`, `g/ROIC in [0,1]` | Shared terminal contract (`wacc.validate_terminal_consistency`). |
| final-year `FCFF > 0` | Gordon tail is undefined on a non-positive base. |
| `0 <= tax < 1`, non-negative cost rates, `-100% < margin <= 100%` | Input domain. |

`terminal_book_to_bill_floor` defaults to `1` and is declared per registration, so
relaxing it for a deliberately normalising final year is a recorded decision
rather than a silent one.

## Required assumptions

Per registration `assumption_prefix`:

- `opening_backlog`, `opening_revenue` — money
- `new_orders_year_{t}` — money, `t` in `1..forecast_years`
- `backlog_burn_rate_year_{t}`, `operating_margin_year_{t}` — ratio
- `operating_tax_rate`, `depreciation_rate_of_revenue`,
  `maintenance_capex_rate_of_revenue`, `incremental_working_capital_rate` — ratio
- `terminal_growth`, `terminal_roic` — ratio

Money inputs are converted through `actual_units.Measure`, so a mixed
`KRW_billion` / `KRW_million` snapshot converts rather than silently mis-scaling;
a cross-currency input still requires an explicit FX transform upstream.

## Downstream integration

The evaluator publishes `SegmentValuationDiagnostics` with
`execution_family = "contracted_backlog_dcf"`, so post-freeze reverse DCF and the
value-sensitivity guardrail operate on backlog segments with no additional
wiring. `backlog_path()` exposes the year-by-year roll-forward
(`BacklogYear`) for reporting and provider-side checks.

## Effect on evidence composition

Backlog, new orders and lead time are ordinarily `realized_or_filing` records.
Driving revenue from them means those filings become valuation inputs rather than
background context, which is visible in the `evidence_composition` guardrail as a
non-zero filing-cited share. Under `explicit_fcff_dcf` the same disclosures sit in
the ledger while the model consumes an analyst FCFF path instead.

## Provider wiring

```python
from valuation_engine.backlog_evaluators import (
    BacklogBurnRegistration,
    live_backlog_burn_registry_loader,
)

loader = live_backlog_burn_registry_loader(
    registrations=(
        BacklogBurnRegistration(
            archetype="contracted_backlog",
            method="backlog_burn_dcf",
            version="1",
            forecast_years=5,
            assumption_prefix="",
        ),
    ),
)
```

`base_loader` composes this family on top of another registry loader when a
company mixes archetypes across segments.

Because `contracted_backlog` now exposes more than one implemented segment
evaluator, `VALUATION_METHOD_INTENT` requires an explicit `SegmentMethodChoice`;
this was already true before this family existed.
