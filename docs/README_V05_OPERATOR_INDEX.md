# v0.5 Operator Index

Use this file as the first navigation page.

## 0. Who commands the workflow?

Read/use:
- `docs/CONTROL_PLANE_ARCHITECTURE.md`
- `config/control_plane_stage_registry.yaml`
- `src/valuation_engine/control_plane.py`
- `src/valuation_engine/orchestrator.py`

Canonical authority:
- Doctrine defines rules.
- Control Plane commands stages, module/scanner loadout, recovery and access.
- LLM observes, reasons, proposes, recovers, designs and asks; it never commits assumptions or authorizes stages.
- Compiler commits validated assumptions.
- Deterministic engines calculate.
- Audit alone authorizes the Intrinsic Freeze.

`None` or a failed method enters the canonical recovery ladder before `VALUATION_BLOCKED`. A material reusable capability gap may be designed by the LLM, but build work requires an explicit user decision. Every applicable module/scanner/gate must leave a doctrine-coverage status; silent skip is forbidden.

## 1. What industry is this business?
Read/use:
- `config/foundation_source_registry.yaml` — ISIC/KSIC/NAICS classification foundations
- `config/industry_taxonomy.yaml`
- `config/sector_adapter_registry.yaml`
- `docs/INDUSTRY_DNA_ROUTER_V1.md`

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

Output: canonical metric definitions and tag mappings. Taxonomy tags define concepts; filings provide realized values.

## 7. What gets into Beta/WACC/PER/DCF?
Read/use:
- `docs/SOURCE_TO_VALUATION_PLACEMENT_MAP.md`
- `src/valuation_engine/knowledge_placement.py`

Rules:
- Beta/WACC/PER calibration references are sanity checks, not copied answers.
- DCF/assumption compilation accepts primary/company evidence only through Bridges.
- Broker and alternative data never directly compile into intrinsic assumptions.

## 8. How are updates handled?
Read/use:
- `config/source_watch_registry.yaml`
- `docs/SOURCE_FRESHNESS_OPERATION.md`
- `src/valuation_engine/source_watch.py`

Output: new release / revision / definition change / schema change / missed release / source failure → dirty nodes → revalidation request.

## 9. How does knowledge become a canonical industry rule?
Read/use:
- `data/mechanism_candidates.yaml`
- `src/valuation_engine/module_promotion.py`

Required: independent-source corroboration + leading indicator + valuation link + kill condition + multi-period evidence + Red Team + regression + explicit approval.

## 10. Does a module actually affect the conclusion?
Read/use:
- `docs/DECISION_IMPACT_SENSITIVITY.md`
- `config/decision_impact_policy.yaml`
- `src/valuation_engine/decision_impact.py`
- `src/valuation_engine/ablation.py`

Every active module/scanner/gate must declare an impact path to assumptions, decisions, economic paths, final outputs, or guardrail protection. Use leave-one-module-out counterfactuals and evidence-backed numeric perturbations to measure value/decision/timing/guardrail impact. Repeated costly zero-impact research becomes a down-rank or retire candidate; mandatory guardrails are retained even when ordinary value delta is zero. Missing counterfactual support is `NOT_MEASURABLE`, never zero impact.

## 11. What does each unit consume, emit and affect?
Read/use:
- `config/unit_contract_registry.yaml` — canonical machine-readable Unit Contract & Impact Registry
- `docs/UNIT_CONTRACT_AND_IMPACT_MAP.md` — maintenance guide
- `src/valuation_engine/unit_contracts.py` — forward/reverse dependency lookup and expected-vs-actual effect audit

Use this layer before editing a scanner/gate/engine. It records inputs, outputs, downstream consumers, allowed effect classes, final-output reach, forbidden effects and canonical implementation references. `ModuleImpactTrace` remains run-specific actual truth; the Unit Contract Registry is static expected design truth.

## 12. How does a generic run build Coverage, Impact, Audit and Freeze automatically?
Read/use:
- `docs/GENERIC_RUNTIME_GOVERNANCE.md`
- `src/valuation_engine/doctrine_runtime.py`
- `src/valuation_engine/impact_adapter.py`
- `src/valuation_engine/audit_adapter.py`
- `src/valuation_engine/orchestrator.py`

The Control Plane builds pre-audit Doctrine Coverage from Unit Contracts and actual stage traces. `AUDIT_GATE` first records module counterfactual/guardrail impact, then runs Generic Audit. After Audit passes, the Control Plane rebuilds final coverage and atomically issues the Intrinsic Freeze Token. Callers do not need to inject hand-written coverage tuples.

## 13. Validation
Run:
```bash
PYTHONPATH=src python scripts/validate_industry_seed.py
PYTHONPATH=src python scripts/validate_module_registries.py
PYTHONPATH=src python scripts/validate_broker_research_layer.py
PYTHONPATH=src python scripts/validate_knowledge_placement.py
PYTHONPATH=src python scripts/validate_workflow_source_injection.py
PYTHONPATH=src python scripts/validate_unit_contract_registry.py
PYTHONPATH=src pytest -q
```

## v0.5.2 Signal Intelligence extension

Read `SIGNAL_INTELLIGENCE_LAYER_V1.md` when a workflow uses permits, procurement, interconnection queues, patents, jobs, credit markets, short interest, insider transactions, customs/logistics, clinical registries, or remote sensing. `SignalClass` is orthogonal to `KnowledgeLayer`; do not infer evidence authority from signal type alone.

Critical market-data split: financing market references may support WACC/funding through a Bridge; target-equity market references remain post-freeze only; positioning signals never mutate same-run intrinsic value.
