# Valuation runtime detail contracts

Read only the sections relevant to the routed method or changed behavior. These are canonical details of the runtime skill, not optional alternatives. Repository-relative paths below resolve from the repository root.

## Separation rules

`Evidence → Hypothesis → Bridge → Assumption → Valuation → Frozen Intrinsic Value → Street/Market Comparison`

- Evidence is external observation; Hypothesis is causal reasoning, not fact.
- Every valuation assumption requires a Bridge; every Bridge must identify Evidence, economic path, kill condition and verification event.
- Deterministic code owns units, valuation math, beta/PER pooling, WACC arithmetic, probability weighting, duplicate-path detection and audit.
- LLM reasoning owns interpretation, Economic-Twin rationale, counter-theses and missing-evidence requests.
- Street reports and target-company price are comparison objects, not intrinsic inputs.

### Claim-to-Value Synchronization Gate

- A new title, headline or investment point that says a disclosure, policy or event changes value must map within the same new run through `Evidence → Hypothesis → Bridge → compiled Assumption → Valuation`.
- Show the previous intrinsic value, revised intrinsic value, changed assumption and value delta in the decision-facing report.
- If verified transmission does not change an intrinsic input, classify the claim `REFERENCE_ONLY`. It may appear only in context or risk, never as the title, headline conclusion or valued catalyst.
- Fail report generation when a `VALUED` headline has no active Evidence/Bridge/Assumption mapping or when its revised intrinsic value equals the prior run.
- Do not force a value change from policy intent alone. A policy claim becomes `VALUED` only when company exposure and a causal transmission path are separately evidenced; otherwise the correct outcome is `REFERENCE_ONLY` and no valuation-changing headline.

### Revision Request Efficiency Gate

- Convert each user correction into atomic clauses with the requested outcome, affected Unit Contract roots, read/write set and observable acceptance criteria. Do not call unrelated research or valuation units.
- Use the Unit Contract graph only to identify impact. Build a separate acyclic task graph for execution because the impact graph intentionally permits feedback cycles.
- Run tasks in parallel only when they have no dependency and disjoint write sets. Same-file or same-artifact writers require one owner or an explicit sequential dependency.
- For economic/model changes preserve `Evidence → Bridge → Compiler/model → report/artifacts → targeted validation → full regression → publish/merge`. Documentation and editorial-only checks follow AGENTS.md; this does not waive existing CI. Report-only copy/layout changes stop at the reporter unless they add a material valuation claim; a `VALUED` claim automatically requires the full model-to-report path.
- When a task fails or a clause changes, rerun only that task and its descendants. Reuse independent completed work only when the base revision and `plan_hash` still match; any scope expansion creates a new plan.
- Block merge for an unmapped clause, missing acceptance validator, dependency cycle, unordered write overlap, unplanned file change, stale plan result or generated artifact that predates its upstream model.
- Deliver a hash-bound immutable report filename containing the as-of date and reference intrinsic value. A mutable latest alias is automation-only and must never be the user-facing download link.

## Industry Knowledge & Signal Intelligence v0.5.2

- Freeze `industry_knowledge_snapshot_hash`, `source_watch_snapshot_hash`, taxonomy/module versions and routing evidence for every valuation run. Later reports cannot silently mutate an in-progress run.
- Decompose economically distinct segments before routing. `INDUSTRY_DNA_ROUTE` is multi-label and evidence-driven: one or more Economic Archetypes may apply, while Sector Adapters are defaults rather than authority. Keyword matching cannot finalize the route.
- Compile `MODULE_REQUIREMENT_PLAN` before collection: required evidence/KPIs, accounting normalization, Beta/PER twin features, scenario variables, funding checks, forbidden methods, terminal policy, double-count traps and kill conditions.
- Fail closed instead of generic-DCF fallback when no supported archetype can be established, a critical module input is missing/definition-conflicted, or a method is forbidden by a material archetype without segment split.
- Assign every source to a Knowledge Layer and enforce `config/knowledge_placement_policy.yaml` plus `config/workflow_source_injection_map.yaml`. Classification/metric/provenance standards define requirements; structural input-output data is a prior; primary/company evidence may reach a Bridge; broker/alternative data is discovery/corroboration; target Street/market remains post-freeze.
- Broker/IB material before freeze may supply value-chain maps, KPI definitions, mechanism candidates, investor debates, channel-check leads and underlying-data locations. Target-company broker revenue/EPS forecasts, target price, rating, target multiple and consensus are forbidden before freeze. Multiple brokers sharing one underlying data family do not count as independent corroboration.
- `SignalClass` is orthogonal to evidence authority. Permit, procurement, interconnection, patent, hiring, physical-production, customs/logistics, credit, clinical and remote-sensing signals require the same provenance/placement gates as other evidence.
- Split market inputs into: `financing_market_reference` (funding/WACC only through an economic Bridge), `positioning_market_signal` (monitoring/post-freeze; never mutates intrinsic), and `target_equity_market_reference` (post-freeze only).
- `NOT_OBSERVED != NO_EVENT`. Negative evidence requires complete/near-complete coverage, mandatory or near-mandatory reporting, elapsed reporting lag, healthy source and no known alternate channel. `SOURCE_FAILURE` is operational evidence only.
- Apply the Representativeness Gate before extrapolating spot/channel/alternative data: coverage share, selection bias, duplicate risk, granularity match, mapping stability and definition stability.
- Track project realization as evidence-backed states (`announced → applied → funded → permitted → awarded/contracted → under construction → commissioned/delivered → revenue`) rather than treating announced capacity as funded demand.
- Preserve `event_time`, `effective_as_of`, `published_at`, `first_seen_at`, `revised_at` and expected reporting lag. Historical/backtest analysis may not use a revision before its first-seen time.
- Dynamic Economic-Twin candidate generation may use product-text similarity, end-market mix, supply-chain topology, patent similarity, revenue model, capital intensity, customer concentration and contract structure. Final Beta/PER peers still require an auditable systematic-risk/fundamental-driver rationale.

## Non-negotiable gates

- Never use current price to select assumptions, probabilities, discount rates or multiples.
- Never load broker target prices/forecasts before `INTRINSIC_VALUE_FREEZE`.
- A Street-discovered claim cannot mutate the same frozen run. Verify it from primary/independently validated evidence and start a new run.
- Never convert policy price directly into company ASP without an economic bridge.
- Never promote company plans to realized evidence.
- Never double count the same evidence/economic path across operating value, option/SOTP, funding, WACC or PER premium.
- Never deduct gross CAPEX again when expansion economics already include the same investment through future EBITDA/funding gap/terminal debt.
- Mark uncalibrated probabilities `UNCALIBRATED`.
- An uncalibrated analyst prior may be displayed only as a clearly labelled 5% band; never bind it into scenario weights or expected value. Declared binary forecasts from an audit-passed live run must be captured append-only before resolution. Resolve them only with explicit first-seen primary Evidence and a directly verifiable source link; synthetic or post-hoc history is forbidden.
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

