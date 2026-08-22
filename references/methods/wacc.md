# WACC — Deprecated Simplified Fallback

This file intentionally contains no independent WACC procedure. The canonical implementation is `docs/V04_ROCKETSLA_EXTENSION.md` plus `src/valuation_engine/risk.py` and `src/valuation_engine/wacc.py`.

A simple Blume-adjusted beta or generic CAPM/small-cap-premium shortcut is only a minimal diagnostic and **must not override** the 4-Level Hierarchical Bottom-up Beta, currency-consistent risk-free rate, exposure-adjusted country risk, marginal Cost of Debt, market-value target capital structure, customer-advance credit gate, terminal consistency or cross-method double-count gates.

If the canonical inputs cannot be supported, WACC is not repaired with a simplified plug; the affected valuation is blocked or explicitly limited.
