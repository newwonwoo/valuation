from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import valuation_engine.mcp_server as mcp_server
from valuation_engine.cli_runtime import LiveCLIError
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import ControlledRunResult, StageTrace


def _completed_result(run_id: str = "MCP-RUN") -> ControlledRunResult:
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
        data={"final_report": "# PRISM canonical report\n"},
        blocked_reasons=(),
        freeze_token=None,
    )


def _blocked_result(run_id: str = "MCP-BLOCKED") -> ControlledRunResult:
    secret = "https://source.invalid?token=TOP-SECRET"
    return ControlledRunResult(
        run_id=run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=(
            StageTrace(
                "WACC_VALIDATION",
                StageStatus.NOT_IMPLEMENTED,
                secret,
                True,
            ),
        ),
        data={},
        blocked_reasons=(f"WACC_VALIDATION: {secret}",),
        freeze_token=None,
    )


def test_mcp_exposes_one_canonical_tool():
    tools = asyncio.run(mcp_server.mcp.list_tools())

    assert [tool.name for tool in tools] == ["prism_analyze"]
    tool = tools[0]
    assert tool.title == "PRISM_ANALYZE"
    assert tool.input_schema["required"] == ["company"]
    assert "strict" in (tool.description or "").lower()
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True


def test_prism_analyze_dispatches_only_to_strict_entrypoint(monkeypatch, tmp_path):
    seen = {}
    factory = object()

    monkeypatch.setenv("VALUATION_MCP_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("VALUATION_MCP_JURISDICTION", "KR")
    monkeypatch.setattr(
        mcp_server,
        "load_live_runtime_config_factory",
        lambda spec: seen.setdefault("factory_spec", spec) or factory,
    )

    def fake_execute(command, **kwargs):
        seen["command"] = command
        seen["state_root"] = kwargs["state_root"]
        seen["factory"] = kwargs["provider_factory"]
        seen["jurisdiction"] = kwargs["jurisdiction"]
        return _completed_result()

    monkeypatch.setattr(mcp_server, "execute_live_analysis", fake_execute)

    result = mcp_server.run_prism_mcp("  고려아연  ")

    assert result["status"] == "COMPLETED"
    assert result["company"] == "고려아연"
    assert result["canonical_command"] == "분석시작 고려아연"
    assert result["execution_mode"] == ExecutionMode.LIVE_PRIMARY.value
    assert result["blocking_codes"] == []
    assert "PRISM canonical report" in result["report"]
    assert seen["command"] == "분석시작 고려아연"
    assert seen["state_root"] == tmp_path / "state"
    assert seen["factory"] is factory
    assert seen["jurisdiction"] == "KR"
    assert seen["factory_spec"] == "valuation_engine.generic_kr_cli:factory"


def test_mcp_call_returns_structured_prism_result(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "load_live_runtime_config_factory",
        lambda spec: object(),
    )
    monkeypatch.setattr(
        mcp_server,
        "execute_live_analysis",
        lambda command, **kwargs: _completed_result("MCP-STRUCTURED"),
    )

    result = asyncio.run(
        mcp_server.mcp.call_tool("prism_analyze", {"company": "010130"})
    )

    assert result.is_error is not True
    assert result.structured_content is not None
    assert result.structured_content["status"] == "COMPLETED"
    assert result.structured_content["company"] == "010130"
    assert result.structured_content["canonical_command"] == "분석시작 010130"
    assert result.structured_content["run_id"] == "MCP-STRUCTURED"


def test_blocked_mcp_result_exposes_only_sanitized_codes(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "load_live_runtime_config_factory",
        lambda spec: object(),
    )
    monkeypatch.setattr(
        mcp_server,
        "execute_live_analysis",
        lambda command, **kwargs: _blocked_result(),
    )

    result = mcp_server.run_prism_mcp("고려아연")

    assert result["status"] == "VALUATION_BLOCKED"
    assert result["blocking_codes"] == ["WACC_VALIDATION:NOT_IMPLEMENTED"]
    assert "VALUATION BLOCKED" in result["report"]
    assert "TOP-SECRET" not in result["report"]
    assert "source.invalid" not in result["report"]


def test_mcp_never_falls_back_when_strict_runtime_rejects(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "load_live_runtime_config_factory",
        lambda spec: object(),
    )

    def reject(*args, **kwargs):
        raise LiveCLIError(
            "LIVE_EXECUTION_ATTESTATION_REQUIRED",
            "완료된 LIVE_PRIMARY 결과에 정식 실행 인증이 없습니다",
        )

    monkeypatch.setattr(mcp_server, "execute_live_analysis", reject)

    with pytest.raises(ToolError, match="LIVE_EXECUTION_ATTESTATION_REQUIRED"):
        mcp_server.run_prism_mcp("고려아연")


def test_mcp_provider_factory_override_precedence(monkeypatch):
    monkeypatch.setenv("VALUATION_LIVE_PROVIDER_FACTORY", "live.module:build")
    assert mcp_server._provider_factory_spec() == "live.module:build"

    monkeypatch.setenv("VALUATION_MCP_PROVIDER_FACTORY", "mcp.module:build")
    assert mcp_server._provider_factory_spec() == "mcp.module:build"


def test_blank_company_fails_before_any_runtime(monkeypatch):
    called = False

    def should_not_load(_):
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(mcp_server, "load_live_runtime_config_factory", should_not_load)

    with pytest.raises(ToolError, match="company is required"):
        mcp_server.run_prism_mcp("   ")
    assert called is False
