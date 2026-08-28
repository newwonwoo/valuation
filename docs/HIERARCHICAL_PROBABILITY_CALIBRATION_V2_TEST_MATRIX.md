# Hierarchical Probability Calibration v2 — acceptance test matrix

Status: DESIGN

| Case | Expected behavior |
|---|---|
| Certified root, child n=0 | Child probability equals root; state `INHERITED` |
| Certified root, child small n | Child is shrunk toward parent; state `SHRUNK` |
| Small child with extreme outcomes | Posterior shift is capped before local promotion |
| Large coherent child sample | Child may reach `CALIBRATED_LOCAL` |
| Child OOS worse than parent | Local promotion denied; fall back to parent/shrunk result |
| Previously local-calibrated child deteriorates | State becomes `DEGRADED`; stale local certificate invalid |
| Parent snapshot changes | Descendant certificate hash changes and stale in-flight certificate fails |
| Same event belongs to parent and child | Event identity counted once; parent/child are views, not duplicate samples |
| Historical replay before late revision first_seen_at | Late revision excluded |
| Historical replay before later hierarchy remap | Later remap excluded unless mapping is declared static |
| Partially matured quarter | Entire quarter excluded from OOS window |
| Target company contaminated holdout | Explicitly excluded from evaluation sample |
| Market price/Street target supplied | Rejected as calibration outcome/input |
| Scenario uses correlated factors with no dependence contract | Numeric assembly withheld; use declared conservative bounds |
| Legacy v1 single cohort | Existing result remains byte/behavior compatible |

## Semiconductor pilot acceptance

Use the already collected semiconductor filing history as a shared fact source, not as a single pooled Down/Core/Bull frequency table.

The first pilot should derive at least these reusable 12-month outcomes:

- margin_compression
- cash_conversion_miss
- capex_overrun / capex_intensity shock where definable
- working_capital_deterioration where data is available

Then compare:

1. GLOBAL_EVENT root;
2. capacity_manufacturing;
3. semiconductor;
4. memory vs equipment/materials/OSAT-test child nodes.

SK hynix must remain excluded from any holdout window already exposed during development. Its current scenario probabilities are assembled only after the hierarchy pilot passes OOS checks.
