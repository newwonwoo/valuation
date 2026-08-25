# Calibration dataset contract

Production probability calibration data must be loaded against a declaration made before evaluation. The declaration fixes:

- forecast class and horizon;
- base rate;
- mapping version;
- dataset version;
- authoritative source reference.

Every forecast and outcome requires an explicit `first_seen_at`. Historical replay may filter what was knowable at a cutoff, but the immutable full-dataset hash does not change with the replay cutoff. A post-hoc cohort, base-rate, mapping, version or source change is rejected.

This contract does not manufacture a production cohort. Until sufficient real resolved history exists, the promotion gate remains CALIBRATING/UNCALIBRATED and no certificate may authorize intrinsic probability weighting.
