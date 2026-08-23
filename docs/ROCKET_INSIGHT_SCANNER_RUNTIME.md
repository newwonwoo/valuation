# Rocket Insight Scanner Runtime v1.0

Status: typed PARTIAL_LIVE runtime contract for `ROCKET_INSIGHT_SCAN`.

## Purpose

The Module Requirement Plan already determines the mandatory research scanners for each routed Economic Archetype. This runtime turns that loadout into an executable, auditable stage rather than leaving scanner names as planning metadata.

`Industry DNA → Module Requirement Plan → exact mandatory scanner IDs → typed scanner handlers → traceable findings → LLM Staff / Red Team / Bridge`

The dispatcher does not interpret evidence by itself and does not commit assumptions. Each scanner-specific handler remains responsible for its economic reasoning within the typed contract.

## Contracts

### ScannerRequest

Every handler receives:

- exact `scanner_id`;
- target and run IDs;
- routed segment IDs;
- required KPIs and evidence metrics;
- active kill conditions;
- active Evidence IDs from the run's `EvidenceLedger`.

### ScannerFinding

A finding may contain:

- supporting and contradicting Evidence IDs;
- missing-evidence requests;
- affected variables;
- economic-path IDs;
- observed kill-condition hits;
- reinforcement-scanner requests.

A PASS/WARNING finding without any impact path is invalid. A mandatory scanner may not hide as `NOT_APPLICABLE`. Referenced Evidence IDs must be active in the current ledger.

### ScannerExecutionResult

The stage records:

- mandatory and reinforcement scanner IDs;
- one typed finding per executed scanner;
- missing mandatory handlers;
- failed handlers;
- missing evidence requests;
- kill-condition hits;
- reinforcement requests;
- deterministic snapshot hash.

## Fail-closed rules

1. Every mandatory scanner requires an exact handler. An optional or LLM-proposed reinforcement cannot replace a missing mandatory scanner.
2. A handler returning the wrong scanner ID is invalid.
3. Invented, superseded or inactive Evidence IDs fail validation.
4. A mandatory `NOT_APPLICABLE` result is invalid because applicability was already determined by Industry DNA and the Module Requirement Plan.
5. Missing evidence enters `RECOVERY_REQUIRED` with explicit metric requests.
6. Missing handler coverage is `NOT_IMPLEMENTED`, not a silent PASS.
7. Handler execution/validation failures are blocking.
8. Findings cannot emit compiled assumptions, WACC, PER, intrinsic value, target price or current-price-dependent outputs.

## Mandatory versus reinforcement scanners

The deterministic Module Requirement Plan owns the mandatory loadout. LLM Staff may propose `scanner_reinforcement_ids` for unknown-unknowns, but reinforcement is additive only.

A reinforcement finding follows the same Evidence-lineage and impact-path rules. Failure or absence of an optional reinforcement does not satisfy or erase a mandatory scanner obligation.

## LIVE_PRIMARY readiness

The generic dispatch and validation contract is PARTIAL_LIVE. It becomes fully live for a routed company only when every mandatory scanner ID has a source-aware handler with fixtures, freshness behavior and regression coverage.

The dispatcher therefore exposes two different gaps explicitly:

- runtime/contract gap: missing scanner handler;
- evidence gap: handler exists but required company/industry evidence is unavailable.

These feed the ordinary Control Plane Recovery and Capability Gap process.

## Maintenance and Decision Impact

Scanner findings must declare affected variables or economic paths so `ModuleImpactTrace`, automatic ablation and research-cost learning can test whether the scanner actually changed assumptions, timing, route, method, conclusion or guardrail state.

Repeated costly zero-impact scanners may be down-ranked through the existing governance process. Mandatory guardrails and catastrophic-risk scanners are not removed solely because ordinary value delta is zero.
