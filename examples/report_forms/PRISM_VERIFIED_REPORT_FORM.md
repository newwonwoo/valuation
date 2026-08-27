# PRISM Verified Controlled-Run Report

- Run ID: `{{ run_id }}`
- Execution mode: `LIVE_PRIMARY`
- Run status: **{{ VERIFIED_FROZEN | INCOMPLETE | BLOCKED }}**
- Attestation hash: `{{ attestation_hash }}`

## Execution Attestation

| Check | Result | Detail |
|---|---:|---|
| `canonical_stage_sequence` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |
| `beta_wacc_same_run_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |
| `capacity_core_consumption_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |
| `broker_research_primary_verification_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |
| `freeze_hash_binding` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |

## Immutable Run Identities

| Artifact | Hash |
|---|---|
| Evidence Ledger | `{{ ledger_snapshot_hash }}` |
| Assumption set | `{{ assumption_set_hash }}` |
| Scenario set | `{{ scenario_set_hash }}` |
| Beta | `{{ beta_snapshot_hash_or_not_applicable }}` |
| WACC | `{{ wacc_snapshot_hash_or_not_applicable }}` |
| Capacity assessment | `{{ capacity_commitment_assessment_hash }}` |
| Capacity consumption | `{{ capacity_bridge_consumption_hash_or_not_applicable }}` |
| Capacity scenario | `{{ capacity_scenario_binding_hash_or_not_applicable }}` |
| Capacity valuation | `{{ capacity_valuation_binding_hash_or_not_applicable }}` |
| Capacity audit | `{{ capacity_audit_hash }}` |
| Broker pre-freeze | `{{ broker_research_snapshot_hash_or_not_applicable }}` |
| Broker audit | `{{ broker_research_audit_hash_or_not_applicable }}` |
| Valuation | `{{ valuation_hash }}` |
| Audit | `{{ audit_hash }}` |
| Intrinsic Freeze | `{{ freeze_token_hash }}` |

## Stage Trace

| # | Stage | Status | Blocking | Rationale |
|---:|---|---|---:|---|
| 1 | `{{ stage }}` | `{{ status }}` | `{{ YES_OR_NO }}` | `{{ rationale }}` |

## Persisted Research Report

{{ immutable_saved_final_report }}
