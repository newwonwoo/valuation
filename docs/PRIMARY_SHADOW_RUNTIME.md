# PRISM Full PRIMARY_SHADOW Runtime v1.1

Status: executable integration contract for the complete canonical Control Plane stage registry.

## Purpose

The PRIMARY_SHADOW runtime proves that all canonical stages can be dispatched in order without claiming unsupported live capabilities. Implemented units use their typed adapters. Non-applicable methods leave an explicit `SKIPPED_NOT_APPLICABLE` trace. A required but missing capability returns `NOT_IMPLEMENTED` and blocks.

## Canonical path

The runtime loads `config/control_plane_stage_registry.yaml` rather than maintaining a second stage list. Every stage is required to leave one terminal trace.

`Company → State/Learning → Industry Snapshot → Freshness → Segment/DNA → Module Plan/Adaptive Loadout → Primary Evidence → Ledger → Insight/Research/Red Team → Bridge → Compiler/Scenario → Risk/Valuation/Decision Impact/Audit → Freeze → Street/Market → Learning/State/Report`

## Shadow semantics

- A static/manual Collector may stand in for a live DART/IR adapter, but it obeys the same typed EvidenceCollector contract.
- Industry Knowledge and source-health snapshots are versioned shadow inputs.
- `ROCKET_INSIGHT_SCAN` may record a warning when only the mandatory scanner plan is exercised.
- Funding, Beta, WACC, Warranted PER and probability stages may be `SKIPPED_NOT_APPLICABLE` only when the selected exact evaluator does not consume them.
- If Industry DNA requires one of those capabilities, absence is `NOT_IMPLEMENTED`, never a silent skip.
- Audit runs Decision Impact before Freeze. Units without reproducible counterfactual adapters are explicitly `NOT_MEASURABLE`, not zero-impact.
- Street and current price remain unavailable until a same-run Freeze Token exists.

## Research-learning feedback

`LOAD_COMPANY_STATE` combines ordinary company state with prior immutable module-impact history. The canonical `Module Requirement Plan` remains unchanged, while an `AdaptiveResearchLoadout` may schedule optional research as always-on, conditional, sampled or governance-review work.

`SAVE_STATE` first persists the current run's module-impact learning record using the same-run Freeze Token, then stores the ordinary immutable run state and report artifacts. Mandatory scanners and gates are never removed automatically; a historical down-rank signal against a mandatory unit creates a governance-review flag only.

A second run against the same state root must load the prior learning record and expose the previous research-loadout recommendations before compiling its new Module Requirement Plan.

## Extension contract

`PrimaryShadowRuntimeConfig.stage_overrides` is the controlled replacement point for progressively adding live funding/risk/evaluator/signal adapters. An override must return the ordinary typed StageExecutionResult and remains subject to doctrine coverage, audit, decision-impact and freeze rules.

PRIMARY_SHADOW completion is integration evidence, not a claim that the same company has been fully analyzed with fresh live sources. Promotion to `LIVE_PRIMARY` requires live source coverage and exact evaluators for the selected Industry DNA.

## Validation

The full runtime contract is exercised by `tests/test_full_primary_shadow_runtime.py`:

- all 32 canonical stages complete for a normalized-multiple fixture;
- no Expected Value is fabricated while scenario probabilities remain uncalibrated;
- a same-run Freeze Token precedes Street/market access and state persistence;
- the first run writes immutable module-impact learning and the second run loads it;
- a project-finance Industry DNA route blocks specifically at `UPSTREAM_FUNDING_SCAN` when no funding adapter exists.

Repository CI must also pass the Unit Contract Registry validator, the complete pytest suite and the unchanged OCI legacy regression output.
