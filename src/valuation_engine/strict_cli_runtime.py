from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from .authority_orchestrator import AuthorityControlledResult
from .cli_runtime import (
    LiveAnalysisRequest,
    LiveCLIError,
    LiveRuntimeConfigFactory,
    build_live_runtime_config,
    generate_run_id,
)
from .control_plane import ExecutionMode
from .orchestrator import ControlledRunResult, MajorGateReporter
from .strict_live_runtime import require_canonical_live_result, run_prism


_BLOCKED_FORBIDDEN_DATA_KEYS = frozenset(
    {
        "generic_valuation_result",
        "intrinsic_scenario_values",
        "expected_value_per_share",
        "valuation_hash",
        "intrinsic_freeze_token",
        "street_comparison",
        "market_comparison",
        "final_report",
        "execution_attestation",
    }
)
StrictLiveRuntimeRunner = Callable[
    [object], AuthorityControlledResult | ControlledRunResult
]


def execute_live_analysis(
    command: str,
    *,
    state_root: str | Path,
    provider_factory: LiveRuntimeConfigFactory,
    run_id: str | None = None,
    jurisdiction: str | None = None,
    runner: StrictLiveRuntimeRunner = run_prism,
    major_gate_reporter: MajorGateReporter | None = None,
) -> ControlledRunResult:
    """Execute `분석시작` through the canonical attested LIVE_PRIMARY path.

    A custom runner remains accepted for focused tests. The default production
    runner must return an AuthorityControlledResult and successful output must
    pass `require_canonical_live_result` before it reaches the CLI/report layer.
    """
    from .cli_runtime import parse_analysis_command

    company_query = parse_analysis_command(command)
    request = LiveAnalysisRequest(
        command=command.strip(),
        company_query=company_query,
        state_root=Path(state_root),
        run_id=run_id or generate_run_id(),
        jurisdiction=jurisdiction,
    )
    config = build_live_runtime_config(request, provider_factory)
    if major_gate_reporter is not None:
        config = replace(config, major_gate_reporter=major_gate_reporter)
    try:
        raw_result = runner(config)
    except Exception as exc:
        raise LiveCLIError(
            "LIVE_PRIMARY_EXECUTION_FAILED",
            "LIVE_PRIMARY 실행에 실패했습니다 "
            f"({type(exc).__name__}); 상세 예외는 터미널에 출력하지 않습니다",
        ) from exc

    if isinstance(raw_result, AuthorityControlledResult):
        if raw_result.result.blocked_reasons:
            result = raw_result.result
        else:
            try:
                result = require_canonical_live_result(raw_result)
            except Exception as exc:
                raise LiveCLIError(
                    "LIVE_EXECUTION_ATTESTATION_REQUIRED",
                    "완료된 LIVE_PRIMARY 결과에 정식 실행 인증이 없습니다",
                ) from exc
    elif isinstance(raw_result, ControlledRunResult) and runner is not run_prism:
        # Focused unit tests may inject a deterministic fake runner. Production
        # uses the default strict runner and can never take this compatibility path.
        result = raw_result
    else:
        raise LiveCLIError(
            "INVALID_LIVE_RUNTIME_RESULT",
            "기본 LIVE_PRIMARY runner는 실행 인증이 포함된 AuthorityControlledResult를 반환해야 합니다",
        )

    if result.run_id != request.run_id:
        raise LiveCLIError(
            "LIVE_RUNTIME_RESULT_ID_MISMATCH",
            "LIVE_PRIMARY 결과 run_id가 CLI 요청과 다릅니다",
        )
    if result.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        raise LiveCLIError(
            "LIVE_RUNTIME_MODE_MISMATCH",
            "분석시작 명령이 LIVE_PRIMARY 이외의 실행모드로 완료됐습니다",
        )
    if not result.blocked_reasons and not result.completed:
        raise LiveCLIError(
            "INVALID_LIVE_RUNTIME_RESULT",
            "차단 사유가 없는 LIVE_PRIMARY 결과에는 stage trace가 필요합니다",
        )
    if result.blocked_reasons:
        leaked = tuple(
            sorted(_BLOCKED_FORBIDDEN_DATA_KEYS.intersection(result.data))
        )
        if result.freeze_token is not None or leaked:
            detail = []
            if result.freeze_token is not None:
                detail.append("freeze_token")
            detail.extend(leaked)
            raise LiveCLIError(
                "BLOCKED_LIVE_RESULT_LEAKAGE",
                "차단된 LIVE_PRIMARY 결과에 intrinsic-owned 출력이 남아 있습니다: "
                + ", ".join(detail),
            )
    return result
