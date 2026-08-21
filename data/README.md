# Industry Knowledge seed data

This directory stores short, structured, reproducible **derived facts and metadata**, not a mirror of copyrighted reports.

- `industry_seed_documents.yaml`: source/document metadata and locators.
- `industry_seed_claims.yaml`: short FACT/FORECAST/DEFINITION/MECHANISM/valuation-linked claims with provenance.
- `mechanism_candidates.yaml`: cross-source causal hypotheses; none become canonical automatically.
- `source_watch_baseline.yaml`: known-vintage baseline for future diffs; production findings require live refetch.
- `source_probe_fixtures.yaml`: concise verified page snippets used to regression-test parsers offline.
- `source_watch_status_snapshot.yaml`: offline fixture/baseline watcher QA report.
- `source_coverage_snapshot.yaml`: deterministic source-coverage heuristic. It is not an investment-quality rating.

Raw licensed/member content must stay outside the public repository. Public source material should be represented by metadata, citations/locators, definitions and short derived facts unless redistribution rights clearly permit more.

Cross-industry standards and structural priors are deliberately **not** mixed into seed claims. Their metadata lives in `config/foundation_source_registry.yaml`, because classification/taxonomy/provenance/input-output sources define the research environment rather than target operating facts.
