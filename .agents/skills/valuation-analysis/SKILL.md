---
name: insight-valuation-analysis
description: Evidence-first equity valuation workflow. Use when the user says "분석시작", asks to value a company, estimate fair value, update a valuation, find buy zones, or combine industry insight with valuation. Trigger for Korean or global equities. Do not use for simple price lookup or news summary without valuation intent.
---

# Insight Valuation Analysis

## Trigger
When the user says `분석시작 <회사/티커>`, execute this workflow immediately. If the company is unambiguous, do not ask a confirmation question.

## Objective
Generate valuation assumptions from evidence and future-value probabilities. Never reverse-engineer them from the current stock price.

## Workflow
1. Resolve company/ticker, geography, reporting currency, holding-company status, and segments.
2. Route industry/model before choosing KPIs or valuation method.
3. Gather primary sources first: filings, official IR, regulator/policy originals. Analyst/media sources are secondary.
4. Build an Evidence Ledger. Every number must be one of: 실적·공시값 / 회사 공식 IR 계획 / 정책 원문 / 외부 참고치 / 모델 가정 / 모델 산출값 / 시장 비교값.
5. Run insight scans: money flow, bottleneck, action sequence, hidden preparation, policy/market segmentation, P×Q×Mix×Yield, funding, timing, multiple, long/short path, kill conditions.
6. Build Evidence → Assumption Bridges. Each bridge must name the changed variable: price, quantity, utilization, margin, funding gap/net debt, discount rate, multiple, probability, or segment value.
7. Build Bear/Base/Bull plus an optional named strategic-option scenario when justified. Move interacting variables together.
8. Use deterministic code for valuation math. Do not calculate final fair values only in prose.
9. Run audit checks: current-price anchor zero, probability sum, units, source leakage, no double count, expected sensitivity direction.
10. Compare with market price only after intrinsic/scenario values are complete.
11. Report conclusion first, confirmed/core value, 미래가치 확률반영, assumptions, what appears priced, catalysts, kill conditions, next verification events.

## Probability rules
- Never tune probabilities to make expected value close to market price.
- Probability changes require a new observable event or evidence-quality change.
- Log the causal reason for each probability change.

## Strategic options
A negotiating or rumored customer/project can have non-zero probability-weighted value when multiple independent behaviors support it. Avoid double counting: if Base already assumes the related utilization, do not add the full contract again. Reflect the option via utilization certainty, margin quality, funding, multiple, or probability unless capacity/revenue is genuinely incremental.

## Current-price rule
Market price is diagnostic output only. It is never an intrinsic-value input.

## Project rules
Read repository `AGENTS.md` before modifying code or model architecture. Run tests before reporting results.
