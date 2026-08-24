import pytest

import valuation_engine.cli as cli
from valuation_engine.cli_runtime import LiveAnalysisRequest, LiveCLIError


def test_blank_jurisdiction_is_a_controlled_request_error(tmp_path):
    request = LiveAnalysisRequest(
        command="분석시작 Target",
        company_query="Target",
        state_root=tmp_path,
        run_id="BLANK-JURISDICTION",
        jurisdiction="   ",
    )
    with pytest.raises(LiveCLIError) as caught:
        request.validate()
    assert caught.value.code == "INVALID_LIVE_ANALYSIS_REQUEST"


def test_cli_blank_jurisdiction_returns_exit_code_two_without_traceback(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        cli,
        "load_live_runtime_config_factory",
        lambda _: lambda request: None,
    )
    status = cli.main(
        [
            "분석시작 Target",
            "--provider-factory",
            "fixture.provider:build",
            "--jurisdiction",
            "   ",
        ],
        environ={},
    )
    captured = capsys.readouterr()
    assert status == 2
    assert "INVALID_LIVE_ANALYSIS_REQUEST" in captured.err
    assert "Traceback" not in captured.err
