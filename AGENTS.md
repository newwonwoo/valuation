# Project instructions for Codex

## Mission
Build a reusable valuation system where the user can type `분석시작 <company>` in ChatGPT/Codex and receive an evidence-first valuation without current-price anchoring.

## Non-negotiable architecture
1. LLM = evidence interpretation, causal reasoning, hypothesis generation, confidence/probability rationale.
2. Deterministic code = unit conversion, valuation math, probability weighting, duplicate-value detection, audit tests.
3. Market price is comparison-only. Never solve assumptions backwards from current price.
4. Evidence source layers are only realized_or_filing, company_official_plan, policy_primary_source, external_reference, or market_comparison. Model assumptions and outputs are separate record types, never Evidence.
5. Evidence source layer and Evidence role are separate. Target-company market price has role target_market_price and is unavailable before Audit PASS; permitted valuation references require their own Bridge.
6. Industry model routing happens before valuation.
7. Unverified future developments are not automatically zero and never automatically 100%; use evidence-backed probability weighting.
8. CAPEX must not be double-counted when future EBITDA already includes expansion. Use funding gap / terminal net debt where appropriate.
9. Every model change needs a regression test and an audit explanation.

## Coding style
- Keep the core engine pure and deterministic.
- Avoid framework-heavy architecture until needed.
- Prefer dataclasses/types and small functions.
- Every industry module must expose explicit assumptions and kill conditions.
- No hidden constants in valuation formulas.

## Validation
Before reporting results run pytest, current-price anchor stress test, probability-sum test, unit consistency tests, and scenario sensitivity checks.

## v0.3 workflow gates
- Treat `.agents/skills/valuation-analysis/SKILL.md` as the canonical runtime contract; keep root `SKILL.md` identical for compatibility.
- Do not call a market-price loader before Audit PASS.
- Do not emit intrinsic value from a blocked run. Save `valuation.json` as suppressed and preserve the last successful current state.
- Keep Researcher and Red Team adapters replaceable. Red Team context must not contain market, valuation, target-price, or position fields and must not expose a market loader.
- Store live thesis/evidence/run history outside this public repository. Fixtures must be clearly labeled.
- Preserve the existing OCI formula engine behind the traced v0.3 workflow until a replacement has its own regression and audit fixtures.
- Treat `docs/GENERIC_ENGINE_DESIGN.md` and `docs/LIVE_VALIDATION_AND_CALIBRATION.md` as the implementation contracts for the generic engine, live-company validation and probability calibration.
- Migrate live valuation only through `LEGACY_REGRESSION → PRIMARY_SHADOW → LIVE_PRIMARY`; never mix modes key by key inside one run.

## Delegation
For large additions, use bounded subagents when available: Evidence/data-source, Industry/model-router, Valuation implementation, Audit/red-team. The main agent reconciles conflicts and owns the final model.
