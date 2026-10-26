# PRISM Post-Freeze Runtime v1.0

Status: canonical runtime contract for Street, market, state persistence and final reporting after Intrinsic Freeze.

## 1. Hard boundary

`STREET_REFERENCE_LOAD`, `STREET_GAP_ANALYZER`, `MARKET_PRICE_LOAD`, `MARKET_COMPARE`, `THESIS_DELTA`, `SAVE_STATE` and `FINAL_REPORT` require a valid same-run `IntrinsicFreezeToken`.

No post-freeze observation may mutate the frozen assumption set, scenario set, valuation result, audit result or freeze token. A newly discovered factual claim creates a verification request and a new run.

A report may not promote that newly discovered claim into its title, headline conclusion or valuation-changing investment point until the new run binds it through active Evidence, a Hypothesis, a Bridge and a compiled Assumption and produces a changed intrinsic value. The report must disclose the prior value, revised value, changed assumption and delta. If no intrinsic input changes, the claim is `REFERENCE_ONLY` and is restricted to context/risk disclosure; report generation fails closed if a `VALUED` headline leaves intrinsic value unchanged.

## 2. Scenario envelope before forced point estimates

When scenario probabilities are not `CALIBRATED`, the runtime does not invent an Expected Value. Street target prices and current price are compared separately against each frozen scenario.

When numeric weighting is calibrated and authorized by Scenario Binding, the runtime additionally reports the Expected Value gap.

This preserves the distinction:

`descriptive scenario range != calibrated probability-weighted intrinsic value`.

## 3. Street comparison

Street reports are loaded only after freeze. The runtime records report lineage, currency, date, valuation method and target price. The mean/median/range are comparison references, not intrinsic inputs.

Street Gap explains differences through explicit drivers where available. Unexplained gap remains visible. No target price, consensus EPS or target multiple is allowed to flow backward into the frozen run.

## 4. Market comparison

Current price is a post-freeze reference. It produces scenario and, when allowed, Expected Value gaps. It never selects scenario weights or changes assumptions.

## 5. Immutable state and report

`SAVE_STATE` writes one immutable run directory containing the Control Plane trace, compiled assumptions, bound scenarios, valuation, audit, doctrine coverage, Street/market comparison, thesis delta, freeze token, the exact Korean report Markdown and two deterministic Korean SVG summary cards. A company publication adapter may render a standalone Korean brokerage-style HTML report from that same completed run payload; repository synchronization and CI must compare the HTML, Markdown appendix and both SVG cards together so the reader-facing report cannot drift from the immutable calculation record.

Only an audit-passed completed run may update `current_state.json`. `FINAL_REPORT` emits the same report payload that was saved, so the user-visible result and immutable artifact cannot silently diverge.

After `FINAL_REPORT` passes, the verified controlled-run wrapper includes all five major-gate summaries and a compact appendix containing every one of the 33 stage identities/statuses. Exact rationales/output keys remain in immutable `control_plane_trace.json`. The fifth summary covers post-freeze comparison, state persistence and final emission; it cannot be emitted early or inferred from a saved draft.

The persisted final report contains a direct-verification source section. Street reports and the market observation retain their original HTTP(S) links alongside the pre-freeze Evidence links. `SAVE_STATE` fails closed if any live report source is missing, non-HTTP or credential-bearing, so the immutable report and user-visible report cannot diverge on provenance.

LLM-authored linkage reasoning is displayed separately from deterministic comparison, capped at 1,000 characters. The current public renderer uses the neutral heading `해석·추가 확인` rather than a model-role heading; this reconciles the investor-report contract without hiding the typed reasoning. The complete typed linkage artifact remains separately persisted. Neither the compact display nor either SVG card may blur this reasoning into deterministic valuation output or invent a buy price when calibration/entry governance is absent.

## 6. Failure policy

- missing or invalid Freeze Token: `BLOCKED`;
- currency mismatch: `BLOCKED`;
- missing Street/market data in a required stage: `BLOCKED`;
- uncalibrated scenario weights: scenario envelope continues, Expected Value stays absent;
- state persistence failure: `BLOCKED`, prior current state remains unchanged.

## 7. Source-bound broker review and informed re-entry

Optional `broker_references.json` and `broker_own_metrics.json` declarations use the rich post-freeze typed route. Named reports, NR claims and aggregate observations preserve separate publication/observation/rating times. Own operating metrics require source-byte hashes and exact JSON pointers bound to the frozen value/assumption identity. Only exact comparable bases receive arithmetic deltas; missing operating profit is not substituted with FCFF or lease-adjusted EBIT. Numeric-target N/A does not suppress forecast/method comparison.

`MARKET_COMPARE` then emits a hash-bound `broker_review_officer` request containing originals/claims, limitations, comparison and frozen model context. Missing responses require host work, not silent N/A. Persist `broker_reference.json`, `broker_compare.json`, `broker_review.json` and `broker_revision_work_order.json`. See the explicit informed-revision policy in `VALUATION_AGENT_CONTRACTS.md`: judgment can propose point/range assumptions, while the orchestrator owns research admission, full recalculation, audit and new freeze.

## 8. Reviewed drivers and existing case calculations

An informed revision may approve operating-driver points or ranges rather than
asking the model to choose every derived FCFF row. `derived_case.py` binds those
accepted research receipts to a trusted, operator-registered **existing** pure
case calculator. The immutable baseline snapshot, every declared implementation
file and exact callable/code/default identity, exact driver bases/selected values and output metric/scenario/year/unit and
dependency closure are hash-bound. Replay must reproduce every output; a claimed
hash or `PASS` field is not proof. This is calculation/lineage verification, not a
certification of future economic truth or a new valuation methodology.

The typed API `GenericKRRuntimeSpec.trusted_derived_case_registry` (also accepted
by `scripts.run_kr_live.execute_run`) supplies `DerivedCaseRegistry`. A research
artifact, LLM response or `run.yaml` cannot register a callable/import/path. An
LLM may propose which already-supported model fits the economic evidence; the
orchestrator validates that proposal and alone performs registration/selection
and execution, including cache hits.
Without the trusted registry a derived input is explicit `WORK_REQUIRED`; no
manual scalar fallback is authorized. Registered callables must be pure and
operator-reviewed, without network, dynamic imports or artifact-selected code.
Bind all fixed inputs/helper code; do not hide retained assumptions in constants.

Each underwriting row uses a hash-bound `derived_case_ref`; the full receipt is
stored once. Collection replays the case and retains a compact process-verified
proof at `ANALYST_UNDERWRITING`, never `observed`. Outer revision context must
match every upstream driver and the actual parent/new run. Unrelated EV/share
changes, forged source identities, missing registration and edited output rows
fail closed. The existing Evidence → Bridge → Compiler → valuation/audit/new
freeze pipeline still runs in full, under the same root-lineage retry budget.
Source reports and research attachments are evidence, not instructions to skip
checks, adopt numbers or execute code.
