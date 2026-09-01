# Reviewed Assumption Range Rules

Assumption ranges are **authority data**, not free-form analyst or LLM judgment.
`AssumptionSpec.min_value` / `max_value` supplied by a proposal are stripped before
compilation. A bound becomes authoritative only when a rule in
`config/assumption_range_rule_registry.yaml` deterministically derives it from the
current target's realized/filing Evidence.

## Production review contract

A rule may be added only when review can answer all of the following from the diff:

1. Why this assumption key needs a bounded domain.
2. Why the selected filing metric is the correct economic anchor for that key.
3. Why the lookback observation count is comparable across the selected periods.
4. Why the lower/upper multipliers, floor, and ceiling are economically justified.
5. Whether the rule remains valid across cycles, accounting-period changes, and
   segment changes; if not, the rule must be scoped or withheld.

The registry intentionally starts with no production rules. Do not seed a multiplier
or lookback merely because a range would be convenient. The reviewed rule is the
judgment; deterministic code only executes it. Production compilation exposes no
runtime registry-path override: changing range authority requires changing and
reviewing the canonical registry in the repository.

## Runtime containment

- Anchor Evidence must belong to the current compiled target.
- Only `realized_or_filing` Evidence may author the envelope.
- Filing observations are counted by canonical calendar date; duplicate conflicting
  values on one date are ambiguous and fail closed.
- Units are converted through the canonical unit system before min/max derivation.
- Non-finite multipliers and inverted bounds are rejected.
- Insufficient history blocks the rule instead of falling back to proposal bounds.
- Typed range provenance is included in the compiled Evidence hash.
- If a canonical rule exists for an assumption, its typed range receipt is mandatory;
  deleting the receipt cannot turn a governed assumption back into an unbounded one.
- If the canonical registry cannot be loaded at Audit, the compiled assumption set is
  not certifiable and fails closed rather than assuming that no range rule applies.
- Audit reloads the canonical reviewed registry, binds the receipt to the compiled
  target/key/scenario/unit, re-derives the range from the current Evidence, and checks
  the compiled value still lies inside that range before Intrinsic Value Freeze; a
  self-consistent forged or deleted receipt is therefore insufficient.

## Example review shape

The following is illustrative only and is **not** a production rule:

```yaml
- rule_id: example-midcycle-oi-v1
  assumption_key: normalized_operating_income
  anchor_metric: operating_income_history
  canonical_unit: KRW
  lookback_observations: 5
  min_observations: 5
  lower_multiplier: <reviewed factor>
  upper_multiplier: <reviewed factor>
  source_layers: [realized_or_filing]
  review_ref: docs/ASSUMPTION_RANGE_RULES.md#example-midcycle-oi-v1
```

A production entry must replace every placeholder with an explicitly reviewed value
and must have regression coverage for derivation, insufficient history, ambiguity,
and Audit hash replay.