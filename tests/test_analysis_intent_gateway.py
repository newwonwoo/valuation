from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuation_engine.cli as cli
import valuation_engine.strict_cli_runtime as strict_cli
from valuation_engine.analysis_intent import (
    canonicalize_analysis_command,
    is_analysis_intent,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import ControlledRunResult, StageTrace


@pytest.mark.parametrize(
    ("utterance", "company"),
    (
        ("분석시작 고려아연", "고려아연"),
        ("분석 시작 고려아연", "고려아연"),
        ("고려아연 분석시작", "고려아연"),
        ("고려아연 분석 시작해", "고려아연"),
        ("고려아연분석시작", "고려아연"),
        ("고려아연 분석해줘", "고려아연"),
        ("고려아연분석해줘", "고려아연"),
        ("고려아연 분석해 줘", "고려아연"),
        ("고려아연 분석", "고려아연"),
        ("010130분석", "010130"),
        ("고려아연 밸류에이션", "고려아연"),
        ("고려아연밸류에이션", "고려아연"),
        ("고려아연 가치평가", "고려아연"),
        ("고려아연가치평가", "고려아연"),
        ("고려아연 적정주가", "고려아연"),
        ("고려아연 프리즘", "고려아연"),
        ("고려아연프리즘으로시작해", "고려아연"),
        ("프리즘 고려아연", "고려아연"),
        ("SK하이닉스 한번 돌려봐", "SK하이닉스"),
        ("SK하이닉스돌려봐", "SK하이닉스"),
        ("010130 분석", "010130"),
        ("SK하이닉스를 분석해줘", "SK하이닉스"),
        ("고려아연 분석해줘?", "고려아연"),
    ),
)
def test_stock_analysis_phrasings_share_one_canonical_command(utterance, company):
    assert canonicalize_analysis_command(utterance) == f"분석시작 {company}"
    assert is_analysis_intent(utterance)


@pytest.mark.parametrize(
    "value",
    (
        "examples/oci/company.yaml",
        "config/company.yaml",
        "삼성전자 실적 알려줘",
        "분석시작하기 삼성전자",
        "README.md",
        "",
    ),
)
def test_non_analysis_inputs_do_not_enter_prism_gateway(value):
    assert canonicalize_analysis_command(value) is None
    assert not is_analysis_intent(value)


def test_intent_without_company_is_recognized_but_left_for_fail_closed_parser():
    assert canonicalize_analysis_command("분석해줘") == "분석시작"
    assert canonicalize_analysis_command("분석 시작해") == "분석시작"
    assert canonicalize_analysis_command("프리즘") == "분석시작"


def _completed_result(run_id: str) -> ControlledRunResult:
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
        data={"final_report": "# canonical"},
        blocked_reasons=(),
        freeze_token=None,
    )


def test_public_strict_entrypoint_canonicalizes_before_runtime_request(
    monkeypatch,
    tmp_path,
):
    seen = {}

    def fake_build(request, factory):
        seen["request"] = request
        seen["factory"] = factory
        return SimpleNamespace(run_id=request.run_id)

    monkeypatch.setattr(strict_cli, "build_live_runtime_config", fake_build)
    factory = lambda request: request

    result = strict_cli.execute_live_analysis(
        "고려아연분석해줘",
        state_root=tmp_path,
        provider_factory=factory,
        run_id="RUN-INTENT",
        runner=lambda config: _completed_result(config.run_id),
    )

    assert result.completed
    assert seen["request"].command == "분석시작 고려아연"
    assert seen["request"].company_query == "고려아연"
    assert seen["factory"] is factory


def test_cli_natural_analysis_intent_enters_live_path_not_yaml(capsys):
    status = cli.main(["고려아연분석해줘"], environ={})
    captured = capsys.readouterr()

    assert status == 2
    assert "LIVE_PROVIDER_FACTORY_REQUIRED" in captured.err
    assert "No such file" not in captured.err


def test_cli_analysis_intent_without_company_fails_before_provider_resolution(capsys):
    status = cli.main(["분석해줘"], environ={})
    captured = capsys.readouterr()

    assert status == 2
    assert "COMPANY_REQUIRED" in captured.err
    assert "LIVE_PROVIDER_FACTORY_REQUIRED" not in captured.err
