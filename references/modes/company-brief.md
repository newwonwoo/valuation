# Mode C — Company Brief

A fact-first company brief for counterparties, competitors, acquisition targets or quick diligence. It is neither valuation nor credit underwriting.

## Suggested collection

```bash
python scripts/dart.py find  <company> <YYYYMMDD>
python scripts/dart.py brief <receipt_no>
```

## Output

1. business/segment structure and revenue mix,
2. period-matched growth and margins,
3. customers/geography/concentration,
4. production capacity, utilization and key sites where relevant,
5. concise balance-sheet/funding snapshot,
6. ownership/subsidiaries,
7. recent material disclosures,
8. Industry DNA route and the evidence still missing to finalize it.

## Guardrails

- Do not include fair value, rating, buy/sell language or position advice.
- Separate fact, management plan and analyst inference.
- Use `NOT_OBSERVED` rather than claiming absence when coverage is incomplete.
