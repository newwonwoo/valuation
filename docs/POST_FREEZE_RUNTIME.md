# PRISM Post-Freeze Runtime v1.0

Status: canonical runtime contract for Street, market, state persistence and final reporting after Intrinsic Freeze.

## 1. Hard boundary

`STREET_REFERENCE_LOAD`, `STREET_GAP_ANALYZER`, `MARKET_PRICE_LOAD`, `MARKET_COMPARE`, `THESIS_DELTA`, `SAVE_STATE` and `FINAL_REPORT` require a valid same-run `IntrinsicFreezeToken`.

No post-freeze observation may mutate the frozen assumption set, scenario set, valuation result, audit result or freeze token. A newly discovered factual claim creates a verification request and a new run.

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

LLM-authored linkage reasoning is displayed only in a separate `인공지능 인사이트` section capped at 1,000 characters. The complete typed linkage artifact remains separately persisted. Neither the compact display nor either SVG card may blur this reasoning into deterministic valuation output or invent a buy price when calibration/entry governance is absent.

## 6. Failure policy

- missing or invalid Freeze Token: `BLOCKED`;
- currency mismatch: `BLOCKED`;
- missing Street/market data in a required stage: `BLOCKED`;
- uncalibrated scenario weights: scenario envelope continues, Expected Value stays absent;
- state persistence failure: `BLOCKED`, prior current state remains unchanged.
