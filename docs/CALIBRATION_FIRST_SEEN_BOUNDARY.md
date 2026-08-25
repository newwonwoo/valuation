# Calibration First-Seen Boundary

`ProbabilityForecast.issued_at` is the economic/model issuance timestamp. It is not sufficient for historical calibration replay because a revision can be imported later with an old issuance date.

The calibration ledger therefore records `first_seen_at`, the first time that exact revision became knowable to PRISM.

Rules:

- initial legacy forecasts may omit `first_seen_at`; their issuance time is the compatibility boundary;
- every superseding revision must declare an explicit timezone-aware `first_seen_at`;
- `first_seen_at` cannot precede issuance or the prior revision's first-seen boundary;
- historical snapshots include only forecasts whose issuance and first-seen timestamps are both at or before the cutoff;
- a revision first seen after a historical cutoff cannot suppress the revision that was terminal at that cutoff;
- outcomes have the same optional first-seen boundary so later backfilled resolutions cannot enter an earlier snapshot;
- ledger serialization persists both first-seen timestamps and `replay_as_of()` restores only the history knowable by the cutoff;
- the visible revision identity and first-seen timestamps enter the calibration snapshot hash.

This boundary prevents hindsight leakage without changing the economic issuance date or treating forecast revisions as independent samples.
