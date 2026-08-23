# Exact Finite-Life NPV Evaluators v1.0

Status: deterministic `PARTIAL_LIVE` evaluator family for assets whose economic life must be modeled explicitly rather than hidden inside a terminal value.

## 1. Exact registrations

The shared kernel may be registered only behind an exact `ModelKey(archetype, method, version)`. Initial supported method families are:

- `project_finance / project_npv`;
- `reserve_depletion / reserve_npv`;
- `hit_driven_content / cohort_npv`.

There is no `generic_npv` fallback.

## 2. Inputs

For an exact registration with prefix `project_` and final year `N`, the scenario must contain:

`project_cashflow_year_0 ... project_cashflow_year_N`

Every cash flow is a compiled money Measure with a Bridge/Evidence/Economic-Path trace. Year 0 is not discounted; years 1...N are discounted with same-run live WACC.

The evaluator does not fetch data, infer COD, estimate reserves, extrapolate title decay or create cash flows. Those operating/timing mechanics must already have passed Evidence → Hypothesis → Bridge → Compiler.

## 3. Why finite life matters

A project construction delay should move or reduce explicit future cash flow rather than be disguised by a permanent-growth assumption. A reserve asset must deplete unless replacement is separately evidenced. A title/cohort cash flow must decay according to its compiled cohort path.

Accordingly this evaluator has **no terminal value**.

## 4. Risk path

The runtime loader requires the same-run `LiveWACCStageResult`. Every segment valuation carries both:

- `wacc:<snapshot>:<segment>`;
- `beta:<snapshot>:<segment>`.

This makes Beta/WACC consumption visible to Generic Audit and Decision Impact just like the explicit FCFF DCF family.

## 5. SOTP boundary

The finite-life evaluator returns enterprise value. Ownership and explicit EV→Equity adjustments remain outside the evaluator and are applied once in SOTP. Project debt, NCI and parent claims must not be silently netted inside the cash-flow kernel.

The cash-flow path may be negative in early construction/development years and the resulting NPV may also be negative. A negative project value is not converted to zero unless a separate contractual limited-liability/abandonment-option evaluator explicitly supports that treatment.

## 6. Composition

`live_finite_npv_registry_loader` accepts an optional base runtime registry loader. This allows a mixed company to combine, for example:

- manufacturing `driver_dcf`;
- one separately owned project `project_npv`;
- exact normalized-multiple segments;

without creating a generic company-wide formula.

Duplicate exact ModelKeys remain invalid.

## 7. Remaining evaluator gaps

This family does not implement:

- probability-weighted clinical rNPV;
- NAV/appraisal value;
- financial-institution residual-income/PB-ROE/DDM;
- regulated-rate-base-specific mechanics;
- levered project equity cash flow or detailed debt draw/amortization waterfalls.

Those remain separate exact evaluator contracts.
