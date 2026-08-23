# PRISM Full PRIMARY_SHADOW Runtime v1.0

Status: executable integration contract for the complete canonical Control Plane stage registry.

## Purpose

The PRIMARY_SHADOW runtime proves that all canonical stages can be dispatched in order without claiming unsupported live capabilities. Implemented units use their typed adapters. Non-applicable methods leave an explicit `SKIPPED_NOT_APPLICABLE` trace. A required but missing capability returns `NOT_IMPLEMENTED` and blocks.

## Canonical path

The runtime loads `config/control_plane_stage_registry.yaml` rather than maintaining a second stage list. Every stage is required to leave one terminal trace.

`Company → State/Learning → Industry Snapshot → Freshness → Segment/DNA → Module Plan/Adaptive Loadout → Primary Evidence → Ledger → Insight/Research/Red Team → Bridge → Compiler/Scenario → Risk/Valuation/Decision Impact/Audit → Freeze → Street/Market → State/Learning/Report`

## Shadow semantics

- A static/manual Collector may stand in for a live DART/IR adapter, but it obeys the same typed EvidenceCollector contract.
- Industry Knowledge and source-health snapshots are versioned shadow inputs.
- Prior immutable module-impact history is loaded at `LOAD_COMPANY_STATE` and can alter only the next-run research loadout, never assumptions or valuation formulas.
- `ROCKET_INSIGHT_SCAN` may record a warning when only the mandatory/learned scanner plan is exercised.
- Funding, Beta, WACC, Warranted PER and probability stages may be `SKIPPED_NOT_APPLICABLE` only when the selected exact evaluator does not consume them.
- If Industry DNA requires one of those capabilities, absence is `NOT_IMPLEMENTED`, never a silent skip.
- Audit runs Decision Impact before Freeze. Units without reproducible counterfactual adapters are explicitly `NOT_MEASURABLE`, not zero-impact.
- Street and current price remain unavailable until a same-run Freeze Token exists.
- `SAVE_STATE` persists both the immutable valuation run and the module-impact learning record; failure is compensated so an incomplete run is not promoted.

## Extension contract

`PrimaryShadowRuntimeConfig.stage_overrides` is the controlled replacement point for progressively adding live funding/risk/evaluator/signal adapters. An override must return the ordinary typed StageExecutionResult and remains subject to doctrine coverage, audit, decision-impact and freeze rules.

PRIMARY_SHADOW completion is integration evidence, not a claim that the same company has been fully analyzed with fresh live sources. Promotion to `LIVE_PRIMARY` requires live source coverage and exact evaluators for the selected Industry DNA.
