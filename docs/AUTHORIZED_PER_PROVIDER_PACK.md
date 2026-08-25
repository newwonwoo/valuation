# Authorized PER Provider Pack

This provider pack closes the source layer between annual filing EPS, model-normalized forward EPS and the existing hierarchical Warranted PER runtime without admitting target-company Street data before Intrinsic Freeze.

## Target normalized forward EPS

1. Accept annual OpenDART filing EPS only (`report_code=11011`). Interim Q1/H1/Q3 EPS is never silently promoted to a forward EPS base.
2. Apply only explicit per-share normalization adjustments with Evidence IDs and source provenance.
3. Select an explicit normalization method (`latest_annual_adjusted` or `three_year_median_adjusted`).
4. Project exactly one business year using an explicit non-Street growth rate with its own Evidence IDs and source reference.
5. Return a `NormalizedForwardEPSCandidate`; the provider does not inject it directly into valuation assumptions.
6. The runtime loader verifies that the compiled `normalized_forward_eps` assumption exactly equals the candidate and carries every provider Evidence ID.

This preserves the Evidence → Bridge → Assumption Compiler boundary.

## Peer residual hierarchy

Peer market references are permitted because they are not target-company market anchors. Each peer source carries:

- peer market price and source,
- peer normalized forward EPS and source,
- independently modeled peer fundamental forward PER,
- one normalized as-of date and methodology.

The provider converts these into market-minus-fundamental residual inputs for the existing L1 → L4 Warranted PER hierarchy. The target company cannot enter its own peer pool and one peer cannot appear at multiple hierarchy levels.

## Isolation

Target-company consensus EPS, target price, current market price and target market capitalization remain forbidden pre-freeze. The provider pack contains no target Street loader and cannot bypass the existing `LivePERInputs` / EvidenceLedger gates.
