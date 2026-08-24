from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from valuation_engine import cli
from valuation_engine.cli import (
    LiveRuntimeConfigurationError,
    _analysis_mode,
    _build_parser,
    load_runtime_factory,
    render_controlled_run,
    run_live_analysis_command,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import LivePrimaryRuntimeConfig
from valuation_engine.orchestrator import ControlledRunResult, StageTrace
from valuation_engine.scenario_binding import ScenarioBindingSpec


def runtime_config(
    *,
    company: str = "Example Co",
    state_root: Path,
    run_id: str = "LIVE-1",
) -> LivePrimaryRuntimeConfig:
    return LivePrimaryRuntimeConfig(
        run_id=run_id,
        state_root=state_root,
        company_request=CompanyResolutionRequest(company),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("required",)),
        providers=SimpleNamespace(),  # runner is stubbed in these CLI contract tests
    )


def completed_result() -> ControlledRunResult:
    return ControlledRunResult(
        run_id="LIVE-1",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "FINAL_REPORT",
                StageStatus.PASS,
                "final report rendered",
                False,
            ),
        ),
        data={"final_report": "# Example Co\n\nLIVE report"},
        blocked_reasons=(),
        freeze_token=None,
    )


def test_analysis_command_defaults_to_live_primary_without_marking_yaml_live():
    args = _build_parser().parse_args(["분석시작 Example Co"])
    assert args.mode is None
    assert _analysis_mode(args.mode) == "live-primary"


def test_direct_yaml_rejects_explicit_live_primary_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["valuation-engine", "company.yaml", "--mode", "live-primary"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_live_analysis_never_falls_back_without_runtime_factory(monkeypatch, tmp_path):
    monkeypatch.delenv("PRISM_RUNTIME_FACTORY", raising=False)
    with pytest.raises(LiveRuntimeConfigurationError, match="no production runtime factory"):
        run_live_analysis_command(
            "분석시작 Example Co",
            state_root=tmp_path,
            runtime_factory_spec=None,
        )


def test_runtime_factory_loader_requires_module_function_contract():
    with pytest.raises(LiveRuntimeConfigurationError, match="module:function"):
        load_runtime_factory("valuation_engine.cli")


def test_live_analysis_calls_injected_runtime_factory_and_runner(monkeypatch, tmp_path):
    module = ModuleType("tests_runtime_factory_fixture")
    seen = {}

    def factory(request):
        seen["request"] = request
        return runtime_config(
            company=request.company_query,
            state_root=request.state_root,
            run_id=request.run_id or "LIVE-1",
        )

    module.build = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)

    runner_seen = {}

    def runner(config):
        runner_seen["config"] = config
        return completed_result()

    result = run_live_analysis_command(
        "분석시작 Example Co",
        state_root=tmp_path,
        runtime_factory_spec=f"{module.__name__}:build",
        run_id="LIVE-1",
        runner=runner,
    )

    assert result.completed
    assert seen["request"].company_query == "Example Co"
    assert seen["request"].state_root == tmp_path
    assert seen["request"].run_id == "LIVE-1"
    assert runner_seen["config"].company_request.query == "Example Co"
    assert runner_seen["config"].state_root == tmp_path


def test_live_analysis_rejects_factory_company_or_state_root_drift(monkeypatch, tmp_path):
    module = ModuleType("tests_runtime_factory_drift_fixture")

    def wrong_company(request):
        return runtime_config(
            company="Different Co",
            state_root=request.state_root,
            run_id="LIVE-1",
        )

    module.build = wrong_company
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(LiveRuntimeConfigurationError, match="company query"):
        run_live_analysis_command(
            "분석시작 Example Co",
            state_root=tmp_path,
            runtime_factory_spec=f"{module.__name__}:build",
            run_id="LIVE-1",
            runner=lambda config: completed_result(),
        )

    def wrong_state(request):
        return runtime_config(
            company=request.company_query,
            state_root=tmp_path / "other",
            run_id="LIVE-1",
        )

    module.build = wrong_state
    with pytest.raises(LiveRuntimeConfigurationError, match="preserve the CLI state_root"):
        run_live_analysis_command(
            "분석시작 Example Co",
            state_root=tmp_path,
            runtime_factory_spec=f"{module.__name__}:build",
            run_id="LIVE-1",
            runner=lambda config: completed_result(),
        )


def test_render_controlled_run_suppresses_report_for_blocked_result():
    result = ControlledRunResult(
        run_id="BLOCKED",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "PRIMARY_EVIDENCE_COLLECTION",
                StageStatus.NOT_IMPLEMENTED,
                "collector provider missing",
                True,
            ),
        ),
        data={"final_report": "MUST NOT BE SHOWN"},
        blocked_reasons=(
            "PRIMARY_EVIDENCE_COLLECTION: collector provider missing",
        ),
        freeze_token=None,
    )
    rendered = render_controlled_run(result)
    assert "# VALUATION BLOCKED" in rendered
    assert "MUST NOT BE SHOWN" not in rendered
    assert "collector provider missing" in rendered
