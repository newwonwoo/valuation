---
name: insight-valuation-analysis
description: Evidence-first equity valuation workflow. Use when the user says "분석시작", asks to value a company, estimate fair value, update a valuation, find buy zones, or combine industry insight with valuation. Trigger for Korean or global equities. Do not use for simple price lookup or news summary without valuation intent.
---

# Insight Valuation Analysis

## Trigger command
When the user says `분석시작 <회사/티커>`, execute this workflow immediately. If the company is unambiguous, do not ask a confirmation question.

## Objective
Produce a valuation whose assumptions are generated from evidence and future-value probabilities, not reverse-engineered from the current stock price.

## Workflow
1. Identify company, ticker, geography, reporting currency, holding-company status, and business segments.
2. Route the company to an industry valuation module before choosing metrics.
3. Collect primary evidence first: filings, official IR, regulator/policy originals. Analyst/media material is secondary reference.
4. Build an Evidence Ledger and label every number: 실적·공시값 / 회사 공식 IR 계획 / 정책 원문 / 외부 참고치 / 모델 가정 / 모델 산출값 / 시장 비교값.
5. Run the insight scanners in the references: money flow, bottleneck, action sequence, hidden preparation, policy/market segmentation, P×Q×Mix×Yield, funding, timing, multiple, long/short path, kill conditions.
6. Convert each material insight into an Evidence → Assumption Bridge. State exactly which variable changes: price, quantity, utilization, margin, funding gap/net debt, discount rate, multiple, probability, or segment value.
7. Build Bear/Base/Bull and, when justified, a named strategic-option scenario. Move interacting variables together.
8. Use deterministic scripts/code for valuation calculations. Never calculate final values only in prose.
9. Run audit tests: current-price anchor zero, probability sum, units, no double counting, source labels, sensitivity direction.
10. Compare with current stock price only after intrinsic/scenario values are complete.
11. Output conclusion first, confirmed value, 미래가치 확률반영, key assumptions, what is priced, catalysts, kill conditions, and next verification events.

## Probability discipline
- Never choose probabilities to make expected value close to market price.
- Probability changes require a new observable event or evidence-quality change.
- Record the causal reason for every probability change.

## Strategic options
A rumored or negotiating customer/project can have non-zero value when multiple independent behaviors support it. Do not add the entire contract value on top of a scenario that already assumes the related utilization. Reflect it through probability, utilization certainty, margin quality, funding, or multiple unless revenue/capacity is truly incremental.

## Current-price rule
Market price is a diagnostic after valuation, never an input to fair-value assumptions.
