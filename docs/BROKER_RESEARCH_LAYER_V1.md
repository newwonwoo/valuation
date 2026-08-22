# Broker / Street Research Layer v1

## Purpose

Use sell-side research to discover industry structure, KPI definitions, leading indicators, unresolved investor debates and valuation-method conventions without contaminating blind intrinsic valuation.

## Core architecture

```text
Broker Research Index
→ Rights / Entitlement Gate
→ Report-Type Classifier
→ Field-Level Quarantine
→ Underlying-Data Lineage
→ Industry Claim Extractor
→ Mechanism / KPI / Debate Candidates
→ Independent Primary/Public Verification
→ Industry Knowledge Layer

INTRINSIC_VALUE_FREEZE
→ Target-company forecasts / target price / rating / target multiple / consensus
→ Street Gap Analyzer
```

## Why a separate layer is required

Sell-side is unusually valuable for *questions and mechanisms* because analysts repeatedly map industry value chains, maintain sector-specific KPI models, conduct channel checks, attend conferences and explain why a metric matters. It is weaker as canonical truth because many reports share the same third-party datasets, management guidance and Street narratives.

Therefore `broker_family` and `underlying_data_family` are separate lineage fields. Two reports from two brokers that both source TrendForce, Counterpoint, SNE Research, Clarksons, FactSet or Bloomberg are not two independent confirmations of the underlying fact.

## Pre-freeze allowed

- Industry definition and taxonomy candidates
- Value-chain maps
- KPI / accounting definition candidates
- Industry-wide mechanism candidates
- Leading-indicator candidates
- Industry-wide forecasts tagged `FORWARD_HYPOTHESIS`
- References to underlying primary/industry datasets for follow-up verification

## Pre-freeze prohibited

- Target-company broker EPS/revenue/EBITDA/FCF forecasts
- Target price, rating, implied upside
- Broker target multiple
- Consensus or trimmed consensus
- Target-company model values inferred from broker target price

Those fields enter only after `INTRINSIC_VALUE_FREEZE` and belong to Street Gap / model-reproduction analysis.

## Report-type use

1. **Industry Primer / Deep Dive** — highest structural value. Extract value chain, unit economics, bottlenecks, KPI definitions and method conventions.
2. **Channel Check / Field Trip** — strongest discovery value but weakest source authority. Turn into a verification request, not realized evidence.
3. **Quant / Alternative Data** — useful leading signals. Record methodology, sample, coverage, vintage and licensing; never treat a black-box proprietary series as canonical by itself.
4. **Industry Outlook** — scenario priors only. Preserve forecast vintage and later score against realized outcomes.
5. **Initiation** — excellent business-model and peer-map source; quarantine all company forecasts/valuation before freeze.
6. **Earnings Preview/Review** — use post-freeze for Street expectation and estimate-delta tracking; pre-freeze only generic KPI watchlists may survive field-level quarantine.

## Analyst / broker calibration

Persist forecast vintages rather than overwriting them. When realized values arrive, score:

- signed error and absolute percentage error by metric/horizon
- direction hit rate
- revision lead time
- channel-check lead/lag to realized data
- target-model reproduction PASS/FAIL
- source-lineage concentration

Calibration changes *weight*, never truth status. A historically accurate analyst is still secondary evidence.

## Copyright / entitlement

Public research indices may be indexed for metadata. Raw text/PDF storage obeys the publisher's terms. Client-only, entitled, redacted or third-party-distributed reports are stored only as metadata plus user-authorized derived facts in private state. Never bulk mirror sell-side PDFs into a public repository.
