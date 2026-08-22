# Mode D — Disclosure / Verification Monitor

Monitor evidence that proves, weakens or kills an existing thesis. The monitor does not re-price the company by itself.

## Rules

- Monitor **verification events**, not share-price moves.
- Primary filings and company disclosures are direct signals; broker/news items are discovery leads unless independently verified.
- No event observed is not automatically `NO_EVENT`; apply the negative-evidence and source-health gates.
- A material new signal marks affected mechanisms/assumptions dirty and creates `REVALIDATION_REQUIRED`.
- Never overwrite a frozen intrinsic run. Material evidence starts a new run.

## Typical trigger families

1. contract/order/backlog conversion,
2. customer advances/contract liabilities,
3. guidance or capacity-ramp changes,
4. permits/interconnection/project-realization states,
5. financing/refinancing/collateral events,
6. dilution/hybrid-security events,
7. clinical/regulatory milestones,
8. accounting-definition or source revisions.

## Daily/periodic output

Report only new evidence, affected mechanism/assumption IDs, current source status, and whether revalidation is required. If nothing material changed, state that succinctly without inventing a signal.
