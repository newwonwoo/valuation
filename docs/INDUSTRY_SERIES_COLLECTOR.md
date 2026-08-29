# Industry Series Collector

`src/valuation_engine/industry_series_collector.py` ·
`config/kr_industry_series_registry.yaml`

## From researched sources to Evidence, through the definition gate

The industry source registry researched 31 sources; the ingestion doctrine
wrote the rules; the indexers parsed metadata. What was missing was the bridge
that turns a *series* into *Evidence* — and that bridge is where the definition
gate either holds or quietly dies. This collector makes the gate structural:

1. **Industry-observable metrics only.** A series may serve `benchmark_price`,
   `input_price`, `output_price`, `inventory`, trade/demand series — never
   `realized_price`, `cash_cost`, `production`, `utilization` or any other
   company-realized quantity. Registry load refuses the mapping. An industry
   average wearing a company metric's name is exactly the benchmark-vs-realized
   conflation the normalization gate forbids, and it is refused by type, not by
   reviewer vigilance.
2. **A definition rides with every number.** `definition_id` and a real
   definition (min 20 chars — a label is not a definition) go into the Evidence
   notes.
3. **One verified series per (source, metric).** Two verified series claiming
   the same metric is a load error naming the scoped-split doctrine — never an
   average.
4. **Operator verification is the collection license.** Rows ship
   `verified: false`; the collector refuses them, and a source with no verified
   series gets no `CollectorCapability` at all. A guessed table identity is a
   fabricated source. **The default registry ships with zero verified rows**,
   so real-world coverage claims stay at zero until a human checks real series
   against the KOSIS/KEEI catalogs.
5. **Knowledge time and credentials.** The newest observation at or before the
   run's `as_of` is selected (period strings YYYY/YYYYMM/YYYYMMDD resolve to
   period ends); an observation after the cutoff can never be selected. The
   fetch URL renders `{api_key}` from an environment variable; the Evidence
   `source_ref` always carries the redacted form.

`KR_KOSIS_API` gained `benchmark_price`/`input_price`/`output_price` in the
source registry (a reviewed data change): KOSIS hosts producer- and
commodity-price index tables, and `inventory` was already declared.

## Effect on the cold-start boundary

With the probe's fixture registry (synthetic verified series, fixture values),
the boundary moved again:

```
전:  realized_price, benchmark_price, inventory, cash_cost,
     input_price, output_price, product_yield, plant_runs, turnaround
후:  realized_price, cash_cost, product_yield, plant_runs, turnaround
```

What remains is precisely the set the definition gate exists to protect:
company-realized quantities that must come from the company's own filings and
IR (판매가격/원재료 가격변동추이 tables, 정기보수 disclosures), not from
industry feeds. The next widening of the filing-KPI patterns — not another
industry source — is what moves this boundary.

## Production activation checklist

1. Pick the series in the KOSIS/KEEI catalog; confirm table identity, item,
   unit and cadence.
2. Copy the operator template in `config/kr_industry_series_registry.yaml`,
   fill it, write the real definition, set `verified: true`.
3. Set the credential env var; run the collector once against the live API and
   diff the parsed observations against the catalog before trusting freshness.
