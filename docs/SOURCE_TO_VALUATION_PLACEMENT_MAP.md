# Source → Valuation Placement Map

This is the operator-facing map for deciding **where each kind of research belongs**.

| Source family | Industry DNA | KPI requirement | Mechanism | Beta | WACC | DCF/Assumption | Warranted PER | Monte Carlo | Street Gap | Update Watch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ISIC / KSIC / NAICS | ✅ |  |  |  |  | ❌ |  |  |  | ✅ revision |
| SASB / ISSB | △ | ✅ | △ risk candidate |  | △ only via evidenced risk path | ❌ | △ twin features only | △ kill/risk candidate |  | ✅ |
| IFRS / XBRL taxonomy |  | ✅ normalization |  |  |  | ❌ value; ✅ definition |  |  |  | ✅ definition |
| OECD/BOK/BEA Input-Output | ✅ structural | △ | ✅ structural prior | △ twin geography/exposure | △ funding exposure context | ❌ direct | △ twin structure | △ correlation prior |  | ✅ vintage |
| Official statistics / regulators | ✅ | △ | ✅ | △ | ✅ rates/credit where applicable | ✅ through Bridge | △ normalization/benchmark | ✅ history |  | ✅ |
| Public research institutes / associations | ✅ | ✅ candidate | ✅ corroboration | △ twin design | △ industry risk context | ❌ direct | △ duration/industry benchmark candidate | ✅ scenario prior with labels |  | ✅ |
| Broker / IB industry research | ✅ candidate | ✅ candidate | ✅ discovery/corroboration | △ peer-feature discovery | △ funding/Kd questions only | ❌ pre-freeze | △ peer-feature discovery; target multiple ❌ | △ verification request | ✅ target fields post-freeze | ✅ index |
| Alternative data |  | △ | △ leading signal |  |  | ❌ direct |  | △ only after validation |  | ✅ high-frequency |
| Company filings / contracts | ✅ segment evidence | ✅ | ✅ | ✅ leverage/business facts | ✅ debt/capital structure | ✅ through Bridge | ✅ normalized EPS/quality | ✅ | ✅ reproduction input | ✅ event |
| Damodaran/sector calibration |  |  |  | ✅ L1 sanity/prior | ✅ sanity | ❌ | ✅ sector sanity | △ prior ranges |  | ✅ vintage |
| Target price / consensus / current price | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ pre-freeze | ❌ | ✅ post-freeze | ✅ market |

Legend: ✅ primary intended use, △ supporting/candidate use, ❌ prohibited direct use.

## Operational source order

For a new company run, collect in this order rather than reading everything:

1. **Foundation snapshot** — classification, metric ontology, provenance version.
2. **Sector module requirement plan** — determines which KPIs are needed.
3. **Primary observed industry state** — only required metrics/series.
4. **Company primary evidence** — segment facts, accounting, contracts, financing.
5. **Broker/IB discovery pass** — only to search for missing mechanisms, debates, channel indicators and underlying datasets.
6. **Independent verification pass** — verify broker-discovered claims with primary/public/independent sources.
7. **Calibration pass** — Beta/WACC/PER sector distributions as sanity checks.
8. **Blind intrinsic value freeze.**
9. **Street/market pass** — company-specific forecasts, target multiples, target prices and current price.

This reduces both research cost and confirmation bias. A module should request the data it needs; the analyst should not collect a large corpus first and decide later what it means.
