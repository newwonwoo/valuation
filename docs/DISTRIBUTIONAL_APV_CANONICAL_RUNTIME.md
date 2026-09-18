# Canonical distributional APV runtime

## Purpose

Promote `capacity_yield_levered/driver_distributional_apv` from a standalone diagnostic calculation into a company-level primary valuation route inside the canonical 33-stage LIVE_PRIMARY workflow.

## Runtime contract

1. Industry DNA selects economic capabilities. Company name, ticker, target ID and industry label never select formulas.
2. `VALUATION_METHOD_INTENT` resolves segment evaluators separately from a company-level primary aggregator. A segment may be delegated to the primary aggregator instead of being misclassified as a capability gap.
3. Same-run Beta/WACC receipts are generated before valuation and bound to the distributional execution spec inside `DETERMINISTIC_VALUATION`.
4. Operating drivers, operating cash flow, debt/lease/refinancing/equity/asset-sale paths, distress waterfall, APV and old-shareholder payoffs are calculated inside the canonical valuation stage.
5. Calibrated pathwise distributions may expose quantiles and a dated-payoff entry quantile only when the distribution route is authorized.
6. Uncalibrated event priors require at least two distinct source-bound probability vectors. They expose an expected-value interval and, only when complete dated payoff models are authorized, a worst-prior expected-return entry ceiling. They never expose a calibrated success probability or one probability-weighted target.
7. `AUDIT_GATE` replays the exact same risk-bound typed execution spec and requires the complete result and immutable lineage to match.
8. The intrinsic valuation envelope binds distribution hash, route authorization, entry policy and any ambiguity/payoff-model hashes into `valuation_hash`. Freeze therefore seals the distributional value before Street/current-price access.
9. Street and current-market comparisons remain post-freeze and read-only. Reverse DCF is withheld for the distributional primary route.
10. SAVE_STATE persists the audited distributional result, freeze token, decision-impact result, probability forecast history, research-learning history and a reader-facing investment report.

## Fail-closed conditions

- Missing distributional execution inputs for a selected primary aggregator.
- Missing same-run required Beta/WACC receipts.
- Duplicate or incomplete path/model-case identities.
- Structural payoff horizon outside the entry horizon.
- Missing calibrated distribution authorization for pathwise quantiles.
- Fewer than two distinct probability vectors for governed-prior ambiguity.
- Missing/partial payoff model cases for robust entry.
- Any audit replay mismatch or envelope/hash mismatch.
- Any attempt to use pre-freeze Street/current-price data.
- Any attempt to present governed-prior outputs as calibrated success probabilities.

## Acceptance sequence

1. Focused runtime tests.
2. Existing distributional/APV/ambiguity tests.
3. Full `valuation-tests` regression.
4. Existing strict LIVE_PRIMARY company workflows.
5. Verified-report workflow.
6. Only after those pass, change the method capability from `PARTIAL_RUNTIME` to `RUNTIME_READY` and rerun the same gates.
