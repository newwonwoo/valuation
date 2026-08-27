---
name: insight-valuation-analysis
description: Run evidence-first equity research and deterministic valuation when the user says "분석시작 기업명", asks to update a thesis, validate assumptions, compare intrinsic value with market/Street references, inspect funding constraints, or inspect kill conditions. Use for Korean and global equities; not for simple price/news lookup without valuation intent.
---

# Insight Valuation Analysis v0.5.2

Read `AGENTS.md`, `01_Rocketesla_Insight_Valuation_Framework.md`, `docs/V04_ROCKETSLA_EXTENSION.md`, `docs/V05_WORKFLOW_CONTRACT.md`, `docs/README_V05_OPERATOR_INDEX.md`, `docs/SIGNAL_INTELLIGENCE_LAYER_V1.md`, `docs/GENERIC_ENGINE_DESIGN.md`, and `docs/LIVE_VALIDATION_AND_CALIBRATION.md` before changing model architecture.

The current CLI remains a v0.3-alpha offline vertical slice unless a newer live adapter is explicitly implemented. Never present fixture evidence or contract-only modules as current research.

## Required workflow

Execute in this order; unavailable live modules must be labelled `CONTRACT_ONLY` or `NOT_IMPLEMENTED`.

1. `COMPANY_RESOLUTION`
2. `LOAD_COMPANY_STATE`
3. `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT`
4. `SOURCE_FRESHNESS_PRECHECK`
5. `SEGMENT_DECOMPOSITION`
6. `INDUSTRY_DNA_ROUTE`
7. `MODULE_REQUIREMENT_PLAN`
8. `PRIMARY_EVIDENCE_COLLECTION`
9. `EVIDENCE_LEDGER`
10. `ROCKET_INSIGHT_SCAN`
11. `UPSTREAM_FUNDING_SCAN` when external finance is material
12. `RESEARCHER_A`
13. `BLIND_RED_TEAM_B`
14. up to three targeted `RESEARCH_LOOP` rounds
15. `EVIDENCE_TO_ASSUMPTION_BRIDGE`
16. `SCENARIO_BUILD`
17. `HIERARCHICAL_BETA_ESTIMATION` when peer beta is used
18. `WACC_VALIDATION`
19. `DETERMINISTIC_VALUATION`
20. `HIERARCHICAL_WARRANTED_PER` when PER is allowed
21. `DCF_PER_ASSUMPTION_CONSISTENCY_GATE`
22. `CROSS_METHOD_DOUBLE_COUNT_AUDIT`
23. `PROBABILITY_DISTRIBUTION_ANALYSIS` when likelihood/forecast inputs are declared or calibrated
24. `AUDIT_GATE`
25. `INTRINSIC_VALUE_FREEZE`
26. `STREET_REFERENCE_LOAD`
27. `STREET_GAP_ANALYZER` including consensus-lag reverse check
28. `MARKET_PRICE_LOAD`
29. `MARKET_COMPARE`
30. `THESIS_DELTA` / `SAVE_STATE` / `FINAL_REPORT`

If a blocking issue remains after round three or a blocking audit fails, return `VALUATION BLOCKED`. Do not output fair value or load Street/current-price data.

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
- Preserve the sequence `Evidence → Bridge → Compiler/model → report/artifacts → targeted validation → full regression → publish/merge`. Report-only copy/layout changes stop at the reporter unless they add a material valuation claim; a `VALUED` claim automatically requires the full model-to-report path.
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

## Verification

Before reporting/publishing model changes:

```bash
pytest -q
valuation-engine examples/oci/company.yaml
valuation-engine "분석시작 OCI홀딩스" --state-root <temporary-path>
```

Confirm: OCI regression ±1 KRW unless intentionally changed; market/Street isolation; probability sum; units; EV-to-equity; CAPEX/economic-path/funding double counts; beta small/noisy L4 shrinkage; WACC currency/target-structure/customer-advance/terminal gates; positive normalized EPS; Expansion PER evidence gate; residual-not-raw-PER pooling; DCF–PER consistency; blocked-run suppression; and byte-identical root/canonical Skills.

## Methodology status

The v0.4 finance-calibration architecture remains **academically grounded engineering synthesis**. v0.5.2 adds evidence-governed Industry Knowledge, Broker Research, Freshness/Revision Watch and Signal Intelligence orchestration; these are repository-specific operating contracts, not a claim that every causal mechanism is academically established. Established components include Blume/Vasicek beta shrinkage, non-synchronous-trading corrections, unlever/relever bottom-up beta practice, standard WACC consistency principles, and fundamental/forward-earnings multiple literature. The L1→L4 Economic-Twin taxonomy, customer-advance transmission gate, three-layer Hierarchical Warranted PER, residual pooling orchestration and cross-method fail-closed gates are repository-specific synthesis. See `docs/V04_ROCKETSLA_EXTENSION.md` for references, practical value and limitations.

## Report contract

Lead with conclusion, thesis delta, frozen industry-knowledge/source-freshness status, known vs underappreciated evidence, strongest Red Team objection, funded-demand constraints when material, scenario worldviews, Core/Expected/Verified Bull values, Beta/WACC/PER audit summary, frozen intrinsic value, Street Gap/Consensus Lag or reverse-check, current-market comparison, kill conditions, next verification events, data quality and limitations. Clearly label fixture, stale, uncalibrated, contract-only or missing evidence.

The Control Plane owns five major progress gates: Evidence and Routing; Insight and Challenge; Assumptions, Method and Risk; Valuation, Audit and Freeze; and Post-Freeze Comparison and Persistence. At completion or blocking termination of each gate, emit only status, decisive result, residual risk and next action. Preserve all 33 stage identities/statuses in the compact verified audit appendix and exact rationales/output keys in the immutable trace artifact instead of streaming them as routine progress. Target 3–4 pages for the decision-facing body, 1–2 pages for the audit appendix and 6 pages maximum combined. Use body text of at least 13pt, primary headings of at least 22pt and section headings of at least 18pt; dense wide tables are forbidden. Never shorten by hiding a material blocker, uncertainty or integrity record.

Source provenance is mandatory and exempt from omission. Every active Evidence claim and each reported identity, Beta, WACC, PER, Street and market reference must map to a directly clickable HTTP(S) original-source link. Group claims that share one document into one compact source entry with covered metrics and effective dates; when a source covers many records, show the count and representative metrics while retaining every exact Evidence ID/metric/date mapping in the immutable Evidence Ledger. A missing, non-HTTP or credential-bearing source reference blocks a `LIVE_PRIMARY` final report.

The user-facing final report is Korean by default. Keep technical IDs and original-source names intact, but render headings, conclusions, explanations and gate summaries in Korean. The decision-facing body must use a Korean brokerage-report order—투자 요약 → 가치평가 → 핵심 가정과 위험 → 증권사·시장 비교 → 원문 출처—and appear before the audit appendix. The first-screen `투자 요약` is the primary investment report, not a preface: it must show 투자판단, 현재가, 기준 내재가치, 가치평가 범위, a one-sentence conclusion, no more than three investment points, and the conditions that strengthen, weaken, or unlock action. Raw stage IDs, enums and hashes belong only in collapsed technical detail or immutable machine artifacts; visible stage names and statuses use Korean labels. Put LLM-authored environment-change/company-strength reasoning in a separate `인공지능 인사이트` section capped at 1,000 characters; deterministic assumptions, calculations, Audit and Freeze results stay outside it. Save the complete typed insight in `context_strength_linkages.json`.

Every successful final report includes two deterministic SVG cards generated from the same immutable run payload: `회사 강점·투자 결론·가치평가` and `가치평가 가정·위험·출처`. Count both cards inside the 3–4 page main-body target, not in addition to the six-page cap. The cards must preserve direct source access. When probability calibration or a governed entry rule is unavailable, show scenario values and current price but explicitly withhold a specific buy price.
