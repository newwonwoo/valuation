# PR #184 review remediation

Reviewed base: `e4bf3de6c085663708c6083c635b14e7b65ceec9`.
All five open findings remain reachable in that code. The committed Korean Air
spec already fails structural-model qualification; that rejection does not make
the generic qualified-input path safe.

| Finding | Reproduction at reviewed base | Correction |
|---|---|---|
| P1 canonical audit | A qualified fixture produces `AUDITED_FINAL` outside the orchestrator. | The standalone builder emits only `DIAGNOSTIC_ONLY`, with audit `passed=false`, no freeze token/hash, no final-report alias, no latest-pointer update and no supersession. It never reads market/broker inputs. |
| P1 source artifacts | Replacing snapshot scenario values while preserving its old receipts is accepted. | Require an independently pinned source bundle manifest, hash every referenced file, and cross-check original valuation, blocking audit findings, run status, freeze receipts, target, ticker, cutoff, units and scenario rows. Missing originals block output. |
| P1 asset sale | A 10 cash sale with no disposal ledger retains the full 100 non-operating asset balance; terminal payoff is 310. | Match each non-core financing sale by period and gross proceeds to explicit disposed present/horizon asset values. Remove those values, not an assumed sale-price proxy. The test with a 30 horizon asset removal gives 280, including sale cash once. |
| P1 structural break | Adding a merger to an 80-quarter panel leaves posterior, parameter hash and authorization identical. | Fit and rolling-score only observations from the latest break onward. Break date denotes the first comparable quarter. Include declared breaks and perimeter in the parameter hash. Fewer than 32 current-regime observations block fitting. |
| P2 generic report | The template embeds Korean Air identity, branch IDs and policy constants. | Diagnostic text and SVG use supplied company, computed branch rows, volatility, claims and entry policy. No hardcoded company investment conclusion. |

## Boundaries

This patch does not integrate the experimental distribution into canonical
orchestration and does not certify an investment value or entry price. A caller
must supply `source_bundle_manifest` and `source_bundle_manifest_sha256` in its
spec, pointing to the **original complete** `kr-live-report-bundle/v1` archive.
The pinned hash is the independently retained archive-manifest receipt; computing
one over fabricated replacement files is not source recovery. The original
source audit remains distinct from audit of any derived calculation.

The committed Korean Air snapshot alone is insufficient. Replaying its builder
now stops with `source bundle manifest and pinned SHA256 are required` and
creates no output. With verified source inputs, its existing structural-model
qualification rejection still applies. Earlier standalone distribution bundles
and the old PR-description price figures must not be treated as newly certified
results. Existing archived source reports are not rewritten.

Non-core disposal values are on the same **pre-sale** basis as the supplied
non-operating balances. Missing/mismatched/excess removals fail closed. Operating
asset sales require rebuilt operating cash-flow paths; distressed sale paths
require a reconciled recovery-asset bridge and remain unsupported here. No
sale-price-equals-asset-value assumption is introduced.

## Verification

The focused source/diagnostic/APV/structural-break tests pass (49 tests). Tests
cover source tampering, snapshot identity, unaudited latest-pointer preservation,
changed company/branch/policy inputs, sale costs and disposal balances, and both
fit and OOS exclusion of pre-break observations. The previously qualified
Korean Air Merton-overlay rejection is preserved after source verification.

The revision impact plan was validated with `revision_orchestration.py` and the
Unit Contract registry (plan hash
`4b14510f090f8bc8ee8378dc360049a763e08a28f3190ea3ac78bf58c0334e4f`).

Full-suite check: 2,028 passed; 11 failed. Three failures are the existing
`compare_runs` clean-HEAD receipt checks, which intentionally reject uncommitted
runtime edits. Eight tunnel-launcher tests reject this container's `overlay`
filesystem; the same eight fail on unmodified `e4bf3de6`. The filesystem security
gate was not relaxed. The three clean-HEAD checks pass after committing (3 passed, 23 deselected).
OCI regression completes with all blocking audit findings passed. Unit Contract
and work-claim registry validators pass.

## Dated-payoff follow-up

The later `dated-payoff-ambiguity-report/v2` route does not promote the retired
standalone structural diagnostic. It replaces that calculation with explicit
five-year operating cash-flow, debt, lease, refinancing, asset-sale, dilution
and distress-waterfall paths. Three complete financing cases are crossed with
all three declared probability vectors; legal shareholder cash flows are
discounted at their actual payment dates. The route policy authorizes only an
expected-value interval and a robust entry ceiling. It continues to forbid a
single target price and a calibrated success-probability claim.

The source-artifact correction above remains binding. Before the new route can
run, it verifies the independently pinned `kr-live-report-bundle/v1` manifest,
every archived file receipt, the completed source run and blocking audit, the
freeze token, target/cutoff/units, and exact signed scenario values. The pinned
manifest is also included in the intrinsic source hash and the new audit and
bundle receipts. Market prices and broker targets remain unavailable until the
dated-payoff result, route authorization and replay audit have been hash-frozen.
