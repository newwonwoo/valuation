# Mode B — Credit-Risk Screening

A public-disclosure screening view of survival, liquidity and refinancing risk. It is **not** a substitute for lender underwriting, covenant-document review, collateral appraisal or legal advice.

## Suggested collection

```bash
python scripts/dart.py find   <company> <YYYYMMDD>
python scripts/dart.py credit <receipt_no>
```

Also inspect auditor going-concern language, financing/refinancing disclosures, guarantees, collateral, major litigation and material debt/security filings.

## Core lenses

- liquidity runway and near-term maturities,
- interest coverage and fixed-charge burden through the cycle,
- debt composition, covenants, refinancing concentration and marginal borrowing cost,
- receivable/inventory quality and working-capital reversals,
- contingent liabilities, guarantees, collateral already pledged and related-party funding,
- capital/regulatory constraints for financial institutions,
- customer advances/refundable obligations and their contractual conditions,
- cash burn and dilution/refinancing paths for loss-making companies.

Thresholds are screening aids only; industry, volatility, collateral, contractual seniority and funding access can dominate a single ratio. Preserve trend, denominator definition and period matching.

## Separation from valuation

A low intrinsic equity value is not evidence of default. Conversely, a highly valued equity can still have refinancing or covenant risk. Credit findings may enter the valuation workflow only through the explicit Funding/WACC/FCFF economic path and double-count gates.
