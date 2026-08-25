# KR OpenDART LIVE_PRIMARY Provider Foundation

## 1. Scope

`valuation_engine.kr_opendart_provider` is the first repository-provided production source bundle for the canonical `LIVE_PRIMARY` CLI.

It provides two official-source capabilities:

1. exact Korean company resolution from the OpenDART `corpCode.xml` archive;
2. request-scoped standard financial Evidence from the OpenDART single-company full-financial-statements endpoint.

It deliberately does **not** pretend to implement the remaining provider universe. Industry Knowledge, freshness policy, segment decomposition, Industry DNA, scanners, LLM Staff, Risk, evaluator registries, valuation-plan inputs, Street and market loaders remain explicit typed extensions.

No extension is replaced by a generic fallback.

## 2. CLI composition

A private operator module may expose a configured factory object directly:

```python
from valuation_engine import (
    KRLiveProviderExtensions,
    KRLiveRuntimeFactory,
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.live_indexers import HttpTransport
from valuation_engine.scenario_binding import ScenarioBindingSpec

transport = HttpTransport(timeout_seconds=20, max_bytes=8_000_000, retries=1)

build_config = KRLiveRuntimeFactory(
    network=OpenDartNetwork.from_http_transport(transport),
    filing=OpenDartFilingSelection(
        business_year="2025",
        report_code="11011",
        fiscal_period_end="2025-12-31",
        checked_at="2026-03-20",
        segment_id="company",
    ),
    extensions=KRLiveProviderExtensions(
        industry_snapshot_loader=load_industry_snapshot,
        freshness_loader=check_source_freshness,
        segment_decomposer=decompose_segments,
        industry_dna_router=route_industry_dna,
        scanner_runners=scanner_runners,
        intelligence_officer=researcher_a,
        red_team_officer=blind_red_team,
        bridge_analyst=bridge_analyst,
        evaluator_registry_loader=load_evaluator_registry,
        valuation_plan_inputs_loader=load_valuation_plan_inputs,
        beta_loader=load_beta_universe,
        wacc_loader=load_wacc_inputs,
        per_loader=load_per_inputs,
        street_loader=load_street_reports,
        market_loader=load_market_price,
    ),
    scenario_binding_spec=ScenarioBindingSpec(
        ("Bear", "Base", "Bull"),
        required_assumption_keys,
    ),
    method_choices=method_choices,
    market_currency="KRW",
)
```

Then:

```bash
valuation-engine "분석시작 삼성전자" \
  --provider-factory my_private_runtime:build_config \
  --state-root ../valuation-vault-local \
  --jurisdiction KR
```

The object is callable and satisfies the `LiveRuntimeConfigFactory` contract.

## 3. Credential behavior

`OpenDartNetwork.api_key` is optional and excluded from its dataclass representation.

- When supplied explicitly, it is used only to build official OpenDART request URLs.
- When omitted, existing OpenDART URL builders read `DART_API_KEY` only when the resolver or collector actually executes.
- Merely importing or constructing the factory does not require the credential.

The bounded `HttpTransport` removes URL query strings and original exception messages from `SourceFetchError`. API keys therefore do not enter ordinary stage rationales through transport failures. The CLI additionally suppresses provider rationales and raw blocking reasons on blocked terminal output.

Private operator logging must apply the same rule.

## 4. Company identity

The resolver accepts an exact:

- six-digit Korean stock code;
- eight-digit OpenDART corporation code;
- normalized exact legal name.

It returns:

```text
KR:DART:<8-digit corp_code>
```

as the stable target ID. The financial collector extracts the corporation code only from that exact identity form. Another jurisdiction or malformed target ID fails closed.

## 5. Filing selection

`OpenDartFilingSelection` requires explicit:

- business year;
- report code;
- issuer fiscal-period end;
- source check/publication upper-bound date;
- CFS or OFS scope;
- output segment ID;
- exact `DartFactMetricSpec` set.

Report codes and account IDs are not inferred from company names. The fiscal-period end is never manufactured from the report code.

The default standard facts are:

- revenue;
- operating income;
- net income;
- total assets;
- total liabilities;
- total equity;
- cash and cash equivalents.

Company-specific facts such as backlog, customer advances, capacity, utilization, RPO, clinical evidence or reserves require explicit metric specs and, where financial-statement facts are insufficient, separate filing-note, IR or regulatory collectors.

## 6. Request-scoped collection

The Collection Plan invokes a collector with the metrics authorized for its current task. The OpenDART wrapper selects only matching metric specs before calling the official endpoint parser.

It blocks when:

- the target ID is not a Korean OpenDART identity;
- the task asks for a metric outside the declared collector capability;
- no supported metric remains;
- returned filing rows do not match corporation, business year or report code;
- interim cumulative-flow fields are missing;
- fiscal, receipt and publication dates are inconsistent;
- currencies conflict;
- one metric resolves to different exact values;
- the underlying collector emits a metric outside the current task.

This prevents one broad financial collector from silently emitting unrelated facts into a narrower Collection Plan task.

## 7. Segment boundary

The first bundle emits filing Evidence under one explicitly configured `segment_id`.

That is appropriate for a single-segment or company-level financial Evidence slice. For a multi-segment company, segment note parsers and separately scoped collectors are still required. A company-wide fact must not be duplicated into each segment merely to satisfy coverage.

When the configured segment does not match the current Collection Plan, the existing exact segment/metric coverage checks block the run rather than accepting the fact under the wrong economic scope.

## 8. Remaining production providers

This bundle does not complete:

- DART filing-note and business-report body parsing;
- company IR and earnings-document parsing;
- industry snapshot ingestion and source freshness implementations;
- segment decomposition and Industry DNA models;
- mandatory scanner and FundingScanner libraries;
- live Beta, WACC and Warranted PER providers;
- Driver-to-FCFF models and missing exact evaluators;
- probability production cohorts;
- Street source access;
- current market-price source access;
- actual-company acceptance for OCI, Oracle, Bloom Energy and GE Vernova.

Those remain explicit backlog items. The purpose of this foundation is to replace duplicated private OpenDART plumbing with one tested official-source contract, not to relabel the full provider stack as complete.
