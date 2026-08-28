# SK hynix large-sample probability calibration — 2026-08-28

## Decision

The earlier research shortcut that moved the scenario prior from `20/60/20` to `15/70/15` is rejected.

Final research calibration decision: **NO_PROBABILITY_UPDATE**.

- Down: **20%**
- Core: **60%**
- Bull: **20%**
- Probability-weighted intrinsic value: **KRW 3,226,790/share**

This is a research calibration result only. It does not issue a production `CALIBRATED` certificate.

## Work-record reconstruction

The repository work records define a 12-month semiconductor-memory historical cohort before outcome inspection:

- Frozen DART periodic-filing metadata universe: **1,777 records** across 30 companies.
- Predeclared design: 30 companies × 8 origins = 240 maximum cases.
- Expected eligible cases: **238** after two SK hynix holdout exclusions for prior result exposure.
- Training origins: 2021Q1, 2021Q2, 2021Q3, 2021Q4, 2022Q1, 2022Q2.
- Holdout origins: 2023Q3, 2024Q3.
- Cutoff is publication timestamp, not quarter end; later revisions cannot rewrite earlier snapshots.

The frozen filing index was replayed and all **238/238** declared origin→12-month-outcome filing pairs were found; no filing-pair identity was missing.

## Numeric replay

The GitHub runner does not currently expose `DART_API_KEY`, so the DART metadata remained the issuer/date/sample identity control while standardized quarterly numeric financials were cross-filled from S&P Global Market Intelligence as displayed by StockAnalysis.

The public quarterly history currently exposes Q3 2021 onward, so Q1/Q2 2021 were not silently fabricated. The numeric replay therefore attempted **178** cases from the declared cohort window.

- Companies fetched: **30/30**
- Candidate cases attempted: **178**
- Resolved positive-origin FCF cases: **107**
- Training: **74**
- Chronological holdout: **33**
- Excluded because origin FCF was missing/non-positive: **71**

Each case was labeled from the same-quarter FCF 12 months later. Scenario thresholds were anchored to the SK hynix valuation world's first-year FCF ratios:

- Down anchor: 120 / 258.4 = 0.4644
- Core anchor: 1.0000
- Bull anchor: 330 / 258.4 = 1.2771
- Down/Core midpoint: 0.7322
- Core/Bull midpoint: 1.1385

Features available at the origin only were used: FCF margin, year-over-year FCF growth, revenue growth and operating margin.

## What history says

Among the 107 resolved positive-FCF historical cases:

- Down: **52.3%**
- Core: **19.6%**
- Bull: **28.0%**

Applied mechanically to SK hynix's extreme 2026Q2 financial state, the fitted historical model produced a raw conditional distribution of:

- Down: **60.6%**
- Core: **33.2%**
- Bull: **6.2%**

This raw result is **not authorized for probability weighting** because its predictive skill did not survive chronological holdout validation.

## Holdout gate

On the 33 untouched holdout cases:

- Model Brier score: **0.585691**
- Base-rate Brier score: **0.585862**
- Brier Skill Score: **+0.000293** (~+0.03%)
- 90% paired-bootstrap BSS interval: **-0.171 to +0.148**
- Accuracy: **60.6%**

The Brier improvement is economically and statistically indistinguishable from zero. The bootstrap interval spans materially negative and positive values. Therefore `holdout_trust = 0` and the historical conditional model is not allowed to alter scenario probabilities.

## Consequence

Large-sample history provides a useful **mean-reversion warning** for extreme semiconductor cash-flow regimes, but it does not provide a validated probability mapping strong enough to replace the existing analyst prior.

Therefore the defensible calibrated action is **zero adjustment**: retain `Down/Core/Bull = 20%/60%/20%` until a genuinely predictive historical mapping or sufficient prospective resolved forecast cohort passes the repository calibration gate.
