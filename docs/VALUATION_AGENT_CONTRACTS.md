# Agent detail contracts

These preserve mandatory runtime, revision and delivery requirements. Read the applicable section when executing a valuation, changing its economics, revising outputs or delivering a report. Repository-relative paths resolve from the repository root. AGENTS.md defines task scope and validation scope.

## Non-negotiable architecture
1. LLM = evidence interpretation, causal reasoning, Economic-Twin rationale, hypothesis generation, confidence/probability rationale and missing-evidence requests.
2. Deterministic code = units, valuation math, beta/PER partial pooling, WACC arithmetic, probability weighting, DCF–PER consistency, Street arithmetic, duplicate-path detection and audit tests.
3. Market price is comparison-only; never solve intrinsic assumptions backwards from price.
4. Street targets/forecasts are inaccessible until `INTRINSIC_VALUE_FREEZE`; a Street-discovered claim cannot mutate the same frozen run.
5. Evidence, Hypothesis, Bridge, Assumption and Model Output are separate object types.
6. Segment decomposition → evidence-driven multi-label Industry DNA routing → Module Requirement Plan happens before valuation-method selection/data collection.
7. Unverified futures are not automatic 0/100. A success probability or single probability-weighted target requires calibration. An `UNCALIBRATED` analyst prior may support only a source-bound ambiguity set: report its expected-value interval and, when every dated payoff is independently authorized, a robust expected-return entry ceiling across complete probability/pricing-model vertices. This is not the branch with the lowest individual probability and must never be described as a calibrated success probability.
8. CAPEX, funding shocks and qualitative advantages must not be double-counted across economic paths.
9. Policy Intent and downstream Transmission Effect are separate claims.
10. Validate changed model behavior with relevant regression/audit tests; follow AGENTS.md for the full-suite publication gate.
11. A valuation-changing headline, title or investment point must be synchronized to the same run's `Evidence → Hypothesis → Bridge → compiled Assumption → Valuation` chain. The report must show the prior value, revised value, changed assumption and value delta. If the chain does not change intrinsic value, classify the claim `REFERENCE_ONLY`; it may appear only as context/risk and may not lead the title or conclusion. Report generation must fail closed when this mapping is missing or when a `VALUED` headline leaves intrinsic value unchanged.

## Valuation calibration gates
- **Beta:** estimate/normalize comparables consistently; unlever peers; L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins; Bayesian/precision partial pooling; then target relever. Fixed level weights are forbidden.
- **WACC:** currency-consistent risk-free rate, market-level ERP, exposure-adjusted country risk, marginal Cost of Debt, market-value target capital structure, terminal consistency. Customer advances improve FCFF/ROIC first; WACC falls only after separate credit-risk evidence.
- **PER:** positive normalized forward EPS; Core Fundamental / Expansion-Adjusted / Market-Realization PER kept separate; Core must share DCF economics; Expansion needs committed/pre-invested evidence; peer market residuals, not raw P/E, are hierarchically pooled.
- **Cross-method:** track material quality/risk drivers with `economic_path_id`; do not capitalize the same visibility/cyclicality/leverage benefit through Beta, WACC, FCF and PER premium without distinct mechanisms/evidence.
- **Scenario probability:** use the target's own realized driver history, not a peer-company distribution. Peer panels remain valid for shared market quantities such as Beta and PER residuals, but another issuer's operating history must not weight the target's Down/Base/Bull cases. Continuous driver draws remain unassigned until each path is valued; named scenarios summarize the frozen value distribution afterward. Historical nearest-anchor snapshots require their exact frozen replay receipt and cannot authorize any new investor output. If the target lacks enough history, report `UNCALIBRATED` with the reason. A load-bearing analyst prior must be represented by at least two distinct source-bound probability vectors. It may produce an ambiguity-set expected-value interval and a robust expected-return reference, but not a single calibrated expected value or quantile success claim. For the reference, value every complete probability-vector × payoff-model-case pair, preserve correlations inside each complete model case, discount every shareholder cash flow from its actual payment period, and take the lowest expected present value. Never select the lowest individual probability or mix a different adverse financing case into each branch. A small discrete-event Q25 is diagnostic; withhold it when its supporting branch changes inside the ambiguity set.
- **Structural equity option:** a Merton-style residual value is primary only when the strike is the promised claim at the model horizon, assets and asset volatility are jointly market-calibrated, and liabilities have a genuine single maturity or an independently validated equivalent maturity. A current carrying claim, DCF-derived asset value, scenario-envelope volatility or multi-maturity aggregate makes the result diagnostic-only; it cannot authorize target value, entry price or intrinsic freeze.
- **Forward evidence update:** own history is a prior, not a ceiling. Target-specific primary evidence such as signed backlog/RPO, executable capacity, contracted pricing, permits and achieved operating milestones may condition the same economic drivers when its source, first-seen time, conversion mechanism and failure modes are explicit. The LLM maps the evidence to drivers and scenarios; deterministic code updates probabilities and value. Current share price and Street targets are forbidden conditioning evidence.

## Industry Knowledge / Signal Intelligence gates
- Freeze Industry Knowledge and source-watch snapshots per run; do not let later publications silently mutate an in-flight valuation.
- Use `KnowledgeLayer` placement contracts. Structural priors, broker research, alternative data and calibration references cannot bypass primary/company Evidence → Bridge requirements.
- Broker/IB research before intrinsic freeze is discovery/corroboration only; target-company forecast/target price/rating/multiple/consensus are post-freeze Street objects.
- `NOT_OBSERVED` is not `NO_EVENT`; source failure, reporting lag and incomplete coverage cannot be converted into negative evidence.
- Preserve event/effective/published/first-seen/revision timestamps to prevent look-ahead.
- Financing-market references may support funding/WACC through an explicit Bridge; positioning signals never mutate intrinsic; target-equity market references are post-freeze only.
- Unsupported archetype or critical module conflict blocks valuation instead of falling back to generic DCF.
- Public repo stores structured derived facts/metadata, not paid/licensed broker-report bodies or secrets.

## Workflow gates
- Keep root and canonical Skill byte-identical.
- Do not load Street/current price before Audit PASS and intrinsic freeze.
- Reverse DCF/current-market implied expectations are post-freeze only and cannot mutate the same frozen run.
- Do not emit intrinsic value from blocked runs; blocked runs cannot replace last-good state.
- Red Team must not see market/Street targets, intrinsic value, position data or market loaders.
- Preserve the OCI legacy formula engine until replacements have regression fixtures.
- Migrate live valuation through `LEGACY_REGRESSION → PRIMARY_SHADOW → LIVE_PRIMARY`; never mix modes key-by-key.
- Label unimplemented live Funding/Street/calibration adapters `CONTRACT_ONLY` or `NOT_IMPLEMENTED`; never fabricate completed scans.

## Revision request orchestration

- Split every user correction into atomic clauses with a desired outcome, affected unit/output, file read/write set and observable acceptance criteria before assigning work.
- Use `revision_orchestration.py` to select only the required Unit Contract path. The Unit Contract consumer graph may scope impact but, because it permits feedback cycles, it must never be used as the execution DAG.
- Parallelize only tasks with no dependency and disjoint write sets. If two tasks can write the same file or generated artifact, give them one owner or add an explicit sequential dependency; unordered overlap fails closed.
- Preserve hard barriers: Evidence before Bridge, Bridge before Compiler/model, model before report/artifact regeneration, targeted checks before the required full regression for model/runtime changes, and that regression before their publish/merge. Documentation or editorial-only validation follows AGENTS.md.
- On failure or a changed clause, invalidate only that task and its descendants. Reuse completed independent tasks only when the base revision and `plan_hash` still match; scope expansion requires a new plan.
- Disjoint write sets are enforced **across pull requests**, not only inside one plan. `config/work_claims.yaml` names the guarded areas — run directories, the economic contract registries, evaluator identity and the segment note — and a pull request that changes a path inside one must hold an active claim naming itself. Two agents heading for the same area then collide in that one short file, before either writes code, instead of colliding across a whole diff after both are finished. A claim is not a lock on the repository: it states who is already working there, and its status moves to `merged` or `released` when its pull request ends. Never take a path another active claim holds; coordinate with that request, or wait for it. A claim already on the default branch is seen by every later request; two requests opened at once are caught by each request reading the other open requests' registries, and by the default-branch registry check as the backstop. Claim paths are exact files or `prefix/**` subtrees, so overlap is decidable.
- A revision is merge-ready only when every clause maps to a task and validator, actual writes remain inside declared write sets, generated artifacts match upstream model outputs, and the latest immutable artifact manifest points to the exact version delivered to the user.

## Reporting and delivery
- The first-screen 투자 요약 is the investment report itself: 투자판단, post-freeze 현재가, 기준 내재가치, 가치평가 범위, one-sentence conclusion, up to three investment points and conditions that strengthen/weaken/unlock action. Never substitute an internal gate summary.
- Count the two SVG cards inside the main-body page target. Keep all material limitations; present unavailable calibration without inventing expected value or buy price.
- The Control Plane owns the canonical five-gate progress contract in `config/control_plane_stage_registry.yaml`; individual adapters and agents may not invent parallel progress groupings.
- Emit one compact summary when each major gate completes or terminates blocked: status, decisive result, residual risk and next action. Do not stream all 33 stage traces as routine progress.
- Keep all 33 stage identities/statuses in the compact audit appendix and the exact rationales/output keys in the immutable `control_plane_trace.json` artifact.
- Editorial targets are 3–4 pages for the decision-facing body, 1–2 pages for the audit appendix and 6 pages maximum combined. Use body text of at least 13pt, primary headings of at least 22pt and section headings of at least 18pt; dense wide tables are forbidden. These are presentation targets, never grounds to omit a material blocker, uncertainty or integrity record.
- Source provenance is never shortened away. Every active Evidence claim and every reported identity/Beta/WACC/PER/Street/market reference must map to a directly clickable HTTP(S) original-source link; non-HTTP, credential-bearing or missing source references block a `LIVE_PRIMARY` final report. Group repeated claims by source URL to stay compact.
- The user-facing final report is Korean by default. Technical IDs, status enums and original-source titles may remain unchanged, but headings, conclusions, explanations and gate summaries must not fall back to English.
- The decision-facing body must use a Korean brokerage-report order—투자 요약 → 가치평가 → 핵심 가정과 위험 → 증권사·시장 비교 → 원문 출처—and appear before the audit appendix. Raw stage IDs, enums and hashes are collapsed technical detail or immutable machine artifacts; visible stage names and statuses are Korean.
- Separate LLM-authored linkage insight from deterministic assumptions, calculations, Audit and Freeze outputs. The displayed `인공지능 인사이트` section is capped at 1,000 Korean characters; the complete typed artifact remains in `context_strength_linkages.json`.
- A final report is incomplete without two deterministic Korean SVG cards generated from the same immutable run data: `회사 강점·투자 결론·가치평가` and `가치평가 가정·위험·출처`. If calibration or an entry rule is unavailable, the card must withhold a specific buy price rather than fabricate one.
- Publish each downloadable report under an immutable filename containing its as-of date, reference intrinsic value and artifact hash. Keep a mutable latest alias only for repository automation; user delivery must use the versioned filename and its hash-bound latest manifest so an older same-named download cannot be mistaken for the current report.

## Methodology description
The v0.4 finance-calibration system is **academically grounded engineering synthesis**. v0.5.2 adds repository-specific evidence-governed Industry Knowledge and Signal Intelligence operating contracts. Established finance/accounting components and repository-specific orchestration must be distinguished explicitly; see `docs/V04_ROCKETSLA_EXTENSION.md`.
