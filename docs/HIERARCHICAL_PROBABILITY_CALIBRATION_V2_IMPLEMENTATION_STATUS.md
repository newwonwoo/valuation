# Hierarchical Probability Calibration v2 — implementation status

Status: IMPLEMENTED_CI_VERIFIED

Implemented waves:

- Wave A — hierarchy contracts, registry, time-versioned classification boundary and CI validator.
- Wave B — leave-child-out beta-binomial partial pooling, shrinkage caps, child specialization gates and degradation handling.
- Wave C — hierarchical snapshot/certificate lineage, typed probability authorization protocol and runtime loader compatibility.
- Wave D — dependence-aware factor-to-scenario graph with explicit state-table/copula contracts and Frechet bounds; naive independence is unsupported.
- Wave E — append-only normalized calibration fact lake, revision/first-seen replay, reusable metric-change outcome derivation and 30-company semiconductor migration-only seed.

Verification baseline:

- implementation PR #125 valuation-tests #569: 765 passed, 1 existing warning;
- hierarchical registry and semiconductor migration-only validators: PASS;
- PM portfolio integrity and PROJECT_STATUS synchronization: PASS;
- runtime performance budget, OCI regression and installed-wheel runtime: PASS;
- verified-report #107: PASS.

Production status remains unchanged: historical migration data cannot itself authorize production probability weighting. The existing production cohort milestone remains blocked until real prospective forecasts resolve under the first-seen and primary-evidence contracts.
