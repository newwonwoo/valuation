# Authorized KR Risk Provider Pack

`authorized_risk_providers.py` connects the official/authorized market-data collectors to the existing strict Beta/WACC runtime contracts without weakening pre-freeze isolation.

## Inputs

- KRX regression Beta (`BetaEstimate`)
- peer debt, market-value equity and tax observations from authorized primary/accounting sources
- Bank of Korea ECOS KRW risk-free rate
- Damodaran mature-market ERP and separate country-risk premium
- an authorized ECOS corporate-bond/borrowing benchmark matched to an explicitly sourced target credit rating and maturity

## Target capital structure

The pack uses equal-weighted peer `debt / (debt + market-value equity)` across unique authorized peers. It does not read the target company's current market capitalization, target price or Street data before Intrinsic Freeze.

The resulting structure is emitted as `PEER_NORMALIZED_MARKET_VALUE` and is reused unchanged for target Beta relevering and WACC.

## WACC source separation

- risk-free: currency-matched ECOS observation
- equity risk premium: Damodaran mature-market ERP
- country-risk premium: separate Damodaran CRP; never added twice to the total ERP
- marginal pre-tax debt cost: ECOS market borrowing benchmark matched to explicit rating/maturity provenance
- country-risk lambda: defaults to zero; non-zero exposure requires a separate exposure source reference

Rates require an explicit percent or decimal/ratio unit. Unknown units fail closed rather than guessing.

The provider pack produces existing `LiveBetaUniverse` and `LiveWACCInputs` objects. All existing Evidence-ID, normalized-return-convention, currency-consistency, same-capital-structure and target-market-leakage gates therefore remain authoritative.
