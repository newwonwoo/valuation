from pathlib import Path
from types import SimpleNamespace

import pytest

import valuation_engine
import valuation_engine.cli as cli
from valuation_engine.cli_runtime import (
    LiveAnalysisRequest,
    LiveCLIError,
    build_live_runtime_config,
    execute_live_analysis,
    load_live_runtime_config_factory,
    parse_analysis_command,
    render_controlled_run,
)
from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
)
from valuation_engine.orchestrator import ControlledRunResult, StageTrace
from valuation_engine.scenario_binding import ScenarioBindingSpec


def _minimal_config(request: LiveAnalysisRequest) -> LivePrimaryRuntimeConfig:
    noop = lambda *args, **kwargs: None
    collector = LiveCollectorProvider(
        CollectorCapability(
            collector_id="fixture",
            source_id="FIXTURE_PRIMARY",
            supported_metrics=("x",),
            jurisdictions=(request.jurisdiction or "GLOBAL",),
            implementation_ref="tests.test_live_cli.fixture",
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


def _completed_result(run_id: str = "RUN-1") -> ControlledRunResult:
    return ControlledRunResult(
        run_id=run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "FINAL_REPORT",
                StageStatus.PASS,
                "final report rendered",
                False,
                ("final_report",),
            ),
        ),
        data={"final_report": "# Live report\n"},
        blocked_reasons=(),
        freeze_token=None,
    )


def _blocked_result(
    run_id: str,
    *,
    data=None,
) -> ControlledRunResult:
    return ControlledRunResult(
        run_id=run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "WACC_VALIDATION",
                StageStatus.NOT_IMPLEMENTED,
                "provider missing",
                True,
            ),
        ),
        data={} if data is None else data,
        blocked_reasons=("WACC_VALIDATION: provider missing",),
        freeze_token=None,
    )


def test_public_package_exports_live_primary_entrypoint():
    assert callable(valuation_engine.run_prism)
    assert valuation_engine.LivePrimaryRuntimeConfig is LivePrimaryRuntimeConfig


def test_parse_analysis_command_requires_exact_prefix_and_company():
    assert parse_analysis_command("분석시작 삼성전자") == "삼성전자"
    with pytest.raises(LiveCLIError, match="기업명"):
        parse_analysis_command("분석시작")
    with pytest.raises(LiveCLIError, match="형식"):
        parse_analysis_command("분석시작하기 삼성전자")


def test_provider_factory_loader_uses_module_colon_callable(tmp_path, monkeypatch):
    module = tmp_path / "fixture_provider.py"
    module.write_text(
        "def build(request):\n    return request\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    factory = load_live_runtime_config_factory("fixture_provider:build")
    marker = object()
    assert factory(marker) is marker

    with pytest.raises(LiveCLIError, match="module:callable"):
        load_live_runtime_config_factory("fixture_provider.build")
    with pytest.raises(LiveCLIError, match="호출 가능"):
        load_live_runtime_config_factory("fixture_provider:__name__")


def test_factory_cannot_change_run_identity_or_state_root(tmp_path):
    request = LiveAnalysisRequest(
        command="분석시작 Target",
        company_query="Target",
        state_root=tmp_path / "state",
        run_id="RUN-1",
        jurisdiction="KR",
    )
    valid = build_live_runtime_config(request, _minimal_config)
    assert valid.run_id == "RUN-1"

    def wrong_run_id(current):
        config = _minimal_config(current)
        return LivePrimaryRuntimeConfig(
            **{**config.__dict__, "run_id": "OTHER"}
        )

    with pytest.raises(LiveCLIError, match="run_id"):
        build_live_runtime_config(request, wrong_run_id)

    def wrong_state_root(current):
        config = _minimal_config(current)
        return LivePrimaryRuntimeConfig(
            **{**config.__dict__, "state_root": tmp_path / "other"}
        )

    with pytest.raises(LiveCLIError, match="state_root"):
        build_live_runtime_config(request, wrong_state_root)


def test_factory_jurisdiction_alias_is_normalized(tmp_path):
    request = LiveAnalysisRequest(
        command="분석시작 Target",
        company_query="Target",
        state_root=tmp_path / "state",
        run_id="RUN-ALIAS",
        jurisdiction="KR",
    )

    def alias_factory(current):
        config = _minimal_config(current)
        return LivePrimaryRuntimeConfig(
            **{
                **config.__dict__,
                "company_request": CompanyResolutionRequest(
                    current.company_query,
                    "KOR",
                ),
            }
        )

    config = build_live_runtime_config(request, alias_factory)
    assert config.company_request.jurisdiction == "KOR"


def test_execute_live_analysis_requires_live_mode_and_matching_result_id(tmp_path):
    def runner(config):
        return _completed_result(config.run_id)

    result = execute_live_analysis(
        "분석시작 Target",
        state_root=tmp_path,
        provider_factory=_minimal_config,
        run_id="RUN-1",
        runner=runner,
    )
    assert result.completed

    def wrong_mode(config):
        value = _completed_result(config.run_id)
        return ControlledRunResult(
            run_id=value.run_id,
            execution_mode=ExecutionMode.PRIMARY_SHADOW,
            stage_traces=value.stage_traces,
            data=value.data,
            blocked_reasons=value.blocked_reasons,
            freeze_token=None,
        )

    with pytest.raises(LiveCLIError, match="LIVE_PRIMARY"):
        execute_live_analysis(
            "분석시작 Target",
            state_root=tmp_path,
            provider_factory=_minimal_config,
            run_id="RUN-2",
            runner=wrong_mode,
        )

    def wrong_run_id(config):
        return _completed_result("OTHER-RUN")

    with pytest.raises(LiveCLIError, match="run_id"):
        execute_live_analysis(
            "분석시작 Target",
            state_root=tmp_path,
            provider_factory=_minimal_config,
            run_id="RUN-3",
            runner=wrong_run_id,
        )


def test_execute_rejects_nonblocked_result_without_stage_trace(tmp_path):
    def runner(config):
        return ControlledRunResult(
            run_id=config.run_id,
            execution_mode=ExecutionMode.LIVE_PRIMARY,
            stage_traces=(),
            data={"final_report": "# impossible"},
            blocked_reasons=(),
            freeze_token=None,
        )

    with pytest.raises(LiveCLIError, match="stage trace"):
        execute_live_analysis(
            "분석시작 Target",
            state_root=tmp_path,
            provider_factory=_minimal_config,
            run_id="RUN-EMPTY",
            runner=runner,
        )


def test_execute_rejects_blocked_result_with_intrinsic_leak(tmp_path):
    def runner(config):
        return _blocked_result(
            config.run_id,
            data={"expected_value_per_share": 999999},
        )

    with pytest.raises(LiveCLIError, match="intrinsic-owned"):
        execute_live_analysis(
            "분석시작 Target",
            state_root=tmp_path,
            provider_factory=_minimal_config,
            run_id="RUN-LEAK",
            runner=runner,
        )


def test_execute_accepts_clean_blocked_result(tmp_path):
    result = execute_live_analysis(
        "분석시작 Target",
        state_root=tmp_path,
        provider_factory=_minimal_config,
        run_id="RUN-BLOCKED",
        runner=lambda config: _blocked_result(config.run_id),
    )
    assert result.blocked_reasons


def test_blocked_render_never_emits_intrinsic_values():
    result = _blocked_result(
        "BLOCKED",
        data={
            "expected_value_per_share": 999999,
            "final_report": "must not render",
        },
    )
    rendered = render_controlled_run(result)
    assert "VALUATION BLOCKED" in rendered
    assert "999999" not in rendered
    assert "must not render" not in rendered


def test_cli_analysis_requires_provider_factory_and_never_falls_back_to_oci(capsys):
    status = cli.main(["분석시작 삼성전자"], environ={})
    captured = capsys.readouterr()
    assert status == 2
    assert "LIVE_PROVIDER_FACTORY_REQUIRED" in captured.err
    assert "OCI" in captured.err


def test_cli_routes_live_analysis_through_explicit_factory(
    monkeypatch,
    capsys,
    tmp_path,
):
    seen = {}

    def fake_loader(spec):
        seen["spec"] = spec
        return _minimal_config

    def fake_execute(command, **kwargs):
        seen["command"] = command
        seen["state_root"] = kwargs["state_root"]
        seen["factory"] = kwargs["provider_factory"]
        seen["run_id"] = kwargs["run_id"]
        seen["jurisdiction"] = kwargs["jurisdiction"]
        return _completed_result("RUN-CLI")

    monkeypatch.setattr(cli, "load_live_runtime_config_factory", fake_loader)
    monkeypatch.setattr(cli, "execute_live_analysis", fake_execute)

    status = cli.main(
        [
            "분석시작 삼성전자",
            "--provider-factory",
            "provider.module:build",
            "--state-root",
            str(tmp_path),
            "--run-id",
            "RUN-CLI",
            "--jurisdiction",
            "KR",
        ],
        environ={},
    )
    captured = capsys.readouterr()
    assert status == 0
    assert seen == {
        "spec": "provider.module:build",
        "command": "분석시작 삼성전자",
        "state_root": str(tmp_path),
        "factory": _minimal_config,
        "run_id": "RUN-CLI",
        "jurisdiction": "KR",
    }
    assert "FINAL_REPORT" in captured.out
    assert "# Live report" in captured.out


def test_cli_legacy_workflow_requires_explicit_flag(monkeypatch, capsys):
    calls = []

    def fake_legacy(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            progress=("legacy step",),
            report="legacy report",
            blocked_reasons=(),
        )

    monkeypatch.setattr(cli, "run_analysis_command", fake_legacy)
    status = cli.main(
        [
            "분석시작 OCI홀딩스",
            "--legacy-oci",
            "--config",
            "examples/oci/company.yaml",
            "--run-id",
            "LEGACY-1",
        ],
        environ={},
    )
    captured = capsys.readouterr()
    assert status == 0
    assert calls[0][0] == "분석시작 OCI홀딩스"
    assert calls[0][1]["run_id"] == "LEGACY-1"
    assert "legacy report" in captured.out


def test_cli_rejects_legacy_fixture_config_in_live_mode(capsys):
    status = cli.main(
        [
            "분석시작 Target",
            "--config",
            "examples/oci/company.yaml",
            "--provider-factory",
            "provider.module:build",
        ],
        environ={},
    )
    captured = capsys.readouterr()
    assert status == 2
    assert "LEGACY_CONFIG_REQUIRES_FLAG" in captured.err
