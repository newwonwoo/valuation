# SEC EDGAR primary-source provider

The SEC provider uses injected transports and official SEC endpoint contracts only:

- `https://data.sec.gov/submissions/CIK##########.json` for issuer identity and filing metadata;
- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` for exact XBRL concepts;
- `https://www.sec.gov/Archives/edgar/data/...` for the accepted primary filing document.

Rules:

- CIK is normalized to exactly ten digits and must match the returned payload.
- Filing identity is accession-number exact; unsupported forms are ignored rather than guessed.
- Company Facts extraction requires an exact taxonomy, concept, unit, accession, form and report-end match. No account-name fuzzy matching is allowed.
- Multiple conflicting values for the exact filing/concept fail closed.
- Filing metadata and Company Facts must agree on filing date.
- Primary filing documents are addressed by the canonical SEC Archives path and hashed byte-for-byte after retrieval.
- SEC data enters the same authorized primary Evidence contract used by other filing/IR/regulator sources; target-market/Street fields remain forbidden pre-Freeze.
- Network policy, SEC User-Agent identification, retries and credentials (if any downstream service requires them) stay outside deterministic valuation code through the injected transport.

The first targeted acceptance users are Oracle, Bloom Energy and GE Vernova; company-specific KPI metric specs remain a separate task from the transport/provider contract.
