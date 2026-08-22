# Consensus Reconciliation — Post-Freeze Pointer

Canonical consensus/Street reconciliation is the post-`INTRINSIC_VALUE_FREEZE` `Street Gap Analyzer` in `docs/V04_ROCKETSLA_EXTENSION.md` and `src/valuation_engine/street.py`. Do not use a trimmed-average target price or consensus multiple as an intrinsic-value anchor.

If Street exposes a potentially missing fact, verify it with primary/independent evidence and start a new valuation run rather than backsolving the frozen run.
