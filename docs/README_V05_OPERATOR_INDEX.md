# v0.5 Operator Index

Use this file as the first navigation page.

## Precedence

Canonical runtime/methodology/code/registries outrank operator references:

`SKILL.md / AGENTS.md / V04 / V05 contracts / src + config` **>** `references/methods`, `references/industry`, `references/modes`.

The reference files are concise manual procedures and diligence checklists. They may narrow or operationalize a workflow but cannot override fail-closed gates, market/Street isolation, calibrated-probability requirements, module routing, Beta/WACC/PER contracts, or double-count audits.

## 1. What industry is this business?
Read/use:
- `config/foundation_source_registry.yaml` — ISIC/KSIC/NAICS classification foundations
- `config/industry_taxonomy.yaml`
- `config/sector_adapter_registry.yaml`
- `docs/INDUSTRY_DNA_ROUTER_V1.md`
- `references/industry/README.md` — operator supplements only

Output: segment decomposition + Sector Adapter + Economic Archetype candidates.

## 2. What must be researched before valuation?
Read/use:
- `config/archetype_module_registry.yaml`
- `config/knowledge_placement_policy.yaml`
- `config/workflow_source_injection_map.yaml`
- SASB/ISSB foundation entry in `foundation_source_registry.yaml`

Output: Module Requirement Plan — required evidence, KPI definitions, Beta/PER twin features, scenario variables, funding checks, kill conditions and forbidden methods.

## 3. Where do actual industry facts come from?
Read/use:
- `config/industry_source_registry.yaml`
- `data/industry_seed_documents.yaml`
- `data/industry_seed_claims.yaml`
- `docs/INDUSTRY_KNOWLEDGE_INGESTION_V1.md`

Output: realized state, definitions, forecasts clearly separated, mechanism evidence.

## 4. Where do sell-side/IB materials go?
Read/use:
- `config/broker_research_source_registry.yaml`
- `config/broker_report_type_registry.yaml`
- `config/broker_underlying_data_aliases.yaml`
- `data/investor_debate_seed.yaml`
- `docs/BROKER_RESEARCH_LAYER_V1.md`

Pre-freeze: value chain/KPI/mechanism/question/data-source discovery only.
Post-freeze: target-company forecast/target price/rating/target multiple/consensus → Street Gap.

Simplified sell-side averaging or target-multiple procedures do not override `src/valuation_engine/street.py` or the Street Gap contract.

## 5. Where do supply-chain maps come from?
Read/use:
- OECD ICIO / BOK IO / BEA IO entries in `config/foundation_source_registry.yaml`
- `config/impact_graph_seed.yaml`

Output: structural upstream/downstream prior. Never a real-time company revenue assumption by itself.

## 6. How are accounting/KPI names normalized?
Read/use:
- IFRS Accounting Taxonomy / SEC XBRL / OpenDART
- SASB/ISSB metric template
- `KnowledgeLayer.METRIC_STANDARD`
- `references/methods/period-matching.md`
- `references/methods/adjusted-earnings.md`

Output: canonical metric definitions, period/scope reconciliation and auditable earnings normalization. Taxonomy tags define concepts; filings provide realized values.

## 7. What manual method supplements are accepted?

Accepted v2-derived operator supplements:
- `references/methods/period-matching.md`
- `references/methods/adjusted-earnings.md`
- `references/methods/cycle-normalization.md`
- `references/methods/dilution.md`
- `references/methods/reverse-dcf.md` — **post-freeze only**

Not adopted as parallel sources of truth:
- simplified WACC/Blume rules → use V04 + `risk.py` / `wacc.py`,
- simplified theoretical-PER cross-check → use Hierarchical Warranted PER + DCF–PER gate,
- fixed evidence-to-probability tables → use calibrated probability framework or label `UNCALIBRATED`,
- trimmed target-price consensus as valuation anchor → use post-freeze Street Gap,
- price-gap-driven bull/bear repair → scenario completeness comes from evidence/mechanisms, not current-price fit.

## 8. What optional non-valuation modes exist?

Operator helpers:
- `references/modes/company-brief.md`
- `references/modes/credit-risk.md` — screening, not lender underwriting
- `references/modes/disclosure-monitor.md`
- `scripts/dart.py` supports `facts`, `credit`, and `brief` extraction helpers.

These modes cannot bypass the valuation workflow or convert their conclusions directly into intrinsic value.

## 9. What gets into Beta/WACC/PER/DCF?
Read/use:
- `docs/SOURCE_TO_VALUATION_PLACEMENT_MAP.md`
- `src/valuation_engine/knowledge_placement.py`
- `docs/V04_ROCKETSLA_EXTENSION.md`

Rules:
- Beta/WACC/PER calibration references are sanity checks, not copied answers.
- DCF/assumption compilation accepts primary/company evidence only through Bridges.
- Broker and alternative data never directly compile into intrinsic assumptions.
- Customer advances affect FCFF/ROIC first; WACC needs separate credit evidence.
- Core PER and DCF share the same economic worldview.

## 10. How are updates handled?
Read/use:
- `config/source_watch_registry.yaml`
- `docs/SOURCE_FRESHNESS_OPERATION.md`
- `src/valuation_engine/source_watch.py`

Output: new release / revision / definition change / schema change / missed release / source failure → dirty nodes → revalidation request.

## 11. How does knowledge become a canonical industry rule?
Read/use:
- `data/mechanism_candidates.yaml`
- `src/valuation_engine/module_promotion.py`

Required: independent-source corroboration + leading indicator + valuation link + kill condition + multi-period evidence + Red Team + regression + explicit approval.

## 12. v0.5.2 Signal Intelligence extension

Read `SIGNAL_INTELLIGENCE_LAYER_V1.md` when a workflow uses permits, procurement, interconnection queues, patents, jobs, credit markets, short interest, insider transactions, customs/logistics, clinical registries, or remote sensing. `SignalClass` is orthogonal to `KnowledgeLayer`; do not infer evidence authority from signal type alone.

Critical market-data split: financing market references may support WACC/funding through a Bridge; target-equity market references remain post-freeze only; positioning signals never mutate same-run intrinsic value.

## 13. Validation
Run:
```bash
PYTHONPATH=src python scripts/validate_industry_seed.py
PYTHONPATH=src python scripts/validate_module_registries.py
PYTHONPATH=src python scripts/validate_broker_research_layer.py
PYTHONPATH=src python scripts/validate_knowledge_placement.py
PYTHONPATH=src python scripts/validate_workflow_source_injection.py
PYTHONPATH=src pytest -q
```
