# Source Freshness & Revision Watcher — Operating Contract

Status: v0.5 candidate.

## Purpose

A knowledge source is not “fresh” merely because its URL still loads. The watcher must distinguish a new vintage, a revision to an old period, a metric-definition change, a transport/schema migration and an expected release that did not arrive.

## Watch state

`CLEAN | NEW_RELEASE | REVISION | DEFINITION_CHANGE | SCHEMA_CHANGE | EXPECTED_RELEASE_MISSED | SOURCE_FAILURE | UPDATED_NOT_REVIEWED | REVALIDATION_REQUIRED | MATERIAL_CHANGE`

Four hashes are tracked independently where the source permits it:
- `document_hash`: document/file changed;
- `fact_hash`: values changed;
- `definition_hash`: meaning/coverage/methodology changed;
- `schema_hash`: API/table/transport shape changed.

Definition or schema changes block automatic module promotion. A revision requires affected assumptions to be revalidated rather than silently overwritten.

## Multi-endpoint reconciliation

Some publishers expose a product page, data explorer, download index and API on different update clocks. Freshness is reconciled across healthy endpoints. The freshest endpoint may be used to establish that a release exists, but endpoint divergence is retained as an operational warning and must not be treated as economic evidence.

The IEA Monthly Electricity Statistics seed is the regression example: the product page and data tool exposed different latest dates during the 2026 SDMX transition. The system therefore prevents a false `EXPECTED_RELEASE_MISSED` finding based on one stale presentation endpoint.

## Release-miss policy

Do not declare a missed release until `expected_release_date + grace_days` and a live refetch has been attempted. A failed crawler is `SOURCE_FAILURE`, not evidence that the publisher stopped releasing data.

## Licensed or credentialed sources

Public metadata may be monitored even when raw data require a subscription/member credential. Raw licensed content is never committed to the public repository. The KAMA production-statistics series is the seed example: public release metadata can be watched, while member statistics require authorized access.

## Impact propagation

A changed source marks generic industry/mechanism nodes dirty. `impact_graph_seed.yaml` maps those nodes to valuation assumptions. Company-specific source→segment→assumption edges belong in private live state.

A source update does not automatically change fair value. It creates a revalidation request. Definition changes, major revisions, kill-condition hits and material mechanism changes may change distributions/scenarios only after evidence/bridge validation.

## Production posture

The current candidate includes deterministic parsers, bounded HTTP transport and fixture regression tests. The current container cannot validate public-network live transport, so `data/source_watch_status_snapshot.yaml` is explicitly an offline fixture/baseline QA artifact, not a live freshness attestation.
