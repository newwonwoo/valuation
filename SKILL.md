---
name: insight-valuation-analysis
description: Complete or revise company valuations through newwonwoo/valuation's orchestrator and deliver verified investor reports. Use for valuation runs and thesis or valuation-report updates.
---

# Valuation analysis through final delivery

Use `AGENTS.md` for task completion, authority and targeted reading. All paths here are repository-root-relative, including when this file is loaded from `.agents/skills/valuation-analysis/`. This skill applies to supported Korean and global company valuations; check actual country/method support instead of assuming all providers exist.

## Start from the real state

Identify the company, requested scope and as-of date from the task and existing run. Inspect the relevant executable entrypoint, saved state and current inputs. Reuse valid existing research and resume work; do not repeat finished stages by habit or substitute an offline fixture. The runtime exists, but a particular provider or evaluator may still be missing.

The Control Plane owns stage order and access in `config/control_plane_stage_registry.yaml`. The host supplies original-source interpretation and typed responses; the compiler, deterministic engines and audit own committed assumptions, values and freeze. Never compose a replacement valuation in chat.

## Complete the research-to-report loop

For research gaps or recovery, read `docs/RESEARCH_CAMPAIGN_RUNBOOK.md` and use the supported `scripts/run_research_campaign.py <campaign> --workspace <workspace> --run-dir <prepared-run>` path. For initial KR preparation read `docs/RUNBOOK_KR_LIVE.md`. The prepared-run campaign adapter is currently KR; do not claim it validates other jurisdictions.

An emitted work order is the host's next action. Read research/staff/completion requests, search original sources, create correctly bound responses and resume the same workspace. The user does not prepare JSON or obtain an LLM API key. Verify supported assisted/replay modes from the current runner. Do not replay an answer bound to an older prompt or reuse a cached result after its inputs change.

Missing disclosed numbers are research problems, not automatic final answers. Use primary/company sources first; then independent sources and broker discovery clues; then comparable-business realized margins with explicit adjustments or defensible bounded inference. Normalize EBIT versus EBITDA, period, accounting basis, scale, utilization and year-specific adjustments. Keep analyst assumptions distinct from reported facts; peer operating data may support margins, not the target's scenario probability calibration. See the campaign runbook for response schemas and peer-margin receipts only when needed.

Carry material business/new-project assumptions through executable capacity, demand/contracts, price and mix, costs/margins, tax, investment and working capital to cash flow, net debt/dilution and per-share value as applicable. Announced capacity is not automatically executable or sold. Reflect upside supported by new contracts, capacity and achieved milestones as well as failure paths; historical averages are not an automatic ceiling and optimism is not evidence.

Repair and rerun until the authorized request's completion criteria hold. Respect research/completion budgets and actual permissions; save resumable work and the exact remaining issue if a genuine blocker survives. A failed gate cannot be converted into PASS, and partial valuation cannot be presented as full-company completion.

## Preserve valuation integrity

- Keep Evidence, Hypothesis, Bridge, Assumption and Model Output separate. Every compiled valuation assumption needs a sourced economic bridge and a verification/kill condition.
- Current target share price and Street forecasts/targets cannot set assumptions or probabilities. Load them only after audit and intrinsic freeze; use broker material before freeze only for permitted discovery/corroboration. A Street-discovered claim requires independent verification in a new run.
- Fit target scenario probabilities using the target's own realized driver history and governed forward evidence. Peer panels may support Beta/PER market quantities. Do not invent equal weights, fabricate Monte Carlo, label a prior calibrated or use nearest-scenario-anchor counts as target probabilities. Historical v3.2 nearest-anchor snapshots are exact-hash replay artifacts only; without the frozen receipt they are diagnostic, and even with it they cannot authorize a new target, entry price or success-probability claim.
- If calibration remains unsupported, default to conditional scenarios without a single probability-weighted target or success-probability buy price. A load-bearing governed analyst prior must form an explicit ambiguity set of at least two distinct source-bound probability vectors, not one exact prior. A registered evaluator and audit may then report the expected-value interval and, only from independently authorized pathwise payoffs, the worst-prior expected-return entry ceiling with full probability/assumption sensitivities. Label both as governed-prior outputs, never as calibrated success probabilities. A quantile entry requires a calibrated high-resolution distribution; for a small event set it remains diagnostic and is withheld when its supporting branch changes anywhere in the ambiguity set. Never let missing calibration silently reuse an older weighted target or buy price.
- Preserve source timing, full material segment coverage, supported method routing, unit/currency/accounting consistency, funding and CAPEX double-count protection, EV-to-equity and dilution checks, blinded Red Team, audit/freeze and last-good-state protections.

### Reusable capacity-yield leveraged distributional APV route

- Route an evidenced capacity×unit-yield business with material fixed operating commitments and debt/lease claims to `capacity_yield_levered/driver_distributional_apv`. Industry labels are only metric adapters; company name, ticker and `target_id` may bind data but never select a formula. Keep `airline_transport/traffic_yield_dcf` as a distinct legacy cross-check.
- Keep the reusable core separate from sector/company inputs. The core owns capacity×utilization×unit-yield operating paths, fixed/variable cost transmission, committed-asset/CAPEX/lease roll-forward, recursive driver distributions, APV, refinancing/dilution/distress waterfall, old-shareholder value distribution and return-based entry arithmetic. Adapters own metric names, units, comparable-perimeter history, structural breaks, asset/claim schedules and SOTP declarations.
- Permit a self-history distribution only after same-frequency target history passes rolling-origin proper-score, interval-coverage, dependence-reproduction and seed/draw stability gates. Permit `COMPOSED_SEGMENT_POSTERIOR` after a merger only when every material predecessor/segment passes its own history gate and a source-bound transaction bridge reconciles the current perimeter. Isolate data-short merger outcomes as mutually exclusive `GOVERNED_EVENT_PRIOR` branches with the prior source and full sensitivity disclosure. Never fabricate pro-forma history or call an analyst prior calibrated.
- Apply limited liability only to an explicit dated old-shareholder terminal payoff or evidenced distress waterfall. A report or point-DCF layer may not floor negative present-value scenarios to zero. Freeze P50, mean, tail quantiles, distress/dilution risks and the versioned entry policy to the same distribution hash before Street or current-price access.
- For an uncalibrated event ambiguity set, calculate a robust entry only from complete probability-vector × payoff-model-case combinations. Discount each legal shareholder cash flow from its actual period, preserve within-case branch correlation, and bind authorization to evaluator receipts. Never use the lowest individual probability, an undated cumulative payoff, or branchwise worst-case cherry-picking. This ceiling is not a point target or success-probability claim.
- Use a Merton-style structural equity value as primary only when its strike is the promised claim at the model horizon, asset value and asset volatility are market-calibrated, and the liability structure has a genuine single maturity or independently validated equivalent maturity. Current carrying claims, DCF-derived assets, scenario-envelope volatility and aggregated multi-maturity debt/leases make it diagnostic-only; such a result cannot authorize a target, entry price or intrinsic freeze.

Read the applicable sections of `docs/VALUATION_RUNTIME_DETAILS.md` for source placement, claim-to-value binding, Industry DNA, funding, Beta, WACC, PER and cross-method requirements. Read `docs/VALUATION_AGENT_CONTRACTS.md` for runtime/calibration gates and revision/delivery details. These constraints remain mandatory when relevant; a short entrypoint does not relax them.

## Revisions and delivery

Use the smallest supported affected path. Preserve unchanged research, but changed economic inputs must reach new calculations, reviewed investor text and regenerated artifacts. Do not patch a report's numbers manually, skip the orchestrator or reuse an older bundle. A changed claim with unchanged value needs an explained, verified reference-only classification.

Before final delivery, confirm full material scope, audit/freeze and saved state; read the actual versioned Korean investor report and verify its claims and figures against the same run. Confirm both SVG cards, immutable bundle/manifest and delivery link. The report order is 투자 요약 → 가치평가 → 핵심 가정과 위험 → 증권사·시장 비교 → 원문 출처. Conclusions, scenario assumptions, risks, dates and original-source links belong in the report; hashes, internal paths, JSON and maintenance identifiers do not belong in its narrative.

Deliver the actual verified report, not a plan or a rewritten chat substitute. State known limitations plainly. Do not claim live-company validation from fixture tests or that a pending merge/CI has finished.
