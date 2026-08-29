# Generic Live Providers

Closing the nine empty provider slots that block a cold start — the design and
its rules, before the code.

## The rule that defines "generic"

A provider is generic when **its code contains no company fact**. Every company
fact must arrive through one of three doors:

1. **Resolution** — the identity that `COMPANY_RESOLUTION` produced from the
   user's query (corp code, ticker, target_id).
2. **Primary sources** — documents and facts fetched by that identity from an
   authorized source (OpenDART), hash-bound into Evidence.
3. **Proposal** — output of the injected LLM transport, which downstream
   deterministic validation re-derives and can reject.

Deployment facts — an API key, an HTTP transport, a model transport — are
injectable without breaking company-neutrality: the same code path serves every
company. This is the same standard the repository already applies to
`MARKET_PRICE_LOAD` ("actual price source is supplied by caller").

What is *not* allowed is the fourth door the hand-written modules used: a
Python file or spec row that carries the judgment itself (a normalized multiple,
an FCFF path, a hypothesis text) keyed to one company. `stage_capability.py`
marks those modules company-bound precisely so they cannot satisfy a capability
axis.

## Slot-by-slot design

### `industry_snapshot_loader` — `opendart_filing_snapshot_loader`

Builds `IndustryKnowledgeSnapshot` from the OpenDART filing index
(`list.json`, already wrapped by `live_indexers.index_opendart_filing_list`):
periodic reports for the resolved corp code within a lookback window, filtered
to `rcept_dt <= as_of` so the snapshot never contains a filing that was not
knowable at the cutoff. Each filing becomes an `AuthoritativeEvidenceLineage`
whose `published_at`/`first_seen_at` derive from the DART receipt date and whose
content hash is the filing row's content fingerprint. Fail-closed when no
periodic filing exists in the window.

### `freshness_loader` — `filing_cadence_freshness_loader`

Deterministic policy over the snapshot's own lineage: the latest periodic filing
must be younger than `max_age_days` (default 120 — quarterly cadence plus
grace). Stale ⇒ `EXPECTED_RELEASE_MISSED` as a warning; an empty lineage ⇒
`SOURCE_FAILURE`, which blocks. No hand-written "CLEAN" rows.

### `segment_decomposer` / `industry_dna_router` — KSIC classification

Both share one classification: `config/kr_industry_classification_map.yaml`
maps KSIC industry-code prefixes (from the DART `company.json` profile) to a
sector adapter, an archetype set, and the segment economic-structure fields.
Longest-prefix match; an unmapped code fails closed naming the code, because
guessing an archetype is a valuation decision.

The decomposer emits a single whole-company segment (`core`) citing the
snapshot's filing evidence. This is deliberate: finer segmentation without
segment-note extraction would be invented structure. One evidence-backed segment
is honest; the hand-written modules also used one segment.

The router copies the segment's structure fields and attaches the mapped
`sector_adapter` and archetypes — the map is data, the routing is code, and the
same code routes every company.

### `scanner_runners` — evidence-ledger screens

`config/generic_scanner_screens.yaml` declares, per scanner ID from
`archetype_control_requirements.yaml`, the evidence-metric keywords the scanner
screens for. The generic runner reads the run's own `EvidenceLedger`:

- matching active Evidence ⇒ finding citing those Evidence IDs, with
  verification requests from the archetype's kill-condition templates;
- no matching Evidence ⇒ an explicit `WARNING` finding stating the scan could
  not observe its subject (never a silent pass).

This is `PARTIAL_LIVE` by design: it screens what collection brought in; it does
not reach out to external scan sources. That limitation is written into the
readiness reason, not hidden.

### `valuation_plan_inputs_loader` — `conventional_valuation_plan_inputs_loader`

One binding per decomposed segment under fixed key conventions
(`ownership`, `ev_adjustment`, `diluted_shares`), reporting unit from
configuration. The keys are conventions the Bridge Analyst is told to propose
against; the plan compiler still refuses a plan whose keys the compiled
scenarios do not carry.

### `evaluator_registry_loader` — `composed_generic_registry_loader`

Composes the existing per-family live loaders (normalized multiples, explicit
FCFF DCF, backlog burn) from the run's declared `SegmentMethodChoice`s. No new
evaluator math; only composition.

### The three LLM staff — `generic_llm_staff` + `llm_transport`

The one genuinely new capability. Split into two parts:

- **`ProposalTransport`** — the injected model: one method,
  `complete(role, prompt) -> str`. The engine never imports a vendor SDK and
  never holds a key (repo doctrine: credentials stay outside the public
  repository). `ScriptedTransport` serves tests and offline runs.
- **`GenericIntelligenceOfficer` / `GenericRedTeamOfficer` /
  `GenericBridgeAnalyst`** — everything around the model, company-neutral:
  - render `LLMStaffContext` into a deterministic prompt: the Evidence table
    (id, metric, value, unit, layer, source, dates), scanner findings, the
    module requirement plan, and the writing contract;
  - demand strict JSON; parse into the typed records (`HypothesisRecord`,
    `ContextStrengthLinkageDecision`, `RedTeamProposal`, `BridgeDraft`);
  - reject unknown fields, unknown Evidence IDs, unregistered transforms;
  - bounded repair: on a validation error, re-prompt once with the error text,
    then fail closed.

The containment already built (`llm_proposal_scope`, output-token sweep,
proposal-vs-recalc tolerance, blind Red Team market isolation) applies to these
officers automatically, because they run through the existing
`run_intelligence_officer` / `run_red_team` / `run_bridge_analyst` wrappers.
This moves the judgment *inside* the seven-layer boundary: a bridge value the
model invents that does not re-derive from cited Evidence dies in the compiler
with `PROPOSAL_RECALC_MISMATCH`.

The transport being scripted or live changes proposal *quality*, never
*authority*: nothing a transport returns can commit an assumption, weight a
probability, or touch market data pre-freeze.

### Factory — `generic_live_providers.build_generic_kr_live_providers`

Assembles all of the above plus the existing generic pieces (OpenDART resolver,
fact collector, authorized risk/PER packs where the method requires them) into a
validated `LivePrimaryProviders`. This is the function whose existence flips
`probe_cold_start` from "cannot assemble" to "assembles; an executed cold run is
now required".

## The executed cold-start probe

`cold_start_probe.execute_cold_start_probe` runs the canonical attested runtime
on 한빛제강 — a fictional steelmaker served by an in-memory OpenDART stub, with
no module, spec row or fixture file anywhere in this repository. The probe runs
in CI (`validate_stage_capability.py`) and feeds `PROJECT_STATUS.md`; its result
is the engine's own, never a hand-written status:

```
reached 7/33 — COMPANY_RESOLUTION … MODULE_REQUIREMENT_PLAN
stopped at PRIMARY_EVIDENCE_COLLECTION:
  not_implemented: no runnable collector is available for the compiled CompanyCollectionPlan
```

That stop is the honest current boundary: the archetype's required industry
evidence (realized_price, production, cash_cost, …) has no source connector yet
beyond the core DART financial facts. The blocked run publishes no intrinsic
value. When source breadth grows, the probe reaches further and the scripted
transport starts failing loudly at RESEARCHER_A — the demand for the next
honest fixture, not a free pass.

## 실행법 — the one line

```bash
export DART_API_KEY=...                                   # OpenDART key (caller's)
export VALUATION_LLM_TRANSPORT=my_deploy.transport:build  # the model seat (caller's)
export VALUATION_METHOD=commodity_price_taker/normalized_multiple
export VALUATION_UNDERWRITING_PATH=runs/<company>/underwriting.yaml   # declared judgments
# optional post-freeze inputs:
#   VALUATION_MARKET_CONFIG / VALUATION_STREET_EXPORT / VALUATION_MARKET_CURRENCY

PYTHONPATH=src python -m valuation_engine.cli "분석시작 <회사명|종목코드>"     --provider-factory valuation_engine.generic_kr_cli:factory
```

A chat front end ("ㅇㅇ 분석해줘") is a thin dispatcher over exactly this call,
with one rule that keeps the last mile honest: the conversational LLM launches
the run and hands back the engine's own report artifact — hashes intact — and
never paraphrases the numbers. The transport builder is the caller's ten lines
(their vendor, their key, outside this repository); the engine imports no model
SDK. A run with no underwriting file does not fail at configuration — it fails
closed later at evidence coverage with the missing judgments named, which is
the correct first-run experience: the engine hands back a work order.

## What this does and does not claim

- It claims the pipeline **runs** for an unseen KR company: providers assemble,
  every stage has an implementation, judgments enter only through the three
  doors.
- It does **not** claim analysis quality: a scripted transport proves plumbing,
  not insight; the KSIC map covers the codes it covers; the scanners screen only
  collected evidence. Each limitation lives in the readiness reason for its
  stage, and the capability validator keeps those words honest.
