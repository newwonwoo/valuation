---
name: insight-valuation-analysis
description: Run evidence-first, persistent equity research and deterministic valuation when the user says "분석시작 기업명", asks to update an existing thesis, validate assumptions, compare intrinsic value with market price, or inspect kill conditions. Use for Korean and global equities; do not use for simple price or news lookup without valuation intent.
---

# Insight Valuation Analysis v0.3

Read repository `AGENTS.md` before changing code or model architecture.

## Entry point

For `분석시작 <company>`, resolve the company without asking when unambiguous. Load prior company state before collecting new evidence. Use the repository engine:

```bash
valuation-engine "분석시작 OCI홀딩스" --state-root <private-state-path>
```

Treat this command as a reproducible offline vertical slice until live source adapters exist. Never present fixture evidence as current research.

## Required workflow

Execute in this order:

1. `COMPANY_RESOLUTION`
2. `LOAD_COMPANY_STATE`
3. `INDUSTRY_ROUTE`
4. `PRIMARY_EVIDENCE_COLLECTION`
5. `EVIDENCE_LEDGER`
6. `ROCKET_INSIGHT_SCAN`
7. `RESEARCHER_A`
8. `BLIND_RED_TEAM_B`
9. Up to three targeted `RESEARCH_LOOP` rounds
10. `EVIDENCE_TO_ASSUMPTION_BRIDGE`
11. `SCENARIO_BUILD`
12. `DETERMINISTIC_VALUATION`
13. `AUDIT_GATE`
14. `INTRINSIC_VALUE`
15. `MARKET_PRICE_LOAD`
16. `MARKET_COMPARE`
17. `THESIS_DELTA`
18. `SAVE_STATE`
19. `FINAL_REPORT`

If a blocking issue remains after round three, or a blocking audit fails, return `VALUATION BLOCKED`. Do not output fair value or load market price.

## Separation rules

Keep different objects and responsibilities:

`Evidence → Hypothesis → Bridge → Assumption → Valuation`

- Evidence is an external observation only.
- Hypothesis is LLM causal reasoning and is not fact.
- Every valuation assumption must reference one Bridge.
- Every Bridge must reference Evidence, a Hypothesis, an economic path, a kill condition, and a verification event.
- Deterministic code owns units, valuation math, probability weighting, duplicate-path checks, state promotion, and audit.
- LLM reasoning owns interpretation, causal chains, counter-theses, and missing-evidence requests.

## Non-negotiable gates

- Never use current price to select assumptions, probabilities, discount rates, or multiples.
- Load current price only after Audit PASS.
- Never convert a policy price directly into enterprise ASP without economic evidence.
- Never promote company plans to realized evidence.
- Never count the same evidence and economic path in operating value and option/SOTP value.
- Never deduct gross CAPEX again when expansion economics are already captured through future EBITDA and funding gap or terminal debt.
- Mark initial probabilities `UNCALIBRATED`; avoid false precision.
- Red Team input must exclude price, market gap, intrinsic value, target value, position data, and market-loader access.
- Save blocked runs, but never promote them to current company state.

## Industry routing

Route holding companies first, then delegate each segment. Each route must declare evidence keys and an allowed model. Use `src/valuation_engine/router.py` model contracts. Preserve the OCI legacy engine as a regression adapter; do not mistake it for the generic valuation model.

## State and privacy

Store code and general fixtures in this repository. Put live thesis, evidence, position rules, API keys, and private run history in a separate private state path or repository. Never commit secrets or personal investment state.

Each successful run updates current state atomically. Each run remains immutable under:

```text
runs/<ticker>/<run_id>/
```

Blocked and failed runs are retained for audit without replacing the last successful state.

## Verification

Before reporting:

```bash
pytest -q
valuation-engine examples/oci/company.yaml
valuation-engine "분석시작 OCI홀딩스" --state-root <temporary-path>
```

Confirm:

- OCI regression remains within ±1 KRW.
- Market-price stress does not change intrinsic value.
- Policy-only price evidence cannot change enterprise ASP.
- Probabilities sum to one.
- Unit, EV-to-equity, CAPEX, duplicate-path, stale-evidence, and source-leakage gates pass.
- Audit failure suppresses valuation and market comparison.
- Blocked runs do not replace current state.

## Report contract

Lead with the conclusion, then show thesis delta, known versus underappreciated evidence, strongest Red Team objection, scenario worldviews, Core/Expected/Verified Bull values, market comparison, separate position view, kill conditions, next verification events, data quality, and limitations. Clearly label fixture or stale evidence.
