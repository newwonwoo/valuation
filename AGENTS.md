# Project instructions for Codex

## Mission
Build a reusable valuation system where the user can type `분석시작 <company>` in ChatGPT/Codex and receive an evidence-first valuation without current-price or Street anchoring.

## Source of truth
Use these files in order and keep them consistent:

1. `.agents/skills/valuation-analysis/SKILL.md` — canonical runtime contract; root `SKILL.md` must remain byte-identical.
2. `01_Rocketesla_Insight_Valuation_Framework.md` — core research/valuation methodology.
3. `docs/V04_ROCKETSLA_EXTENSION.md` — v0.4 Upstream Funding, Beta, WACC, Hierarchical Warranted PER, Street Gap, academic/practitioner rationale and limitations.
4. `docs/GENERIC_ENGINE_DESIGN.md` — generic deterministic-engine implementation contract.
5. `docs/LIVE_VALIDATION_AND_CALIBRATION.md` — live validation/probability calibration.

If documents conflict, preserve the conflict and reconcile the newest intentional design decision across source-of-truth files. Do not silently choose whichever produces a convenient valuation.

## Non-negotiable architecture
1. LLM = evidence interpretation, causal reasoning, Economic-Twin rationale, hypothesis generation, confidence/probability rationale and missing-evidence requests.
2. Deterministic code = units, valuation math, beta/PER partial pooling, WACC arithmetic, probability weighting, DCF–PER consistency, Street arithmetic, duplicate-path detection and audit tests.
3. Market price is comparison-only; never solve intrinsic assumptions backwards from price.
4. Street targets/forecasts are inaccessible until `INTRINSIC_VALUE_FREEZE`; a Street-discovered claim cannot mutate the same frozen run.
5. Evidence, Hypothesis, Bridge, Assumption and Model Output are separate object types.
6. Industry routing happens before valuation-method selection.
7. Unverified futures are probability-weighted from evidence, not automatic 0/100.
8. CAPEX, funding shocks and qualitative advantages must not be double-counted across economic paths.
9. Policy Intent and downstream Transmission Effect are separate claims.
10. Every model change requires regression/audit tests.

## Valuation calibration gates
- **Beta:** estimate/normalize comparables consistently; unlever peers; L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins; Bayesian/precision partial pooling; then target relever. Fixed level weights are forbidden.
- **WACC:** currency-consistent risk-free rate, market-level ERP, exposure-adjusted country risk, marginal Cost of Debt, market-value target capital structure, terminal consistency. Customer advances improve FCFF/ROIC first; WACC falls only after separate credit-risk evidence.
- **PER:** positive normalized forward EPS; Core Fundamental / Expansion-Adjusted / Market-Realization PER kept separate; Core must share DCF economics; Expansion needs committed/pre-invested evidence; peer market residuals, not raw P/E, are hierarchically pooled.
- **Cross-method:** track material quality/risk drivers with `economic_path_id`; do not capitalize the same visibility/cyclicality/leverage benefit through Beta, WACC, FCF and PER premium without distinct mechanisms/evidence.

## Coding style
- Keep core calculations pure and deterministic.
- Prefer dataclasses/types and small functions.
- Avoid framework-heavy architecture until justified.
- No hidden constants in valuation formulas.
- Prefer contract-first pure functions before live adapters.
- Do not store paid broker-report text, secrets, personal positions or private state in this public repository.

## Validation
Before reporting/publishing model changes run the full pytest suite plus current-price/Street isolation, probability sum, units, scenario sensitivity, beta pooling, WACC validation, DCF–PER consistency and PER residual-pooling checks.

## Workflow gates
- Keep root and canonical Skill byte-identical.
- Do not load Street/current price before Audit PASS and intrinsic freeze.
- Do not emit intrinsic value from blocked runs; blocked runs cannot replace last-good state.
- Red Team must not see market/Street targets, intrinsic value, position data or market loaders.
- Preserve the OCI legacy formula engine until replacements have regression fixtures.
- Migrate live valuation through `LEGACY_REGRESSION → PRIMARY_SHADOW → LIVE_PRIMARY`; never mix modes key-by-key.
- Label unimplemented live Funding/Street/calibration adapters `CONTRACT_ONLY` or `NOT_IMPLEMENTED`; never fabricate completed scans.

## Methodology description
The v0.4 system is **academically grounded engineering synthesis**. Established finance/accounting components and repository-specific orchestration must be distinguished explicitly; see `docs/V04_ROCKETSLA_EXTENSION.md`.

## Delegation
For large additions use bounded subagents where available: Evidence/data-source, Industry/router, Funding/credit, Beta/WACC/PER calibration, Valuation implementation, Street reconciliation, Audit/red-team. The main agent reconciles conflicts and owns final model integrity.
