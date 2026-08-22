# PRISM Decision Impact & Research Sensitivity Layer v1.0

Status: canonical measurement contract for deciding whether Gates, scanners and modules earn their research cost.

## 1. Why this exists

A module can be intellectually interesting yet irrelevant to the final decision. Repeatedly researching such a module wastes time and tool calls. Conversely, a safety Gate can have zero ordinary value delta yet remain essential because it prevents invalid valuation states.

Therefore module utility is not measured by "did we run it?" or by numeric value delta alone.

Every active module/scanner/gate is evaluated on four axes:

1. **Value Impact** — did intrinsic value move materially?
2. **Decision Impact** — did route, method, status, conclusion, assumption eligibility or block/unblock state change?
3. **Guardrail Impact** — would removing the Gate permit an invalid or prohibited state?
4. **Research Cost** — how much evidence-search/review effort did it consume?

Timing is tracked separately because many infrastructure/project signals matter by moving revenue/COD timing rather than terminal economics.

## 2. Mandatory impact trace

Every active module must register a `ModuleImpactTrace`.

A normal research module must connect to at least one of:
- affected compiled assumptions;
- affected decisions;
- economic path IDs;
- final output references.

A pure guardrail may declare `guardrail_only=true`.

Research that produces none of those paths is not allowed to hide as "useful context". It must be marked context-only and down-ranked, or removed from the mandatory loadout.

This does not mean every discovered fact must change valuation. It means the reason for researching the module must be falsifiable.

## 3. Counterfactual protocol

The canonical run is the baseline.

For each module, construct an auditable counterfactual that neutralizes only that module while preserving the same evidence snapshot and all unrelated assumptions where possible.

Examples:

- **Scanner ablation**: omit one signal family and recompile affected assumptions.
- **Gate counterfactual**: disable one Gate and test whether an invalid state becomes possible.
- **Route counterfactual**: replace one routing contribution and test model/method/status changes.
- **Timing shift**: remove or stress a permit/interconnection/delivery signal and compare timing.
- **Numeric perturbation**: move the module-derived driver through an evidence-backed range and rerun the deterministic evaluator.

Target current price, target price and Street consensus are forbidden from selecting the counterfactual or perturbation range.

## 4. Impact classifications

`GUARDRAIL_CRITICAL`
: Removing the module permits a prohibited/invalid state. Keep regardless ordinary value delta.

`DECISION_MATERIAL`
: Route, valuation method, completion/block status, final conclusion or equivalent decision changes.

`VALUE_MATERIAL`
: Intrinsic value changes beyond the configured threshold.

`TIMING_MATERIAL`
: A material timing conclusion changes beyond the configured threshold.

`ASSUMPTION_ONLY`
: A compiled assumption changed, but observed final value/decision impact remains below threshold in this run.

`LOW_OBSERVED_IMPACT`
: No material value, timing, route, method, status or conclusion effect observed.

`INCONCLUSIVE`
: The counterfactual cannot produce a comparable outcome.

These are observed-run classifications, not permanent labels on the module.

## 5. Numeric sensitivity

When a module changes a numeric driver, use a three-point or fuller deterministic perturbation:

`low evidence-backed input → base compiled input → high evidence-backed input`

and record:

- low/base/high input;
- low/base/high intrinsic value;
- downside/upside value percentage;
- monotonicity versus the expected economic direction.

If the sign is economically wrong, that is a model/audit finding before it is a research-utility conclusion.

Monte Carlo is not required for this layer. Deterministic perturbation is enough to establish whether a driver matters.

## 6. Adaptive research deployment

Research intensity is learned from **applicable runs only**.

- `ALWAYS`: the module repeatedly has material decision/value/timing impact.
- `CONDITIONAL`: the module matters intermittently; activate when its trigger conditions are present.
- `SAMPLE_ONLY`: history is insufficient, or low impact but cheap enough for occasional validation.
- `RETIRE_CANDIDATE`: enough applicable observations show no material impact while research effort remains high.
- `KEEP_GUARDRAIL`: mandatory safety/audit Gate; never retire merely because ordinary value delta is zero.

Known non-applicable research that was still performed is direct avoidable waste.

A `RETIRE_CANDIDATE` is not deleted automatically. The Control Plane proposes a loadout change, regression/backtest evidence is reviewed, and ordinary user/canonical promotion governance applies.

## 7. Avoiding false pruning

Do not prune a module solely because:

- it rarely triggers but guards a catastrophic invalid state;
- the current sample is too small;
- its effect is primarily timing rather than terminal value;
- it changes valuation eligibility rather than value magnitude;
- it is required to prove a negative or preserve auditability;
- a correlated module captured the same economic path in the tested run.

Where two modules repeatedly produce the same impact path, test joint and leave-one-out ablations before declaring either redundant.

## 8. Relationship to Control Plane

The Control Plane must eventually maintain, for every mandatory module/scanner/gate:

- applicability;
- activation status;
- expected impact path;
- actual `ModuleImpactTrace`;
- counterfactual/sensitivity result when measurable;
- research effort;
- current research-intensity recommendation.

This creates a feedback loop:

`Industry DNA → Module Loadout → Research → Decision Impact → Research ROI → next-run Loadout`

The feedback loop may change *what gets researched next time*. It may not rewrite the current frozen intrinsic value, and it may not use market price to decide which module "worked".

## 9. Initial implementation scope

`src/valuation_engine/decision_impact.py` provides:
- counterfactual outcome comparison;
- guardrail-critical detection;
- numeric three-point sensitivity;
- impact-trace validation;
- research-effort recording;
- repeated-run research-intensity recommendations;
- direct non-applicable research-waste detection.

The current generic live engine is not yet automatically rerunning every module ablation. This layer is the deterministic measurement contract that the Control Plane/orchestrator will call as live evaluators are migrated from legacy/shadow to live-primary.
