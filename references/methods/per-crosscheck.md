# PER Cross-check — Deprecated Simplified Fallback

Canonical PER logic is `docs/V04_ROCKETSLA_EXTENSION.md` and `src/valuation_engine/per.py`: Core Fundamental, Expansion-Adjusted and Market-Realization PER, plus the DCF–PER Assumption Consistency Gate. This file exists only to point operators to that implementation; it is not an alternate theoretical-PER model.

If coverage/data are insufficient to run the canonical PER logic, report PER as unavailable rather than backsolving from current price, Street target P/E, or a raw peer average.
