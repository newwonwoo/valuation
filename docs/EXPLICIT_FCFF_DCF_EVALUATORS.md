# Exact Explicit FCFF DCF Evaluators v1.0

Status: deterministic `PARTIAL_LIVE` evaluator family for explicit segment FCFF paths.

## 1. Purpose

Many Industry DNA methods differ in how operating drivers produce revenue, margin, working capital, CAPEX and ultimately FCFF. Once those driver-specific assumptions have already been compiled into an explicit FCFF path, the final discounting mechanics can share one deterministic evaluator contract.

This does **not** create a generic DCF fallback.

Each usable model must still be registered with an exact:

`ModelKey(archetype, method, version)`

and may use its own assumption-key prefix and forecast horizon.

Examples of exact registrations include:

- `capacity_manufacturing / driver_dcf / cap-v1`;
- `recurring_subscription / arr_fcf_dcf / subscription-v1`;
- `metered_usage_network / usage_driver_dcf / cloud-v1`;
- `transaction_marketplace / gmv_take_rate_dcf / marketplace-v1`;
- `consumer_unit_economics / unit_economics_dcf / retail-v1`.

The driver models remain different upstream. The shared evaluator only discounts their already-compiled explicit FCFF output.

## 2. Required assumptions

For an evaluator with horizon `N` and optional prefix `segment_`, the Bound Scenario must contain:

- `segment_fcff_year_1` … `segment_fcff_year_N`;
- `segment_terminal_growth`;
- `segment_terminal_roic`.

All FCFF measures must be one convertible money unit. Terminal growth and terminal ROIC must be ratios.

The terminal state is validated through the existing WACC terminal-consistency gate:

- WACC must exceed terminal growth;
- terminal ROIC must be positive;
- implied reinvestment `g / ROIC` must remain in `[0, 1]`.

A Gordon terminal value requires positive final-year FCFF. A loss-making or finite-life segment needs a different exact evaluator or an explicit finite-horizon treatment rather than a fabricated terminal value.

## 3. Live WACC binding

`live_fcff_dcf_registry_loader` requires a `LiveWACCStageResult` from the same run. Callers cannot inject an unrelated discount rate directly into the Stage adapter.

The loader:

- rejects target current price, target market capitalization, target price, target multiple and target-company Street references pre-freeze;
- reads the deterministic live WACC result;
- constructs exact evaluators for declared registrations;
- records the WACC snapshot in the segment discount path.

The shared WACC source is scoped to each segment's value path (`wacc:<snapshot>:<segment>`). This preserves source lineage while preventing SOTP from falsely treating a legitimate common discount-rate input as a duplicated segment value contribution.

## 4. Exact registry, no fallback

A runtime registry may contain several exact DCF evaluators plus explicitly registered normalized-multiple evaluators. It never interprets an unknown method as “some DCF.”

If a Company Valuation Plan requests an unregistered `ModelKey`, the stage returns `NOT_IMPLEMENTED` and enters the ordinary Capability Gap process.

A duplicate exact registration is invalid. Different segments may use different versions or assumption prefixes even when they share an archetype/method family.

## 5. Runtime registry loading

`deterministic_valuation_adapter` now accepts exactly one of:

- a static `EvaluatorRegistry`; or
- a same-run `registry_loader(context)`.

Static behavior remains backward compatible. Runtime loading is necessary when the evaluator must bind a same-run WACC, scenario-specific source snapshot or other typed upstream contract.

The loader itself cannot fetch market data, interpret evidence or compile assumptions.

## 6. Valuation and SOTP path

For each scenario and segment:

1. load compiled FCFF measures;
2. discount years 1…N with the same-run WACC;
3. validate and calculate terminal value;
4. return an enterprise-value `SegmentValuation` with economic path IDs;
5. apply explicit EV-to-equity adjustment and ownership in SOTP;
6. divide company equity value by compiled diluted shares.

All EV-to-equity adjustments remain explicit, including an explicit zero. Debt/NCI/parent adjustments may not be silently embedded in the DCF evaluator.

## 7. What this does not cover

This evaluator family is not suitable by itself for:

- finite-life project NPV with construction/drawdown/COD mechanics;
- clinical or other probability-weighted asset rNPV;
- reserve-depletion NPV;
- NAV/appraisal models;
- financial-institution residual-income/PB-ROE/DDM models;
- title/cohort NPV;
- negative-terminal-FCFF businesses;
- methods whose cash flow belongs directly to equity rather than enterprise value.

Those require separate exact evaluators with their own assumption and audit contracts.

## 8. Decision Impact and sensitivity

The evaluator carries every explicit FCFF path, terminal path and segment-scoped WACC path into `SegmentValuation.economic_path_ids`.

This allows automatic ablation and numeric sensitivity to test whether:

- operating assumptions affect FCFF and value;
- WACC changes value in the expected direction;
- terminal growth/ROIC materially drive value;
- a scanner or Gate actually reaches a compiled FCFF path;
- an expensive research module has no observable conclusion impact.

The evaluator must never use target market price to judge whether a sensitivity result is “right.”
