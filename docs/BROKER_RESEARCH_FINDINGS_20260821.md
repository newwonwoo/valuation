# Broker Research Search Findings — 2026-08-21

## Bottom line

Sell-side research should be added as a dedicated **Question / Mechanism / KPI Discovery Layer**, not blended into primary evidence. Korean broker research is unusually accessible and detailed at the industry-mechanism level. Global banks add proprietary alternative-data frameworks, cross-sector thematic work and institutional debate maps, but much of the full product is entitled/client-only.

## High-value patterns found

### 1. Industry DNA / value-chain decomposition

LS Securities explicitly argues that the same semiconductor upcycle does not benefit IDM, foundry, fabless and value-chain companies equally, and frames the work as `industry concept → value-chain analysis → front-end environment → company earnings/rerating`. This is direct support for segment-first, economic-DNA routing.

### 2. Indicator-quality audit

Daishin semiconductor work challenges spot DRAM price as a dominant signal when spot transactions are a small portion of a market increasingly driven by server/mobile fixed-price contracts. This supports an `Indicator Representativeness Gate`: before using any leading indicator, measure the share of economics it actually represents.

### 3. Engineering intensity as valuation driver

Hanwha semiconductor deep dives connect HBM/NAND process complexity to wafer warpage, yield loss and incremental back-side-deposition equipment intensity. This is a strong template for `technology transition → physical bottleneck → tool intensity → order/capacity → FCF`.

### 4. Project-stage realization haircut

IBK research on semiconductor clusters distinguishes announced investment from executable revenue using land acquisition, compensation, permitting, power/water and construction timing. Goldman Sachs applies a similar concept to data-center capacity, explicitly haircutting scheduled projects for delays/cancellations. This should become a generic `Announced/Scheduled → Funded → Permitted → Under Construction → Energized/Operating` state machine.

### 5. Financial-sector capital efficiency

Hanwha bank research emphasizes ROE together with RoRWA and highlights service income as a low-capital-consumption route to higher ROE. Broker research is therefore useful for discovering sector-native KPIs that generic accounting taxonomies miss.

### 6. Alternative-data / question-bank architecture

UBS Neo advertises Evidence Lab (1000+ datasets, 50+ sectors, 30+ countries) and a Question Bank with 200,000+ investor questions. BofA describes social-media monitoring, industry surveys, jobs data and third-party alternative datasets. Evercore highlights recurring sector data reviews, surveys and proprietary leading indicators. These suggest two new RocketSLA concepts:

- `Investor Debate Registry`: what questions professional investors repeatedly ask by sector.
- `Alternative Data Candidate Registry`: data source, coverage, sample, lag, bias, licensing and validation status.

### 7. Street echo risk

Broker independence is not source independence. Two reports citing the same TrendForce, Counterpoint, SNE, Clarksons, FactSet, Bloomberg or company channel can repeat one underlying observation. Store both `broker_family` and `underlying_data_family`; mechanism corroboration counts underlying information families.

## Recommended operating policy

### Before intrinsic freeze
Allowed from broker reports:
- industry definitions / value chains
- KPI and accounting-definition candidates
- industry mechanisms and leading-indicator candidates
- industry-wide forecasts as `FORWARD_HYPOTHESIS`
- pointers to underlying primary/association datasets

Forbidden before intrinsic freeze:
- target-company Street revenue/EPS/EBITDA/FCF
- target price / rating / implied upside
- target multiple
- consensus

### After intrinsic freeze
Use full Street layer for:
- model reproduction
- forecast delta decomposition
- target-price method and assumption comparison
- consensus-lag detection
- analyst calibration

## New modules suggested

1. `Broker Research Indexer`
2. `Rights & Entitlement Gate`
3. `Report Type Classifier`
4. `Field-Level Street Quarantine`
5. `Underlying Data Lineage Resolver`
6. `Indicator Representativeness Gate`
7. `Investor Debate Registry`
8. `Alternative Data Candidate Registry`
9. `Analyst Forecast Calibration Ledger`
10. `Street Echo / Consensus Herding Audit`


## Additional patterns from the extended domestic/global sweep

### 8. Multi-constraint bottleneck taxonomy
Goldman Sachs' public `6 Ps` power framework is useful as a research-planning template: AI pervasiveness, compute productivity, electricity price, policy, parts and people can each become the binding constraint. RocketSLA should therefore avoid choosing a single bottleneck ex ante; it should maintain competing constraint candidates and update them with evidence.

### 9. Economic-life / depreciation / financing linkage
BofA's 2026 AI hardware work explicitly connects hyperscaler capex, practical GPU useful life, financing concerns and long-lead bottlenecks across wafers, memory, substrates, optics, power and land. This supports a common `Asset Economic Life & Collateral` bridge connecting operating assumptions, depreciation, residual value and funding availability without double counting.

### 10. Geography is a capability vector, not a domicile label
Samsung Securities' cross-country strategic-industry comparison evaluates different capability dimensions by industry. Country exposure should therefore be modeled through manufacturing, design/IP, data, infrastructure, regulation and end-demand capability rather than headquarters alone.

### 11. Technology transition must be translated into physical intensity
Yuanta's power-semiconductor work illustrates why `same semiconductor` is too broad: Si, SiC and GaN have different voltage/thermal/application economics and supplier architectures. Technology roadmaps should map to wafer/material/device/packaging intensity before becoming a revenue assumption.

### 12. Funding-source expansion needs realization gates
Eugene's sovereign-AI work broadens AI-capex funding beyond private hyperscalers toward state/sovereign support. That is useful for the Upstream Funding Ladder, but announced sovereign pipelines remain `FORWARD_HYPOTHESIS` until funding instrument, procurement, site/power and execution evidence are verified.

### 13. Hidden common-factor concentration
Oppenheimer's public 2026 viewpoints note that AI exposure increasingly spans equities, public credit and private asset-backed finance. Industry diversification should therefore be audited by common economic drivers, not just GICS/sector labels.

## Search-universe coverage

The domestic discovery universe now tracks 32 Korean securities houses listed by Bondweb, while only directly verified/usable portals are promoted into the broker source registry. The global watchlist covers 29 major research franchises across the US, Europe and Asia; restricted/client-only providers remain metadata/watchlist entries until legitimate entitlement is available.

Extel rankings are useful only to prioritize which research houses deserve deeper source-specific adapters. They do not increase evidence weight by themselves.
