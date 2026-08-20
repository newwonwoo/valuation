# RocketSLA v0.4 Method Extension

Status: methodology + pure-function contract. Live data adapters remain separate.

## 1. Upstream Funding & Constraint Ladder

Start from `Funded Demand`, not desired demand alone.

`product/project → buyer cash flow → financing channel → collateral value → LTV/advance rate/haircut/covenant/guarantee/tenor → credit spread → maturity-matched benchmark/swap rate → market plumbing → policy/liquidity backstop`

Rules:
- Ask who prices the layer immediately above the current one.
- Financing condition is benchmark + spread + collateral/lending terms + tenor/refinancing availability, not one Treasury yield.
- Collateral rental/resale/residual value can lead purchasing capacity.
- `Policy Intent ≠ Transmission Effect`.
- Record `confirmed fact → first-order mechanism → second-order transmission → investment hypothesis` with declining confidence where evidence weakens.
- Use Bridge Test: Mechanical Link, Materiality, Lead-Lag, Market Confirmation.
- Prevent the same funding shock from being counted in WACC, quantity and distress probability without separate economic paths.

## 2. Hierarchical Bottom-up Beta Engine

Canonical hierarchy:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`

Pre-processing and audit:
- Estimate comparable betas on a consistent index/horizon/frequency where possible.
- Preserve estimation uncertainty/standard error.
- Blume/Vasicek shrinkage is permitted and auditable.
- Use Scholes-Williams/Dimson-style non-synchronous-trading correction when material and data permit.
- Unlever every comparable to asset beta before pooling.
- Do not average levels or use fixed 10/20/30/40 weights.
- Sequential Bayesian/precision-weighted partial pooling: small/noisy L4 samples shrink toward upper priors; precise L4 samples may move the posterior materially.
- Relever only after the business-risk estimate is fixed, using target capital structure.

Economic Twins are selected by systematic-risk drivers: product, end demand, geography, backlog/order structure, operating leverage, capital intensity, pricing power, concentration and cyclicality.

### Academic/practitioner basis
Established components include market-model beta, Blume mean reversion, Vasicek Bayesian beta, Scholes-Williams/Dimson corrections and unlever/relever bottom-up beta practice. Classic references: Blume (1971, Journal of Finance); Vasicek (1973, Journal of Finance); Scholes & Williams (1977, Journal of Financial Economics); Dimson (1979, Journal of Financial Economics).

The four-level taxonomy plus sequential Economic-Twin pooling is **RocketSLA engineering synthesis**, not a published named academic model.

### Practical value / limitations
Value: reduces one-raw-beta dependence and tiny-peer overfitting; forces peer rationale to be auditable. Limitations: twin selection remains judgmental; beta is not a complete measure of business risk; results are sensitive to horizon/index/regime change and target leverage.

## 3. WACC Validation Engine

Canonical flow:

`currency-consistent Rf + Hierarchical Beta × ERP + exposure-adjusted country risk + evidenced extra premia = Cost of Equity`

`Marginal Cost of Debt × (1-tax) + market-value Target Capital Structure → WACC`

Rules:
- Risk-free rate must match cash-flow currency and nominal/real convention.
- ERP is a market-level risk price, not a company-specific balancing plug.
- Country risk is exposure-adjusted; headquarters alone does not justify the full premium.
- Generic small-cap premium is forbidden unless liquidity/refinancing/other risk is evidenced.
- Cost of Debt is marginal/current, not merely a legacy coupon.
- Use market-value target D/E; the same target structure must be used in beta relevering and WACC weights.
- WACC is a state variable: it falls only when business/credit risk is evidenced to have fallen, not because time passed.
- Terminal checks: `WACC > g`, currency consistency, nominal/real consistency, and `reinvestment_rate = g / terminal_ROIC`.

### Customer advances / contract liabilities
First-order effect:

`Customer Advances ↑ → NWC Need ↓ → External Funding Need ↓ → FCFF ↑ → Invested Capital ↓ → Incremental ROIC ↑`

When possible compute:

`Customer-Funded Growth Ratio = growth-order-related advances / (growth CAPEX + incremental NWC need)`

Do not cut WACC directly. A second-order WACC reduction requires recurring/structural advances plus actual credit improvement: better Net Debt/EBITDA and interest coverage, slower external borrowing growth, lower borrowing rate/credit spread, and lower liquidity/refinancing risk. Check prepayment discounts, delay penalties, performance/refund obligations, fixed-price inflation exposure and cancellation rights.

Direct FCFF benefit and indirect WACC benefit require a Double-Count Audit.

### Academic/practitioner basis and limits
The components are standard corporate-finance valuation logic: currency-consistent discounting, market-value capital structure, marginal financing cost and terminal growth/return/reinvestment consistency. The explicit customer-funded-growth transmission gate is RocketSLA synthesis. WACC remains an estimate, not an observable truth.

## 4. Hierarchical Warranted PER Engine v1.0

Purpose: determine what P/E the economics warrant, not which peer multiple fits a desired price.

### EPS Quality Gate
PER requires positive, economically normalized forward EPS. Adjust or separately flag one-offs, asset sales, abnormal tax/FX, aggressive capitalization, stock-based compensation economics, dilution, peak-cycle earnings, acquisition accounting and one-off subsidies. Non-positive or non-normalizable EPS blocks PER.

### Three output layers
1. `Core Fundamental PER` — same economics as Core DCF/operating model.
2. `Expansion-Adjusted Fundamental PER` — may extend growth duration only when committed/pre-invested capacity or equivalent evidence passes the gate.
3. `Market-Realization PER` — applies pooled residual market premium/discount from peer fundamentals.

Never average the three layers.

### Fundamental economics
Treat P/E as compressed equity-cash-flow valuation driven by normalized forward EPS, Cost of Equity, growth, growth duration, ROE/ROIC, incremental returns, required reinvestment, FCFE/EPS conversion and terminal growth. Growth alone does not justify a premium.

### DCF–PER Assumption Consistency Gate
Core PER must use the same growth path, margin normalization, EPS economics, reinvestment/capital intensity, growth duration and risk assumptions as Core DCF. It is forbidden to normalize growth/margins in DCF while silently extending 25–30% EPS growth for years in PER. Only the Expansion layer may extend duration, and only with evidence.

### Hierarchical residual pooling
For each peer estimate its own fundamental warranted PER, then compute:

`Residual_i = ln(Market Forward PER_i / Fundamental PER_i)`

Pool the **residual**, not raw peer P/E, through L1→L4. Small/noisy L4 residual samples shrink toward upper priors.

`Market-Realization PER = applicable Fundamental PER × exp(Pooled Residual Premium)`

PER Economic Twins emphasize growth rate/duration, ROE/ROIC, reinvestment, FCF conversion, margin stability, revenue visibility, cyclicality, capital intensity, balance-sheet risk, concentration, pricing power and dilution. They need not be identical to Beta twins.

### Academic/practitioner basis
Relevant foundations include Ohlson & Juettner-Nauroth (2005, Review of Accounting Studies), which links expected EPS/growth and required return to value, and Liu, Nissim & Thomas (2002, Journal of Accounting Research), which documents the usefulness of forward-earnings multiples. These works support fundamental/forward-multiple logic; they do not define the complete RocketSLA three-layer hierarchy.

### Practical value / limitations
Value: removes raw peer-P/E averaging, forces growth to be reconciled with reinvestment and returns, and makes DCF/PER disagreement diagnosable. Limitations: fundamental PER is sensitive to duration/Cost of Equity/terminal assumptions; accounting EPS quality can make P/E inappropriate; residual peer premia can break under regime change.

## 5. Beta ↔ WACC ↔ PER Cross-Method Double-Count Gate

Some qualities affect multiple channels: cyclicality, backlog visibility, leverage, customer concentration, contract duration and pricing power. Every material driver must carry an `economic_path_id`.

Audit whether the same evidence has been capitalized repeatedly through lower Beta/Cost of Equity, lower WACC, higher cash flow/probability and higher PER residual premium. Multiple channels are allowed only when the mechanisms are distinct and separately evidenced.

## 6. DCF ↔ PER reconciliation

Do not average DCF and PER values mechanically. If they differ materially, bridge EPS normalization, growth/duration, margin, reinvestment, incremental ROIC, FCFE/FCF conversion, Cost of Equity/WACC, terminal assumptions and residual market premium.

## 7. Street Gap Analyzer

Only after `INTRINSIC_VALUE_FREEZE`, load broker/consensus references. Decompose the gap into operating assumptions, financing, valuation policy, options and capital structure. A gap driven mainly by lower WACC/higher multiple is not automatically alpha. If Street reveals a possibly missed fact, verify it from primary/independent evidence and start a new intrinsic run; do not mutate the frozen run.

## 8. Correct description of methodology

Use: **academically grounded engineering synthesis**.

Do not claim that `4-Level Hierarchical Bottom-up Beta`, `Hierarchical Warranted PER Engine`, customer-advance WACC transmission, or the full cross-method gate are published academic models. Their components are grounded in established literature/practice; the orchestration, taxonomy, audit gates and fail-closed implementation are repository-specific methodology.
