# Deep Signal Research Findings — 2026-08-21

## Executive conclusion

The next useful expansion is not another broad industry-report feed. It is a **Signal Intelligence Layer** that captures traces appearing before financial statements: procurement, regulatory state, grid/interconnection, patents, hiring, trade/logistics, physical activity, financing conditions, ownership/positioning, clinical-regulatory events and remote sensing.

The most important architecture correction is to split `market data` into three roles. Target-equity price must stay blind until Intrinsic Value Freeze, but financing-market observations such as sovereign curves and target bond spreads are legitimate pre-freeze WACC/funding evidence. Short interest/options/flows are neither: they are positioning signals and must not mutate intrinsic value in the same run.

## High-value source families discovered

### 1. Procurement as funded-demand state
- Korea PPS/KONEPS exposes procurement plans, bid notices, award/contract processes, contract changes and delivery-request information through real-time public APIs.
- Use: public-sector demand state machine.
- Gate: plan != award; award != revenue; contract terms/cancellation and delivery/accounting evidence still matter.

### 2. Regulatory dockets as pre-news state
- FERC eLibrary and Regulations.gov expose dockets, orders, filings, comments and attachments.
- EPA ECHO exposes permits, inspections, violations, enforcement and facility histories, with public web services and bulk downloads.
- Use: permitting/social-license/execution timing before press coverage.
- Gate: stakeholder comments are claims; agency orders are authority. Absence is not evidence until reporting lag and coverage are verified.

### 3. Grid/interconnection project realization
- LBNL Queued Up provides project-level U.S. interconnection data, including queue status, agreements, withdrawals and COD duration. The 2026 edition shows only a minority of historical queue capacity ultimately reaches commercial operation and median queue-to-COD duration for built 2025 projects exceeded five years in regions with data.
- ERCOT large-load reports expose requested load pipelines; request GW is not realized load.
- Use: realization haircut, Time-to-Power, project execution probability.

### 4. Patent legal state, not just patent count
- KIPRISPlus now exposes legal-status history and other event data in addition to publications/citations.
- WIPO trend data provides technology-field direction and patent-family trends.
- Use: technology option, competitive map, legal-life/transfer monitoring.
- Gate: patent count is never direct moat/revenue evidence.

### 5. Labor demand as capability/capacity signal
- Academic evidence shows job postings can be transformed into granular labor-demand measures and firm-level skill/capability indicators.
- Use: hiring intent, new skill stack, capacity build, bottleneck detection.
- Gate: duplicates, staffing intermediaries, outsourcing and stale requisitions.

### 6. Physical production/logistics nowcasting
- U.S. Census M3: shipments, new orders, unfilled orders, inventories.
- Federal Reserve G.17: production, capacity and utilization with detailed methodology and revision behavior.
- AAR weekly rail traffic: commodity carloads and intermodal units.
- Customs/official trade: product-country flows.
- Use: cycle state and physical confirmation.

### 7. Financing market versus positioning market
- FINRA TRACE and fixed-income APIs provide bond/structured-product activity and market breadth/sentiment data.
- Federal Reserve SLOOS gives quarterly credit-standard and loan-demand state, including NDFI conditions.
- SEC structured insider datasets, KRX short/lending/flow data and FINRA short interest provide behavior/positioning signals.
- Use: TRACE/credit for WACC and funding; positioning only for monitoring/post-freeze.

### 8. Remote sensing as independent physical verifier
- Public satellite/night-light research supports monitoring construction/economic activity, and ESA projects demonstrate construction and industrial-site monitoring.
- Use: verify physical construction or activity when reporting is lagged.
- Gate: must calibrate to ground truth; cloud/light/process artifacts make it verification-request data by default.

## Dynamic Economic Peer Graph

Traditional classifications should be only priors. Hoberg-Phillips text-based network industry research shows firm-specific, time-varying product-description similarity can identify competitors better than static industry codes for several economic outcomes. RocketSLA should use filing product text + end-market mix + supply-chain topology + patent similarity + business-model features to produce **candidate Economic Twins** for Beta/PER.

This does not automatically accept peers. The hierarchical Beta/PER engines still perform the final systematic-risk/economic-driver audit.

## New mandatory anti-bias fields

Every signal should carry:
- event time
- effective-as-of time
- publication time
- RocketSLA first-seen time
- revision time
- expected reporting lag

This is required to avoid look-ahead bias in backtests and to distinguish a late revision from information that was actually available to an investor at the time.

## Negative-evidence rule

`No record found` may become `NO_EVENT` only when source coverage, reporting obligation, lag, endpoint health and alternate channels have all been checked. Otherwise preserve `NOT_OBSERVED`.

## Priority implementation order

1. Market-data role split in the central Knowledge Placement policy.
2. Procurement + regulatory + grid project-realization adapters.
3. Dynamic Economic Peer Graph for Beta/PER candidate peers.
4. Patent legal-state and job-skill monitoring.
5. Physical/logistics nowcast feeds.
6. Financing/positioning split.
7. Remote-sensing verifier only for high-value projects/facilities.
