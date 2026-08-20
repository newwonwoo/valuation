---
name: insight-valuation-analysis
description: Run evidence-first equity research and deterministic valuation when the user says "분석시작 기업명", asks to update a thesis, validate assumptions, compare intrinsic value with market/Street references, inspect funding constraints, or inspect kill conditions. Use for Korean and global equities; not for simple price/news lookup without valuation intent.
---

# Insight Valuation Analysis v0.4

Read `AGENTS.md`, `01_Rocketesla_Insight_Valuation_Framework.md`, `docs/V04_ROCKETSLA_EXTENSION.md`, `docs/GENERIC_ENGINE_DESIGN.md`, and `docs/LIVE_VALIDATION_AND_CALIBRATION.md` before changing model architecture.

The current CLI remains a v0.3-alpha offline vertical slice unless a newer live adapter is explicitly implemented. Never present fixture evidence or contract-only modules as current research.

## Required workflow

Execute in this order; unavailable live modules must be labelled `CONTRACT_ONLY` or `NOT_IMPLEMENTED`.

1. `COMPANY_RESOLUTION`
2. `LOAD_COMPANY_STATE`
3. `INDUSTRY_ROUTE`
4. `PRIMARY_EVIDENCE_COLLECTION`
5. `EVIDENCE_LEDGER`
6. `ROCKET_INSIGHT_SCAN`
7. `UPSTREAM_FUNDING_SCAN` when external finance is material
8. `RESEARCHER_A`
9. `BLIND_RED_TEAM_B`
10. up to three targeted `RESEARCH_LOOP` rounds
11. `EVIDENCE_TO_ASSUMPTION_BRIDGE`
12. `SCENARIO_BUILD`
13. `HIERARCHICAL_BETA_ESTIMATION` when peer beta is used
14. `WACC_VALIDATION`
15. `DETERMINISTIC_VALUATION`
16. `HIERARCHICAL_WARRANTED_PER` when PER is allowed
17. `DCF_PER_ASSUMPTION_CONSISTENCY_GATE`
18. `CROSS_METHOD_DOUBLE_COUNT_AUDIT`
19. `PROBABILITY_DISTRIBUTION_ANALYSIS` when calibrated
20. `AUDIT_GATE`
21. `INTRINSIC_VALUE_FREEZE`
22. `STREET_REFERENCE_LOAD`
23. `STREET_GAP_ANALYZER`
24. `CONSENSUS_LAG_REVERSE_CHECK`
25. `MARKET_PRICE_LOAD`
26. `MARKET_COMPARE`
27. `THESIS_DELTA`
28. `SAVE_STATE`
29. `FINAL_REPORT`

If a blocking issue remains after round three or a blocking audit fails, return `VALUATION BLOCKED`. Do not output fair value or load Street/current-price data.

## Separation rules

`Evidence → Hypothesis → Bridge → Assumption → Valuation → Frozen Intrinsic Value → Street/Market Comparison`

- Evidence is external observation; Hypothesis is causal reasoning, not fact.
- Every valuation assumption requires a Bridge; every Bridge must identify Evidence, economic path, kill condition and verification event.
- Deterministic code owns units, valuation math, beta/PER pooling, WACC arithmetic, probability weighting, duplicate-path detection and audit.
- LLM reasoning owns interpretation, Economic-Twin rationale, counter-theses and missing-evidence requests.
- Street reports and target-company price are comparison objects, not intrinsic inputs.

## Non-negotiable gates

- Never use current price to select assumptions, probabilities, discount rates or multiples.
- Never load broker target prices/forecasts before `INTRINSIC_VALUE_FREEZE`.
- A Street-discovered claim cannot mutate the same frozen run. Verify it from primary/independently validated evidence and start a new run.
- Never convert policy price directly into company ASP without an economic bridge.
- Never promote company plans to realized evidence.
- Never double count the same evidence/economic path across operating value, option/SOTP, funding, WACC or PER premium.
- Never deduct gross CAPEX again when expansion economics already include the same investment through future EBITDA/funding gap/terminal debt.
- Mark uncalibrated probabilities `UNCALIBRATED`.
- Red Team input excludes price, Street target, intrinsic value, market gap, position data and market/Street loader access.
- Blocked runs are saved but never promoted to current state.

## Upstream Funding & Constraint Ladder

When demand depends materially on external finance, analyze `Funded Demand`:

`product/project → buyer cash flow → financing channel → collateral value → lending terms (LTV/advance rate/haircut/covenant/guarantee/tenor) → credit spread → maturity-matched benchmark/swap rate → market plumbing → policy/liquidity backstop`

Rules:
- Ask who prices the layer immediately above the current one.
- Funding condition is benchmark + spread + collateral/lending terms + tenor/refinancing availability, not one Treasury yield.
- Collateral rental/resale/residual value can lead purchasing capacity.
- `Policy Intent ≠ Transmission Effect`.
- Preserve `confirmed fact → first-order mechanism → second-order transmission → investment hypothesis` and evidence confidence.
- Prefer upstream kill conditions when they lead downstream orders.

## Hierarchical Bottom-up Beta

For non-financial companies using peer beta:

`L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins`

- Estimate/normalize comparable betas consistently and preserve estimation uncertainty where available.
- Blume/Vasicek adjustment is allowed and auditable; apply non-synchronous-trading correction when material/data permit.
- Unlever every comparable before pooling.
- L4 Economic Twins are chosen by systematic-risk drivers, not labels: product, end demand, geography, backlog/order structure, operating leverage, capital intensity, pricing power, concentration and cyclicality.
- Fixed 10/20/30/40-style weights and simple level averages are forbidden.
- Use Bayesian/precision-weighted partial pooling; small/noisy L4 samples shrink to upper priors, precise L4 may move the posterior materially.
- Relever only after business-risk beta is fixed, using target capital structure.
- Financial institutions use sector-specific cost-of-equity methods rather than industrial D/E unlevering.

## WACC Validation Engine

- Risk-free rate must match cash-flow currency and nominal/real convention.
- ERP is market-level; do not use company-specific ERP as a plug.
- Country risk is exposure-adjusted, not headquarters-only.
- Generic small-cap premium is forbidden without explicit liquidity/refinancing/other risk evidence.
- Cost of Debt is marginal/current, not merely historical coupon.
- Use market-value Target Capital Structure; the same target D/E must be used in beta relevering and WACC weights.
- WACC is state-dependent: a lower future WACC requires evidence that business/credit risk actually declined, not simply passage of time.
- Terminal checks require `WACC > g`, currency and nominal/real consistency, and `reinvestment_rate = g / terminal_ROIC`.

### Customer Advances / Contract Liabilities

First reflect:

`Customer Advances ↑ → NWC Need ↓ → External Funding Need ↓ → FCFF ↑ → Invested Capital ↓ → Incremental ROIC ↑`

When possible calculate `Customer-Funded Growth Ratio = growth-order-related advances / (growth CAPEX + incremental NWC need)`.

Do not lower WACC merely because advances rose. A second-order WACC reduction requires recurring/structural advances plus verified credit improvement: better Net Debt/EBITDA and interest coverage, slower external borrowing growth, lower actual borrowing rate/credit spread, and lower liquidity/refinancing risk. Check prepayment discounts, delay penalties, performance/refund obligations, fixed-price inflation exposure and cancellation rights. Audit direct FCFF benefit versus indirect WACC benefit for double counting.

## Hierarchical Warranted PER Engine v1.0

PER is permitted only where the industry router and EPS quality allow it. Never start from current P/E, broker target P/E or raw peer average.

### EPS Quality Gate
Use positive, economically normalized forward EPS. Adjust/flag one-offs, asset sales, abnormal tax/FX, aggressive capitalization, stock-based compensation economics, dilution, peak-cycle earnings, acquisition accounting and one-off subsidies. Non-positive/non-normalizable EPS blocks PER.

### Three layers
1. `Core Fundamental PER` — identical economic worldview to Core DCF/operating model.
2. `Expansion-Adjusted Fundamental PER` — extends growth duration only with committed/pre-invested capacity or equivalent verified evidence.
3. `Market-Realization PER` — applies pooled residual market premium/discount from peer fundamentals.

Do not average these layers.

### DCF–PER Assumption Consistency Gate
Core PER must use the same growth path, margin normalization, EPS economics, reinvestment/capital intensity, growth duration and risk assumptions as Core DCF. Do not borrow Street EPS or silently extend high growth beyond the DCF horizon. Only Expansion-Adjusted PER may extend duration, and only after its evidence gate passes.

### Fundamental PER economics
Treat P/E as compressed equity-cash-flow valuation driven by normalized forward EPS, Cost of Equity, growth and duration, ROE/ROIC, incremental returns, required reinvestment, FCFE/EPS conversion and terminal growth. Growth alone never justifies a premium.

### Hierarchical residual pooling
For each peer estimate its own fundamental PER, then:

`Residual_i = ln(Market Forward PER_i / Fundamental PER_i)`

Pool the residual — not raw peer P/E — through L1→L4. Small/noisy L4 samples shrink toward upper priors.

`Market-Realization PER = applicable Fundamental PER × exp(Pooled Residual Premium)`

PER Economic Twins emphasize growth rate/duration, ROE/ROIC, reinvestment, cash conversion, margin stability, revenue visibility, cyclicality, capital intensity, balance-sheet risk, concentration, pricing power and dilution. They need not be identical to Beta twins.

## Cross-method double-count gate

Track material qualitative/risk drivers with `economic_path_id`. Do not capitalize the same cyclicality/visibility/leverage/concentration advantage independently through lower Beta, lower WACC, higher FCF/probability and higher PER residual without distinct mechanisms and evidence.

If DCF and PER differ materially, do not average them. Reconcile EPS normalization, growth/duration, margin, reinvestment, incremental ROIC, FCFE/FCF conversion, Cost of Equity/WACC, terminal assumptions and market residual premium.

## Street Gap Analyzer

Only after intrinsic freeze, load recent broker references. Record broker/date/source, target price/currency, method/base year, estimates and disclosed WACC/g/net debt/CAPEX/multiples. Decompose gap into operating, financing, valuation-policy, option and capital-structure drivers.

`Different from Street` is not alpha. A gap mainly driven by lower WACC/higher multiple is low-quality unless separately justified. `Consensus Lag` requires observable operating/policy/funding change plus stale/omitting Street estimates. Preserve unexplained residuals.

## Probability / Monte Carlo

Use 10k–100k simulations only when a calibrated stochastic implementation exists and preserve realistic correlations. Current price is never a distribution input. Otherwise report probabilities as `UNCALIBRATED`; do not fabricate Monte Carlo output.

## Verification

Before reporting/publishing model changes:

```bash
pytest -q
valuation-engine examples/oci/company.yaml
valuation-engine "분석시작 OCI홀딩스" --state-root <temporary-path>
```

Confirm: OCI regression ±1 KRW unless intentionally changed; market/Street isolation; probability sum; units; EV-to-equity; CAPEX/economic-path/funding double counts; beta small/noisy L4 shrinkage; WACC currency/target-structure/customer-advance/terminal gates; positive normalized EPS; Expansion PER evidence gate; residual-not-raw-PER pooling; DCF–PER consistency; blocked-run suppression; and byte-identical root/canonical Skills.

## Methodology status

The full v0.4 architecture is **academically grounded engineering synthesis**. Established components include Blume/Vasicek beta shrinkage, non-synchronous-trading corrections, unlever/relever bottom-up beta practice, standard WACC consistency principles, and fundamental/forward-earnings multiple literature. The L1→L4 Economic-Twin taxonomy, customer-advance transmission gate, three-layer Hierarchical Warranted PER, residual pooling orchestration and cross-method fail-closed gates are repository-specific synthesis. See `docs/V04_ROCKETSLA_EXTENSION.md` for references, practical value and limitations.

## Report contract

Lead with conclusion, thesis delta, known vs underappreciated evidence, strongest Red Team objection, funded-demand constraints when material, scenario worldviews, Core/Expected/Verified Bull values, Beta/WACC/PER audit summary, frozen intrinsic value, Street Gap/Consensus Lag or reverse-check, current-market comparison, kill conditions, next verification events, data quality and limitations. Clearly label fixture, stale, uncalibrated, contract-only or missing evidence.
