# Canonical company KPI registry

This registry closes the reusable extraction-contract gap for the four canonical real-company acceptance targets without hardcoding current KPI values.

## Companies and source contracts

- OCI Holdings: OpenDART exact financial facts plus exact-locator OCI Holdings IR capacity metrics.
- Oracle: SEC exact Company Facts plus exact-locator filing metrics for remaining performance obligations, significant-financing customer prepayments and cloud infrastructure revenue.
- Bloom Energy: SEC exact Company Facts plus exact filing labels for Product, Installation and Service revenue.
- GE Vernova: SEC exact Company Facts plus exact filing labels for remaining performance obligations/backlog, Gas Power contracted equipment/slot-reservation capacity and orders.

The registry fixes issuer identity, official source hosts, metric name, segment, locator and unit. It never supplies the current value. A source-specific parser or provider must still return a source-backed candidate with the exact registered locator and unit, after which the ordinary authorized-primary-source contract binds document hash, publication time, first-seen time and Evidence lineage.

No fuzzy account-name or locator matching is allowed. Target price, current market price, target market capitalization and consensus metrics are not registry-eligible intrinsic KPIs.

The SEC source transport is provided by `sec_edgar.py`; OpenDART standard facts use `dart_facts.py`. Real-company LIVE acceptance remains a separate QA requirement because a registry contract is not itself a successful company valuation run.
