# Dilution & Hybrid-Security Treatment

> **Operator supplement.** Current target-equity price is post-freeze information and must not be used to choose intrinsic dilution assumptions. Model contractual paths and economic consequences first.

Potential dilution includes convertibles, exchangeables, warrants, options, stock compensation, ATM programs, rights offerings and other contingent share issuance.

## Collect the contract first

For each instrument capture:
- principal / units outstanding,
- conversion or exercise price and reset/refixing formula,
- exercise window and maturity,
- issuer/holder put and call rights,
- cash/physical/net-share settlement rules,
- collateral and covenants,
- coupon and redemption premium,
- historical exercises/redemptions,
- shares reserved or treasury shares used for settlement.

## Intrinsic treatment before market-price load

Do not classify a convertible as “dilution” or “cash redemption” solely by comparing its strike with today’s stock price before `INTRINSIC_VALUE_FREEZE`.

Instead model contractual/economic states:
1. conversion/share-settlement path,
2. redemption/refinancing path,
3. reset/refixing path where applicable,
4. liquidity shortfall/default path when material.

Use the scenario’s own model-implied equity value and the security terms when conversion economics are needed. Current market moneyness may be described only after freeze as a market-state comparison.

## Double-count rules

- Do not deduct the same convertible as debt and also add the full conversion shares in the same scenario unless the security economics genuinely require both components.
- Exchangeable bonds may not issue new shares but can release treasury/held shares and create supply overhang; distinguish per-share dilution from market-float effects.
- Stock compensation is not “free” because it is non-cash; reflect the economic cost and/or dilution consistently.

If material settlement paths cannot be bounded, the per-share valuation is `VALUATION BLOCKED` or must be shown as an explicitly separated scenario range.
