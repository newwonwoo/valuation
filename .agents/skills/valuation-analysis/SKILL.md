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
- Fit target scenario probabilities using the target's own realized driver history and governed forward evidence. Peer panels may support Beta/PER market quantities. Do not invent equal weights, fabricate Monte Carlo, label a prior calibrated or bind uncalibrated probabilities into expected value.
- If calibration remains unsupported after research, deliver valid conditional scenarios with the limitation clearly stated and without a fabricated weighted expected value or specific buy price. If those quantities were requested, identify them as outstanding; do not announce unconditional completion.
- Preserve source timing, full material segment coverage, supported method routing, unit/currency/accounting consistency, funding and CAPEX double-count protection, EV-to-equity and dilution checks, blinded Red Team, audit/freeze and last-good-state protections.

Read the applicable sections of `docs/VALUATION_RUNTIME_DETAILS.md` for source placement, claim-to-value binding, Industry DNA, funding, Beta, WACC, PER and cross-method requirements. Read `docs/VALUATION_AGENT_CONTRACTS.md` for runtime/calibration gates and revision/delivery details. These constraints remain mandatory when relevant; a short entrypoint does not relax them.

## Revisions and delivery

Use the smallest supported affected path. Preserve unchanged research, but changed economic inputs must reach new calculations, reviewed investor text and regenerated artifacts. Do not patch a report's numbers manually, skip the orchestrator or reuse an older bundle. A changed claim with unchanged value needs an explained, verified reference-only classification.

Before final delivery, confirm full material scope, audit/freeze and saved state; read the actual versioned Korean investor report and verify its claims and figures against the same run. Confirm both SVG cards, immutable bundle/manifest and delivery link. The report order is 투자 요약 → 가치평가 → 핵심 가정과 위험 → 증권사·시장 비교 → 원문 출처. Conclusions, scenario assumptions, risks, dates and original-source links belong in the report; hashes, internal paths, JSON and maintenance identifiers do not belong in its narrative.

Deliver the actual verified report, not a plan or a rewritten chat substitute. State known limitations plainly. Do not claim live-company validation from fixture tests or that a pending merge/CI has finished.
