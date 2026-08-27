# PRISM Probability Calibration Runtime v1.0

Status: `PARTIAL_LIVE` runtime contract. Scoring/promotion and production forecast capture are implemented; enough real resolved cohort history and validated mapping tables do not yet exist.

## 1. Purpose

A label such as `CalibrationStatus.CALIBRATED` is not enough to authorize probability-weighted intrinsic value in `LIVE_PRIMARY`.

The live path is:

`ProbabilityForecast → append-only event ledger → primary-evidence outcome resolution → cohort metrics → promotion gate → CalibrationSnapshot → CalibrationCertificate → Scenario Binding`

Only a hash-bound `CalibrationCertificate` with status `CALIBRATED` may enable numeric scenario weighting in `LIVE_PRIMARY`.

## 2. Event ledger

A forecast fixes before resolution:

- independent `event_key`;
- forecast class and horizon;
- event definition;
- probability and displayed band;
- issuance time and evaluation deadline;
- evidence-snapshot hash and model version;
- resolution rule and primary-source policy.

Forecasts and outcomes are immutable. A revision creates a new forecast with `supersedes_id`; revisions of one event are not independent samples. Only the terminal revision is resolved/scored.

An audit-passed `LIVE_PRIMARY` run may also persist declared binary forecasts through `ProbabilityForecastHistoryStore`. The run record fixes the raw pre-resolution probability, Evidence snapshot, event definition, deadline and resolver contract. A later run may change the probability only by creating a superseding revision; it may not redefine the company, hypothesis, event, deadline or resolution contract.

Production outcome ingestion requires an explicit `first_seen_at` and active primary Evidence with a directly verifiable HTTP(S) source. Analyst assertions, market-comparison Evidence and synthetic outcomes are rejected. The outcome record preserves the source identity and URL alongside the immutable resolution.

`AMBIGUOUS` and `CENSORED` outcomes are not silently converted to success/failure. They are excluded from the binary scoring numerator and retained in the censoring/ambiguity rate.

## 3. Metrics

For each predeclared `forecast_class | horizon` cohort the runtime records:

- Brier score;
- Brier Skill Score against a predeclared cohort base rate;
- log loss;
- fixed-bin reliability table;
- fixed-bin Expected Calibration Error (ECE);
- outcome coverage;
- ambiguous/censored rate;
- company, quarter and displayed-band sample breadth;
- chronological out-of-sample Brier Skill Score windows.

Market price, target price and valuation error are not calibration outcomes.

## 4. Promotion gate

Version `1.0` defaults follow the canonical live-validation contract:

- at least 200 independent resolved events;
- at least 20 companies;
- at least 8 quarters;
- at least 30 resolved observations in every used displayed band;
- at least two chronological out-of-sample windows and every supplied OOS Brier Skill Score positive;
- fixed-bin ECE at or below 0.08;
- ambiguous/censored outcomes at or below 10%.

The base rate is deliberately **not** global. `config/probability_calibration_policy.yaml` requires each production cohort to pre-register its own base rate. With no cohort policy/history, the system remains `UNCALIBRATED`/`CALIBRATING`; it does not invent 20/50/30 scenario weights.

## 5. Lifecycle

- `UNCALIBRATED`: no effective resolved sample.
- `CALIBRATING`: observations exist but promotion gate is not met.
- `CALIBRATED`: all promotion conditions pass; certificate may be issued.
- `DEGRADED`: a previously calibrated cohort fails the current versioned gate or out-of-sample validation.

A degraded snapshot cannot issue a weighting certificate.

## 6. Scenario Binding

`PRIMARY_SHADOW` preserves backward-compatible scenario-weight tests.

`LIVE_PRIMARY` is stricter. When probability assumptions are marked `CALIBRATED` and would otherwise produce numeric scenario weighting, Scenario Binding additionally requires:

1. `ScenarioBindingSpec.calibration_cohort_key`;
2. a typed `CalibrationCertificate`;
3. certificate status `CALIBRATED`;
4. exact cohort match;
5. calibrated probabilities sum to one.

The certificate snapshot hash enters the `BoundScenarioSet` hash chain so a later calibration update cannot silently mutate an in-flight run.

## 7. Maintenance and next data work

`src/valuation_engine/probability_calibration.py` is the scoring/promotion source of truth. `src/valuation_engine/probability_adapter.py` loads a snapshot/certificate into a pre-Scenario runtime context. `src/valuation_engine/scenario_binding.py` enforces certificate consumption.

Remaining `PARTIAL_LIVE` work is real elapsed-time evidence, not permission or writer logic: allow the newly captured forecasts to reach their declared deadlines, resolve them only from qualifying primary Evidence, accumulate the required forecast-class/horizon cohorts, predeclare cohort base rates, version mappings, run chronological holdouts and collect forward validation before any cohort is promoted for production weighting.
