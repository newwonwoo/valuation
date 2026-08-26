# PM Capacity LIVE_PRIMARY Acceptance Contract

A capacity fix is accepted only when all conditions below are true on one exact commit SHA.

1. A `capacity_manufacturing` route executes `CapacityCommitmentGate` before the LLM Bridge Analyst.
2. Missing commitment or bridge-consumption loaders fail closed before Scenario Build.
3. The LLM receives the frozen typed `CapacityCommitmentAssessment` and cannot define its own completeness boundary.
4. Every Core-required project consumes distinct capacity, CAPEX and ramp Bridges before assumptions compile.
5. Capacity collection requirements include land control, baseline treatment, sizing inputs, ramp, equipment and cancellation/no-active status.
6. A blocked run cannot emit intrinsic value, Freeze token, market comparison or a verified report.
7. Beta and WACC remain separate typed stages; missing providers cannot be replaced by analyst priors.
8. The committed report form exposes run ID, stage trace, Evidence, Capacity, Beta, WACC, Scenario, Valuation, Audit and Freeze identities.
9. Focused integration tests, full pytest, OCI regression and existing CI gates pass.
10. PM acceptance is recorded only after the merged main SHA passes `valuation-tests`.
