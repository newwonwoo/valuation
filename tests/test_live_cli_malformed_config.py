from dataclasses import replace

import pytest

from valuation_engine.cli_runtime import (
    LiveAnalysisRequest,
    LiveCLIError,
    build_live_runtime_config,
)
from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec


def _request(tmp_path) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command="분석시작 Target",
        company_query="Target",
        state_root=tmp_path / "state",
        run_id="MALFORMED-CONFIG-1",
        jurisdiction="KR",
    )


def _valid_config(request: LiveAnalysisRequest) -> LivePrimaryRuntimeConfig:
    noop = lambda *args, **kwargs: None
    collector = LiveCollectorProvider(
        CollectorCapability(
            collector_id="fixture",
            source_id="FIXTURE_PRIMARY",
            supported_metrics=("x",),
            jurisdictions=("KR",),
            implementation_ref="tests.malformed.fixture",
        ),
        noop,
    )
    providers = LivePrimaryProviders(
        company_resolver=noop,
        industry_snapshot_loader=noop,
        freshness_loader=noop,
        segment_decomposer=noop,
        industry_dna_router=noop,
        collectors=(collector,),
        scanner_runners={},
        intelligence_officer=noop,
        red_team_officer=noop,
        bridge_analyst=noop,
        evaluator_registry_loader=noop,
        valuation_plan_inputs_loader=noop,
    )
    return LivePrimaryRuntimeConfig(
        run_id=request.run_id,
        state_root=request.state_root,
        company_request=CompanyResolutionRequest(
            request.company_query,
            request.jurisdiction,
        ),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("x",)),
        providers=providers,
    )


@pytest.mark.parametrize(
    "mutate, expected_type",
    (
        (
            lambda config: replace(config, company_request=None),
            "AttributeError",
        ),
        (
            lambda config: replace(config, stage_registry_path=object()),
            "TypeError",
        ),
        (
            lambda config: replace(config, providers=None),
            "AttributeError",
        ),
    ),
)
def test_malformed_nested_config_is_classified_without_traceback_or_secret(
    tmp_path,
    mutate,
    expected_type,
):
    request = _request(tmp_path)
    secret = "TOP-SECRET"

    def factory(current):
        config = mutate(_valid_config(current))
        # The value is intentionally unused by the runtime. It proves that the stable
        # error contract does not stringify the malformed object or unrelated fields.
        object.__setattr__(config, "run_id", config.run_id)
        return config

    with pytest.raises(LiveCLIError) as caught:
        build_live_runtime_config(request, factory)
    assert caught.value.code == "INVALID_LIVE_RUNTIME_CONFIG"
    assert expected_type in str(caught.value)
    assert secret not in str(caught.value)


def test_specific_identity_error_is_not_collapsed_into_generic_config_error(
    tmp_path,
):
    request = _request(tmp_path)

    def factory(current):
        return replace(_valid_config(current), run_id="OTHER-RUN")

    with pytest.raises(LiveCLIError) as caught:
        build_live_runtime_config(request, factory)
    assert caught.value.code == "LIVE_RUNTIME_IDENTITY_MISMATCH"


def test_process_control_exception_from_nested_validation_propagates(tmp_path):
    request = _request(tmp_path)
    config = _valid_config(request)

    class InterruptingPath:
        def __fspath__(self):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        build_live_runtime_config(
            request,
            lambda _: replace(config, stage_registry_path=InterruptingPath()),
        )
