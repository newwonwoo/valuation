# PRISM Verified Controlled-Run Report

- Run ID: `{{ run_id }}`
- Execution mode: `LIVE_PRIMARY`
- Run status: **{{ VERIFIED_FROZEN | INCOMPLETE | BLOCKED }}**
- Attestation hash: `{{ attestation_hash }}`

## Verification

- Checks: **{{ passed_checks }}/{{ total_checks }} PASS**
- Canonical stages: **{{ terminal_stage_count }}/33 terminal traces**
- Failed checks only: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | major_gate_reporting_contract | major_gate_delivery | direct_source_links | none }} — {{ detail }}`

## Frozen Identity Chain

- Evidence: `{{ ledger_snapshot_hash }}`
- Assumptions: `{{ assumption_set_hash }}`
- Scenarios: `{{ scenario_set_hash }}`
- Valuation: `{{ valuation_hash }}`
- Audit: `{{ audit_hash }}`
- Intrinsic Freeze: `{{ freeze_token_hash }}`
- Auxiliary bindings: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | NOT_APPLICABLE }}`

## Major Gate Summaries

### {{ ordinal }}. {{ title }} — {{ STATUS }} ({{ completed/expected }})

- Result: `{{ decisive_result }}`
- Risk: `{{ residual_risk }}` · Next: `{{ next_action }}`

## Final Report Delivery Contract

- Main body editorial target: 3–4 pages
- Audit appendix editorial target: 1–2 pages
- Combined editorial cap: 6 pages
- Typography: body ≥ 13pt, primary heading ≥ 22pt, section heading ≥ 18pt; dense wide tables forbidden.
- Mandatory: every claim source is mapped to a direct HTTP(S) original link in `Sources — Direct Verification`.

## Compact Audit Appendix — 33-Stage Trace

- **{{ gate_id }}:** `{{ stage_number }} {{ stage }}={{ status }}` · …
- Exact rationales and output keys remain in the immutable `control_plane_trace.json` artifact.

## Persisted Research Report

{{ immutable_saved_final_report_including_sources_direct_verification }}
