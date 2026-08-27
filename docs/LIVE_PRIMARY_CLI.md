# LIVE_PRIMARY CLI Contract

## 1. Purpose

The CLI command:

```text
분석시작 <기업>
```

is a user-facing entrypoint into the canonical PRISM `LIVE_PRIMARY` Control Plane. It must not select the OCI regression fixture, PRIMARY_SHADOW data, a generic evaluator, or market-implied assumptions merely because a production provider is unavailable.

The runtime boundary is:

```text
CLI command
→ LiveAnalysisRequest
→ operator-supplied LiveRuntimeConfigFactory
→ validated LivePrimaryRuntimeConfig
→ run_prism()
→ ControlledRunResult
→ stage progress + final report or VALUATION BLOCKED
```

## 2. Provider factory

Supply a Python import specification:

```text
python.module:callable
```

through either:

```bash
--provider-factory my_runtime.providers:build_config
```

or:

```bash
VALUATION_LIVE_PROVIDER_FACTORY=my_runtime.providers:build_config
```

The callable receives:

```python
@dataclass(frozen=True)
class LiveAnalysisRequest:
    command: str
    company_query: str
    state_root: Path
    run_id: str
    jurisdiction: str | None
```

and must return `LivePrimaryRuntimeConfig`.

The factory may assemble live transports, credentials, source clients, LLM Staff callbacks, scanner runners, risk providers, evaluator registries and post-freeze loaders. Secrets, paid reports and private position/state data remain outside the public repository.

Ordinary import-time and provider-factory exceptions are converted into stable CLI error codes. Their exception type may be reported, but the original exception message is not printed because it may contain credential-bearing URLs, authorization headers or private response bodies. `KeyboardInterrupt` and `SystemExit` remain process-control signals rather than being swallowed as provider errors.

## 3. Identity locks

The factory cannot change:

- CLI-generated or explicitly supplied `run_id`;
- requested `state_root`;
- company query in `CompanyResolutionRequest`;
- explicit jurisdiction constraint.

These checks happen before Control Plane execution or state persistence.

A blank jurisdiction is rejected as `INVALID_LIVE_ANALYSIS_REQUEST` before the provider factory runs. Jurisdiction aliases such as `KR` and `KOR` are normalized by the canonical jurisdiction helper.

The company resolver is additionally wrapped at the first Control Plane stage. When a jurisdiction is locked, the returned `ResolvedCompanyIdentity.jurisdiction` must match after canonical alias normalization. A factory cannot copy `KR` into the request but return a US identity and proceed to downstream collection.

The factory is not allowed to reinterpret `분석시작 삼성전자` as another target, redirect state to another directory, reuse an unrelated run ID, or silently choose a different jurisdiction.

## 4. Installed registry resources

The console entrypoint must work from an installed wheel outside a repository checkout. All YAML runtime registries are packaged under:

```text
valuation_engine._registry_data
```

For class-default registry fields, `build_live_runtime_config()` binds the canonical package resources. Valid explicit custom registry paths remain supported and are not replaced.

An importable installed registry package is authoritative. If the package exists but one requested YAML member is missing, the runtime fails closed as `LIVE_RUNTIME_REGISTRY_UNAVAILABLE`; it never consults an unrelated parent-level `config/` directory. Repository fallback is permitted only when the installed resource package is unavailable and independent project, source and canonical-config markers positively identify a real source checkout.

The package-backed fields are:

- Control Plane stage registry;
- archetype module registry;
- archetype control requirements;
- industry source registry;
- Unit Contract registry.

The method-capability and archetype registries use the same package-first authority. A missing explicit custom registry path fails as `INVALID_LIVE_RUNTIME_CONFIG`.

Wheel regression tests:

1. build with the active test interpreter and its declared `setuptools>=70` dev dependency;
2. install the wheel into an isolated directory;
3. run outside the checkout with an adversarial parent-level `config/` directory;
4. construct the documented factory skeleton and load the 33-stage and Unit Contract registries;
5. delete one installed registry member and verify that lookup fails rather than falling back to the adversarial directory.

## 5. No fallback

Without a provider factory, the command returns:

```text
LIVE_PROVIDER_FACTORY_REQUIRED
```

It does not use `examples/oci/company.yaml` automatically.

OCI remains available only through the explicit regression command:

```bash
valuation-engine "분석시작 OCI홀딩스" \
  --legacy-oci \
  --config examples/oci/company.yaml
```

The following combinations are invalid:

- `--legacy-oci` plus `--provider-factory`;
- live analysis plus `--config`;
- YAML fixture mode plus LIVE/legacy command options.

## 6. Factory-config validation boundary

After the factory returns, the following operations are executed inside one secret-safe validation boundary:

- nested-field access;
- run, state-root, company and jurisdiction identity checks;
- package/custom registry resolution;
- resolved-jurisdiction provider wrapping;
- `LivePrimaryRuntimeConfig.validate()`.

Existing specific `LiveCLIError` classifications are re-raised unchanged. Any other ordinary exception becomes `INVALID_LIVE_RUNTIME_CONFIG` with only its exception type exposed. Process-control exceptions still propagate.

## 7. Result validation

The runner must return `ControlledRunResult` with:

- the same `run_id`;
- `ExecutionMode.LIVE_PRIMARY`;
- typed stage traces;
- no blocking reasons for a completed run;
- a non-empty `final_report` for a completed run.

A result from PRIMARY_SHADOW or LEGACY_REGRESSION is rejected even if its numerical output appears valid.

A blocked result must not retain a Freeze Token or intrinsic-owned outputs such as valuation objects, scenario values, Street/market comparison or final report. Any such result is rejected as `BLOCKED_LIVE_RESULT_LEAKAGE` rather than rendered.

## 8. Secret-safe blocked-run rendering

Routine progress renders one compact block for each of the five canonical major gates:

```text
Gate n/5 — title
Status / decisive result / residual risk / next action
```

All 33 stage identities/statuses are reserved for the compact verified appendix; exact rationales/output keys remain in the immutable trace artifact. If a gate terminates blocked, its compact summary uses only the terminal stage/status code and never the raw blocking rationale, because provider exceptions may be embedded in that string.

When a stage blocks, CLI output contains only:

- completed major-gate summaries plus the blocked gate's stable stage/status summary;
- `VALUATION BLOCKED`;
- stable uppercase `STAGE:STATUS` codes.

The renderer does not print:

- provider exception messages;
- raw blocking-reason strings;
- scenario intrinsic values;
- expected value;
- valuation hash or Freeze Token;
- Street or market comparison;
- a stale or injected final report.

`run_prism()` also redacts intrinsic-owned keys from blocked results. CLI result validation and rendering are additional protections rather than the sole guard.

## 9. Example factory skeleton

```python
from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import LivePrimaryRuntimeConfig


def build_config(request: LiveAnalysisRequest) -> LivePrimaryRuntimeConfig:
    providers = build_private_provider_bundle(request)
    return LivePrimaryRuntimeConfig(
        run_id=request.run_id,
        state_root=request.state_root,
        company_request=CompanyResolutionRequest(
            request.company_query,
            request.jurisdiction,
        ),
        scenario_binding_spec=build_scenario_binding_spec(request),
        providers=providers,
        method_choices=build_method_choices(request),
        market_currency=resolve_market_currency(request),
    )
```

`build_private_provider_bundle` must still obey every normal PRISM source-layer, Evidence, market-isolation, exact-evaluator and Audit contract. The CLI factory boundary does not authorize assumptions or valuation math.

## 10. Operational errors

Errors are classified before returning exit code `2`:

- `COMPANY_REQUIRED`
- `INVALID_ANALYSIS_COMMAND`
- `INVALID_LIVE_ANALYSIS_REQUEST`
- `LIVE_PROVIDER_FACTORY_REQUIRED`
- `INVALID_PROVIDER_FACTORY`
- `PROVIDER_FACTORY_LOAD_FAILED`
- `PROVIDER_FACTORY_NOT_CALLABLE`
- `PROVIDER_FACTORY_FAILED`
- `INVALID_LIVE_RUNTIME_CONFIG`
- `LIVE_RUNTIME_REGISTRY_UNAVAILABLE`
- `LIVE_RUNTIME_IDENTITY_MISMATCH`
- `LIVE_RUNTIME_STATE_ROOT_MISMATCH`
- `LIVE_RUNTIME_COMPANY_MISMATCH`
- `LIVE_RUNTIME_JURISDICTION_MISMATCH`
- `LIVE_PRIMARY_EXECUTION_FAILED`
- `INVALID_LIVE_RUNTIME_RESULT`
- `LIVE_RUNTIME_RESULT_ID_MISMATCH`
- `LIVE_RUNTIME_MODE_MISMATCH`
- `BLOCKED_LIVE_RESULT_LEAKAGE`
- `LIVE_REPORT_MISSING`

These errors are operational/capability failures. They must not be converted into an intrinsic estimate. Sensitive provider exception messages are retained only in a separately protected operator diagnostic channel if one is configured outside this public terminal contract.
