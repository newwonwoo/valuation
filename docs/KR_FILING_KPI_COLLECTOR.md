# KR Filing-KPI Collector

`src/valuation_engine/kr_filing_kpi_collector.py` ·
`config/kr_filing_kpi_patterns.yaml`

## Another island, same treatment

`dart_kpi.py` — the exact-locator extractor for DART original filings, with
member SHA-256s, normalized-text spans and a fail-closed exactly-one-match
rule — existed with 396 lines and tests, and had **no engine caller**. The same
pattern as `run_probability_engine_v3` before its bridge: the capability was
built, the entrance was not. Sanil's backlog numbers entered through a provider
snapshot YAML while the extractor that could have read them from the filing sat
unused.

This module is the entrance: a `CollectorCapability`
(`kr-dart-filing-kpi`, source `KR_OPENDART`) that the collection planner can
route archetype evidence requirements to. `KR_OPENDART` is a
`company_primary`/`listed_companies` source, so it is a fallback candidate for
every metric of a listed company — no registry change was needed.

## What it reads

The semi-standard operating tables of Korean statutory periodic filings
("II. 사업의 내용"):

| metric | table | canonical unit |
|---|---|---|
| `orders` | 수주상황 · 수주총액 | KRW_million |
| `backlog` | 수주상황 · 수주잔고 | KRW_million |
| `capacity` / `nameplate_capacity` | 생산 및 설비 · 생산능력 | KRW_million |
| `production` | 생산 및 설비 · 생산실적 | KRW_million |
| `utilization` | 생산 및 설비 · 가동률 | ratio |

The patterns describe the **filing format**, never a company; the same YAML
runs against every corp code. Two declared limitations, written into the
config's own purpose text:

- money-denominated tables only for capacity/production (백만원/억원/천원); a
  physical-unit disclosure (MW, 대, 톤) needs its own dimension-specific metric
  entry because one extraction spec cannot mix unit dimensions;
- a filing that formats its table differently simply yields no Evidence for
  that metric — the coverage check then names the gap. Extraction never
  guesses, and an ambiguous match (two locations) is a gap, not a coin flip.

Filing selection is deterministic: the latest periodic report at or before the
run's `as_of`, with the fiscal period parsed from the report title's
`(YYYY.MM)` suffix — receipt dates are when knowledge arrived, period ends are
when economics happened, and the two are never conflated.

## The receipt on every number

Each extracted value becomes an `EvidenceRecord` whose `source_ref` embeds
`member=…&member_sha256=…&normalized_sha256=…&normalized_span=start:end`, and
whose notes carry the matched text. A reviewer can reopen the filing archive
and put a finger on the digit. Two new money units (`KRW_thousand`,
`KRW_hundred_million`) were added to `actual_units` because statutory tables
quote them.

## Effect on the cold-start boundary

Before: the probe stopped at `PRIMARY_EVIDENCE_COLLECTION` with
*"no runnable collector is available"*. After:

```
recovery_required: required primary evidence missing:
metrics=realized_price, benchmark_price, inventory, cash_cost,
        input_price, output_price, product_yield, plant_runs, turnaround
```

The engine now collects what filings disclose and names, metric by metric, the
market-side industry evidence that still lacks a source connector — exactly the
metrics the industry source registry (KOSIS/KIET/MOTIE/KEEI…) was researched
for. The boundary is no longer "can it run" but "which series are wired", which
is the correct shape for a one-person securities operation: every widening of
coverage is a reviewed connector, not a hand-typed number.

## The LLM locator path (implemented)

For filings the static patterns miss, the model gets the reading job — inside
the cage (`src/valuation_engine/llm_filing_locators.py`):

1. The filing-locator analyst sees the filing's normalized visible text and
   the target metrics (definition, anchor vocabulary, allowed unit tokens),
   and answers with **locators**: member path, a verbatim quote containing
   anchor + value + unit, and which characters are the value and the unit.
2. The deterministic verifier rejects: a quote that does not occur exactly
   once in that member (a fabricated number has no quote to be found in), a
   quote without the metric's anchor terms (no relabeling an unrelated
   figure), an unregistered unit token, a value_text not inside the quote.
3. A surviving locator compiles into an ordinary `DartKPIExtractionSpec`
   whose pattern is the escaped quote, and `extract_dart_kpi` re-reads the
   document through the same machinery as the static path — same member-hash
   and span receipts, notes marked "LLM locator (verified)".

A model that lies loses the round, not the run: rejected locators become named
coverage gaps, exactly as if the metric were undisclosed. Beyond fabrication,
the verifier also refuses **period laundering**: a quote carrying a prior-period
marker (전기/전년/전분기…) or a forward-looking one (전망/예상/계획/목표…) cannot
enter as a current realized value — the second forges the evidence layer itself,
turning a forecast into a fact. See `docs/LLM_CONTAINMENT_THREAT_MODEL.md`. The
stated residual is two adjacent current-period figures under one label, which
vocabulary cannot separate; the receipts exist so a reviewer can reopen the
filing at the span — that review is the operator's, not the model's.

The static patterns remain the fast path: zero model calls for statutory
layouts, the locator pass only for what they miss.
