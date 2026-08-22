# Knowledge Placement & Use Contract v1

## Decision

RocketSLA does not maintain one flat source hierarchy. It maintains **orthogonal source authority + pipeline placement**.

A source can be authoritative for one job and forbidden for another. Example: SASB/ISSB can be authoritative for an industry KPI/risk-definition template while being forbidden from directly changing revenue, margin, WACC or PER. OECD/BOK/BEA input-output tables can be strong structural supply-chain priors while being too stale/broad for a current company demand assumption. Sell-side can be excellent for discovering the right question while remaining secondary evidence.

## Placement stack

```text
FOUNDATION / ONTOLOGY
├─ Classification: ISIC / KSIC / NAICS
├─ Metric & risk ontology: SASB / IFRS taxonomy / XBRL tags
└─ Provenance & quality: DCAT / PROV-O / DQV

STRUCTURAL PRIORS
├─ OECD ICIO / TiVA
├─ BOK input-output
└─ BEA input-output

INDUSTRY STATE / STRUCTURE
├─ official statistics & regulators
├─ public research institutes / associations
└─ public industry datasets

DISCOVERY / EDGE FINDING
├─ broker / investment-bank research
├─ channel checks
└─ alternative-data candidates

COMPANY PRIMARY
├─ filings / XBRL
├─ contracts / regulatory approvals
└─ company official plans (plans stay plans)

VALUATION CALIBRATION
├─ sector beta / ROIC / margin / multiple distributions
└─ never copied into target assumptions

POST-FREEZE STREET / MARKET
├─ target-company broker forecasts / target prices / ratings / target multiples
├─ consensus
└─ current target-company market price
```

## What each layer is allowed to do

| Layer | Best use | Can directly set target assumption? |
|---|---|---|
| Classification standard | Label/crosswalk industries | No |
| Metric standard | Define required KPI/risk/accounting concepts | No |
| Provenance standard | Version, lineage, data-quality governance | No |
| Structural supply-chain prior | Identify upstream/downstream exposure and structural weights | No |
| Primary observed | Establish realized industry state | Only through Bridge |
| Public industry research | Structure/mechanism/forecast candidates | No |
| Broker research | Questions, KPIs, value chain, debates, data-source discovery | No pre-freeze |
| Alternative data | Leading-indicator/nowcast verification request | No |
| Company primary | Target company realized facts/plans | Only through Bridge; plans remain plans |
| Calibration reference | Beta/PER/ROIC/margin sanity distributions | No |
| Market/Street | Compare with frozen intrinsic value | Post-freeze only |

## Broker placement

Broker content is split field-by-field, not report-by-report.

**Before freeze:** industry definition, value chain, KPI candidates, industry-wide mechanisms, investor debates, leading-indicator candidates and underlying-data references are allowed as secondary discovery objects.

**After freeze:** target-company forecasts, target price, rating, target multiple and consensus enter `STREET_GAP_ANALYZER` and model reproduction. They never backsolve the already-frozen run.

Broker independence is based on `underlying_data_family`, not broker logo. Five brokers repeating one TrendForce/FactSet/Clarksons series remain one underlying data family.

## Foundation placement

### SASB / ISSB
Use as `ModuleRequirementTemplate`: which risk/opportunity topics and metrics deserve collection. Never convert a SASB metric directly into WACC, PER or scenario probability.

### ISIC / KSIC / NAICS
Use for stable labels and crosswalks. Economic Archetypes remain an independent RocketSLA classification because statutory industry codes do not describe cash-flow mechanics well enough.

### Input-output tables
Use to seed `SupplyChainTopology` edges and structural exposure priors. Current edge strength must be refreshed/overridden by recent industry and company evidence when available.

### IFRS / SEC / OpenDART XBRL taxonomy
Use to normalize accounting concepts and detect tag/definition changes. A taxonomy tag is a definition; the filing value is the fact.

### DCAT / PROV-O / DQV
Use to align dataset-series versioning, claim derivation lineage and data-quality metadata. Keep internal YAML/dataclass storage; no RDF rewrite is required.

## Deterministic gate

`src/valuation_engine/knowledge_placement.py` is the fail-closed dispatcher. Before any source is exposed to a valuation stage, it must be assigned a `KnowledgeLayer` and pass `decide_placement(...)`.

This placement gate complements, rather than replaces, source authority, freshness, evidence/Bridge and double-count gates.
