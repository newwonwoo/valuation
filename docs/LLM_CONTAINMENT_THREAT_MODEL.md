# LLM Containment Threat Model

Five model-controlled surfaces now feed the engine: the Intelligence Officer,
the Blind Red Team, the Bridge Analyst, the Filing-Locator Analyst, and the
conversational dispatcher. This document is the adversarial audit of each —
what the model controls, how it could escape, and what stops it. Every row is
backed by an attack test in `tests/test_llm_escape_vectors.py`; the containment
is proven by running the attack through the real pipeline, not by asserting the
guard exists.

## The one rule

A model may **propose** and **point**; it may never **commit** or **assert a
number**. Every value that reaches the arithmetic is re-derived deterministically
from cited Evidence, and every Evidence number is re-extracted from the source
document. The model's leverage is confined to *which* evidence and *where* — and
both are checked.

## Surface-by-surface

### Bridge value channel — CONTAINED

The model proposes `new_value`; the compiler recomputes it from the cited
Evidence through the declared transform and rejects any mismatch
(`PROPOSAL_RECALC_MISMATCH`). The un-recalculated fields it also controls
(`old_value`, `confidence`) were attacked with absurd values and **do not move
the valuation** — they are provenance annotations, not inputs.

### Text channel — CONTAINED

Hypothesis statements and the red-team counter-thesis are free text. A smuggled
"적정주가 95,000원 / 목표가 110,000원" in either **never reaches the final
report**: the report is rendered deterministically from the frozen valuation and
audit artifacts, not from model prose. (Pre-freeze, market-token guards and the
market-comparison evidence-layer ban already keep price claims out of the
intrinsic path.)

### Filing-locator laundering — CONTAINED (this audit's finding)

The locator verifier already refused fabrication (a quote that is not in the
document, missing the metric anchor, an invented unit). This audit found two
escapes it did **not** catch, because the number was real and the anchor was
present:

1. **Prior-period laundering** — pointing at "전기말 수주잔액은 900,000
   백만원" puts last year's figure into the current slot.
2. **Forward-looking laundering** — pointing at "…2,000,000 백만원에 이를
   것으로 전망" forges the evidence *layer*, turning a forecast into a realized
   fact — the more dangerous of the two.

Fix: `_PERIOD_DISQUALIFYING_TERMS` — a quote carrying a prior-period marker
(전기/전년/전분기/직전…) or a forward-looking marker (전망/예상/계획/목표/추정…)
is refused. The current-period disclosure still extracts. A metric the model
cannot locate on a current-period basis becomes a named coverage gap, exactly
as if undisclosed.

Residual, stated: a genuinely current-period quote that is nonetheless the wrong
column (two adjacent current figures under one label) is not distinguishable by
vocabulary. The full extraction receipt — member SHA-256, normalized-text span,
matched text — exists so the operator can reopen the filing at the span. That
review is the operator's, not the model's, and is the documented boundary.

### Red-team blocking flag — SAFE BY ROLE

The Red Team controls `blocking` on its own issues. Suppressing an issue does
not pass a value error: the Red Team is a falsification safety net, not the
value verifier. Value integrity is held independently by the compiler
(recalc), the cross-method double-count audit (unique economic paths), the
DCF/PER consistency gate and the generic audit gate — none of which the Red
Team can wave through.

### Evidence composition — DISCLOSURE, BY DESIGN

If a valuation stands entirely on operator-declared underwriting (the honest
state of a first cold run), the evidence-composition guardrail does not block —
it **discloses**: AUDIT_GATE goes WARNING and the report states the intrinsic
value rests on judgment, not filings. A knowingly-declared run completes; a
reader is never misled about what backs the number.

### Conversational dispatcher — OUT OF THE AUTHORITY BOUNDARY BY CONTRACT

The chat LLM that turns "ㅇㅇ 분석해줘" into a run launches the CLI and hands
back the engine's own report artifact, hashes intact. It holds no authority: it
does not choose the number, and the documented rule is that it never paraphrases
the numbers. Deployment declarations it would pass (method, as_of, underwriting
file) are the operator's, reviewed as configuration — and every one still passes
through the same in-run checks.

## Regression lock

`tests/test_llm_escape_vectors.py` runs each attack above through the real
pipeline or the real verifier. A change that reopens any escape fails a test
whose name says what was reopened.
