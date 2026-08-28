# Probability Engine v3

## Decision

New investment probability work uses a dynamic hierarchical Bayesian posterior instead of a binary statistical promotion gate.

The engine must produce a probability whenever data integrity is intact. Sparse or weak evidence reduces the influence of local data and widens probability uncertainty; it does not make the probability unavailable.

## Pipeline

1. Define reusable economic events such as price decline, margin compression, cash-conversion miss, capex overrun, or qualification delay.
2. Start each event with a beta prior at the broadest reusable level.
3. Traverse economic archetype → industry → sub-industry → company evidence.
4. Convert OOS predictive evidence into a continuous likelihood weight.
5. Apply weighted fractional-count Bayesian updates at each hierarchy node.
6. Carry the final beta posterior, not only its mean, into scenario assembly.
7. Sample event probabilities and joint event states under an explicit versioned dependence contract.
8. Map every joint state to exactly one valuation scenario and report point probability plus credible interval.
9. Bind normalized scenario posterior probabilities into valuation assumptions and retain posterior/dependence lineage hashes.

## Hard blocks

Only data-integrity failures hard-block numeric probability weighting:

- first-seen violation;
- publication-cutoff violation;
- duplicate event identity;
- outcome leakage / post-resolution construction;
- untraceable source;
- period or unit inconsistency.

Weak Brier Skill Score, a confidence interval crossing zero, small leaf samples, or missing OOS windows are not hard blocks.

## Continuous evidence weight

Predictive evidence weight is a bounded continuous function of:

- mean Brier Skill Score;
- resolved-event maturity;
- company breadth;
- quarter breadth;
- OOS-window depth;
- expected calibration error;
- Brier-skill interval precision;
- current-regime similarity.

A weak model therefore contributes a small likelihood update rather than zero evidence. The same weight also implies greater uncertainty inflation.

## Hierarchical posterior

For a beta parent prior with parameters `alpha, beta`, local successes `s`, failures `f`, and predictive evidence weight `w`:

`alpha_post = alpha + w*s`

`beta_post = beta + w*f`

A zero-observation leaf exactly inherits its parent posterior. Parent strength can be estimated from between-group dispersion via empirical Bayes rather than fixed globally.

## Scenario probabilities

Direct historical Down/Core/Bull frequency is forbidden. Scenario probability is derived from posterior economic events.

Naive independent multiplication is forbidden. Point scenario probabilities require an explicit dependence contract, currently a versioned correlation/copula representation. The Monte Carlo layer samples both event-probability uncertainty and correlated joint event states, producing a credible interval for each scenario probability.

## Compatibility

- v1 probability calibration remains available for historical replay.
- v2 hierarchical promotion-gate artifacts remain valid and replayable.
- v3 certificates implement the existing probability-weighting certificate contract, so Scenario Binding can consume v3 probabilities without a separate valuation path.
- market price and target price remain prohibited calibration inputs.
