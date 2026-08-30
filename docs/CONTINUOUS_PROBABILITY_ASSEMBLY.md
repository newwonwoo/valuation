# Continuous Probability Assembly

`src/valuation_engine/continuous_probability_assembly.py`

The v3.2 continuous financial-path route used to exist only as
`skhynix_continuous_probability.py`: one module that both *declared* which
calibration to run and *performed* the whole assembly. Every check inside it was
generic reasoning written against one company's constants — the ticker `000660`,
a 9-year path, four named drivers, three scenario labels, four literal hashes.
A second company could not use the route without copying the file, and a copied
guard is a guard that drifts.

This module holds the assembly. A company holds a binding.

## Split

| Generic (this module) | Bound per company |
|---|---|
| Artifact load, self-hash check, format version, OOS chronology | Which artifact file, its expected hash |
| Provenance load, lineage hash, target-exclusion check | Expected lineage hash, excluded ticker |
| Knowledge-cutoff enforcement (training and conditioning first-seen) | — |
| Conditioning ↔ artifact ↔ provenance three-way agreement | Which drivers, which may not go negative |
| Driver posterior / OOS diagnostic / scenario path / dependence reconstruction | Driver ids and order, scenario ids, path length |
| Forbidden value-and-price key sweep | Extra cohort-specific banned keys |
| Monte Carlo call and snapshot sealing | Credible level, draw counts, seed |

Nothing on the binding is a computation. A binding cannot loosen a check, only
say what the check is comparing against; the only fields it adds are
`extra_forbidden_artifact_keys` and `non_negative_driver_ids`, both of which can
only make the artifact harder to accept.

## Binding

```python
ContinuousCalibrationBinding(
    cohort_key="shipbuilding|5y_path|continuous_v1",
    forecast_class="industrial.shipbuilding.continuous_financial_path",
    horizon="5y_path_from_12m_transitions",
    method_version="probability_engine_v3.2_continuous_financial_path_v1",
    mapping_version="shipbuilding_continuous_v1",
    driver_ids=("order_intake_growth", "yard_utilisation", "steel_cost_ratio"),
    scenario_ids=("Bear", "Base", "Bull"),
    path_length=5,
    artifact_path=...,
    provenance_path=...,
    expected_artifact_sha256=...,
    expected_provenance_artifact_sha256=...,
    expected_dataset_sha256=...,
    expected_provenance_hash=...,
    expected_source_row_count=210,
    expected_source_company_count=17,
    excluded_ticker="009540",
    seed=...,
    non_negative_driver_ids=("yard_utilisation",),
)
```

`excluded_ticker` is checked against **both** the artifact and the frozen
provenance file, so a calibration can never be silently re-pointed at a company
whose own rows trained it.

`driver_ids` is ordered, not a set. The order fixes the dependence matrix axes
and the per-driver source-hash composition, so reordering a binding changes the
snapshot hash — which is the intended behaviour, not a defect.

## Conditioning

`ContinuousConditioning` carries the company's current driver readings plus the
source that published them and the moment it became observable. It is checked
three ways: against the binding's driver set, against the artifact's frozen
`current_conditioning` row, and against the provenance file's source ref, source
hash and first-seen time. `conditioning_from_mapping` builds one from a provider
snapshot row in binding driver order.

`first_seen_at` is compared to the requested `as_of_date`. Replaying an earlier
snapshot date raises `PermissionError` rather than quietly conditioning on
information that did not exist yet; the same test is applied to the calibration's
own `training_latest_publication_at`.

## Issuance

The assembler returns a `ContinuousProbabilityCalibrationSnapshot`. That snapshot
runs its own integrity validation and then issues a canonical
`CalibrationCertificate` through `.certificate()`, which is what the runtime
weighting socket accepts. The company adapter never touches the certificate; it
only decides which calibration was loaded.

```
binding + conditioning -> assembler -> continuous snapshot -> .certificate() -> runtime socket
```

## SK hynix

`skhynix_continuous_probability.py` is now a declaration. It keeps its public
names (`COHORT_KEY`, `DRIVER_IDS`, `SCENARIOS`, the `EXPECTED_*` hashes,
`CurrentConditioning`, `build_skhynix_continuous_probability_snapshot`) so the
live-primary wiring is unchanged, and it produces a byte-identical snapshot: the
snapshot hash payload never contained the binding.

Its `CurrentConditioning` remains a four-field dataclass because the call sites
read better with named drivers; `as_conditioning()` converts it to the generic
form.

## Tests

- `tests/test_continuous_probability_assembly.py` — a company that does not exist
  in this repository (three drivers, five years, two scenarios, a different
  ticker) reaching a `CALIBRATED` snapshot and a weighting-grade certificate,
  plus each guard re-proved on that company rather than on SK hynix.
- `tests/test_skhynix_live_primary.py` — unchanged, and still asserting the same
  frozen probabilities.
