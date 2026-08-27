# Project instructions for Codex

## Mission
Build a reusable valuation system where the user can type `분석시작 <company>` in ChatGPT/Codex and receive an evidence-first valuation without current-price or Street anchoring.

## Source of truth
Use these files in order and keep them consistent:

1. `.agents/skills/valuation-analysis/SKILL.md` — canonical runtime contract; root `SKILL.md` must remain byte-identical.
2. `01_Rocketesla_Insight_Valuation_Framework.md` — core research/valuation methodology.
3. `docs/V04_ROCKETSLA_EXTENSION.md` — v0.4 Upstream Funding, Beta, WACC, Hierarchical Warranted PER, Street Gap, academic/practitioner rationale and limitations.
4. `docs/V05_WORKFLOW_CONTRACT.md` — v0.5.2 Industry Knowledge/Industry DNA workflow ordering and fail-closed gates.
5. `docs/README_V05_OPERATOR_INDEX.md` — source/module/operator navigation.
6. `docs/SIGNAL_INTELLIGENCE_LAYER_V1.md` — deep-signal, negative-evidence, latency and market-role contracts.
7. `docs/GENERIC_ENGINE_DESIGN.md` — generic deterministic-engine implementation contract.
8. `docs/LIVE_VALIDATION_AND_CALIBRATION.md` — live validation/probability calibration.

`references/methods/`, `references/industry/`, and `references/modes/` are **operator supplements, not parallel sources of truth**. They may provide checklists or fallback procedures but never override the canonical documents, deterministic code, or v0.5.2 registries. When a simplified reference conflicts with a canonical gate, the canonical gate wins and the conflict must not be silently averaged.

If documents conflict, preserve the conflict and reconcile the newest intentional design decision across source-of-truth files. Do not silently choose whichever produces a convenient valuation.

## Non-negotiable architecture
1. LLM = evidence interpretation, causal reasoning, Economic-Twin rationale, hypothesis generation, confidence/probability rationale and missing-evidence requests.
2. Deterministic code = units, valuation math, beta/PER partial pooling, WACC arithmetic, probability weighting, DCF–PER consistency, Street arithmetic, duplicate-path detection and audit tests.
3. Market price is comparison-only; never solve intrinsic assumptions backwards from price.
4. Street targets/forecasts are inaccessible until `INTRINSIC_VALUE_FREEZE`; a Street-discovered claim cannot mutate the same frozen run.
5. Evidence, Hypothesis, Bridge, Assumption and Model Output are separate object types.
6. Segment decomposition → evidence-driven multi-label Industry DNA routing → Module Requirement Plan happens before valuation-method selection/data collection.
7. Unverified futures are probability-weighted from evidence, not automatic 0/100. Numeric probabilities require calibration; otherwise label them `UNCALIBRATED`.
8. CAPEX, funding shocks and qualitative advantages must not be double-counted across economic paths.
9. Policy Intent and downstream Transmission Effect are separate claims.
10. Every model change requires regression/audit tests.

## Valuation calibration gates
- **Beta:** estimate/normalize comparables consistently; unlever peers; L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins; Bayesian/precision partial pooling; then target relever. Fixed level weights are forbidden.
- **WACC:** currency-consistent risk-free rate, market-level ERP, exposure-adjusted country risk, marginal Cost of Debt, market-value target capital structure, terminal consistency. Customer advances improve FCFF/ROIC first; WACC falls only after separate credit-risk evidence.
- **PER:** positive normalized forward EPS; Core Fundamental / Expansion-Adjusted / Market-Realization PER kept separate; Core must share DCF economics; Expansion needs committed/pre-invested evidence; peer market residuals, not raw P/E, are hierarchically pooled.
- **Cross-method:** track material quality/risk drivers with `economic_path_id`; do not capitalize the same visibility/cyclicality/leverage benefit through Beta, WACC, FCF and PER premium without distinct mechanisms/evidence.

## Industry Knowledge / Signal Intelligence gates
- Freeze Industry Knowledge and source-watch snapshots per run; do not let later publications silently mutate an in-flight valuation.
- Use `KnowledgeLayer` placement contracts. Structural priors, broker research, alternative data and calibration references cannot bypass primary/company Evidence → Bridge requirements.
- Broker/IB research before intrinsic freeze is discovery/corroboration only; target-company forecast/target price/rating/multiple/consensus are post-freeze Street objects.
- `NOT_OBSERVED` is not `NO_EVENT`; source failure, reporting lag and incomplete coverage cannot be converted into negative evidence.
- Preserve event/effective/published/first-seen/revision timestamps to prevent look-ahead.
- Financing-market references may support funding/WACC through an explicit Bridge; positioning signals never mutate intrinsic; target-equity market references are post-freeze only.
- Unsupported archetype or critical module conflict blocks valuation instead of falling back to generic DCF.
- Public repo stores structured derived facts/metadata, not paid/licensed broker-report bodies or secrets.

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
- Reverse DCF/current-market implied expectations are post-freeze only and cannot mutate the same frozen run.
- Do not emit intrinsic value from blocked runs; blocked runs cannot replace last-good state.
- Red Team must not see market/Street targets, intrinsic value, position data or market loaders.
- Preserve the OCI legacy formula engine until replacements have regression fixtures.
- Migrate live valuation through `LEGACY_REGRESSION → PRIMARY_SHADOW → LIVE_PRIMARY`; never mix modes key-by-key.
- Label unimplemented live Funding/Street/calibration adapters `CONTRACT_ONLY` or `NOT_IMPLEMENTED`; never fabricate completed scans.

## Reporting and delivery
- The Control Plane owns the canonical five-gate progress contract in `config/control_plane_stage_registry.yaml`; individual adapters and agents may not invent parallel progress groupings.
- Emit one compact summary when each major gate completes or terminates blocked: status, decisive result, residual risk and next action. Do not stream all 33 stage traces as routine progress.
- Keep all 33 stage identities/statuses in the compact audit appendix and the exact rationales/output keys in the immutable `control_plane_trace.json` artifact.
- Editorial targets are 3–4 pages for the decision-facing body, 1–2 pages for the audit appendix and 6 pages maximum combined. Use body text of at least 13pt, primary headings of at least 22pt and section headings of at least 18pt; dense wide tables are forbidden. These are presentation targets, never grounds to omit a material blocker, uncertainty or integrity record.
- Source provenance is never shortened away. Every active Evidence claim and every reported identity/Beta/WACC/PER/Street/market reference must map to a directly clickable HTTP(S) original-source link; non-HTTP, credential-bearing or missing source references block a `LIVE_PRIMARY` final report. Group repeated claims by source URL to stay compact.

## Methodology description
The v0.4 finance-calibration system is **academically grounded engineering synthesis**. v0.5.2 adds repository-specific evidence-governed Industry Knowledge and Signal Intelligence operating contracts. Established finance/accounting components and repository-specific orchestration must be distinguished explicitly; see `docs/V04_ROCKETSLA_EXTENSION.md`.

## Delegation
For large additions use bounded subagents where available: Evidence/data-source, Industry/router, Funding/credit, Beta/WACC/PER calibration, Valuation implementation, Street reconciliation, Audit/red-team. The main agent reconciles conflicts and owns final model integrity.
