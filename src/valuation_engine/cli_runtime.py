from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib import import_module
import os
from pathlib import Path
from typing import Callable, Mapping

from .collection_plan import normalize_jurisdiction
from .control_plane import ExecutionMode, StageStatus
from .live_primary_adapters import ResolvedCompanyIdentity
from .live_runtime import (
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
    run_prism,
)
from .orchestrator import ControlledRunResult, MajorGateReporter, MajorGateSummary
from .report_localization import (
    localize_stage_references,
    next_action_label_ko,
    status_label_ko,
)
from .runtime_resources import runtime_registry_path


_PROVIDER_FACTORY_ENV = "VALUATION_LIVE_PROVIDER_FACTORY"
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
    }
)
_RUNTIME_REGISTRY_FIELDS = {
    "stage_registry_path": "control_plane_stage_registry.yaml",
    "archetype_registry_path": "archetype_module_registry.yaml",
    "archetype_control_requirements_path": (
        "archetype_control_requirements.yaml"
    ),
    "industry_source_registry_path": "industry_source_registry.yaml",
    "unit_contract_registry_path": "unit_contract_registry.yaml",
}
_BLOCKING_STATUSES = frozenset(
    {
        StageStatus.BLOCKED,
        StageStatus.NOT_IMPLEMENTED,
        StageStatus.RECOVERY_REQUIRED,
        StageStatus.AWAITING_USER_DECISION,
    }
)


class LiveCLIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        if not code or not message:
            raise ValueError("LiveCLIError requires code and message")
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LiveAnalysisRequest:
    command: str
    company_query: str
    state_root: Path
    run_id: str
    jurisdiction: str | None = None

    def validate(self) -> None:
        if not self.command or not self.company_query or not self.run_id:
            raise LiveCLIError(
                "INVALID_LIVE_ANALYSIS_REQUEST",
                "live analysis request requires command, company query and run_id",
            )
        if not self.state_root:
            raise LiveCLIError(
                "INVALID_LIVE_ANALYSIS_REQUEST",
                "live analysis request requires state_root",
            )
        if self.jurisdiction is not None and not self.jurisdiction.strip():
            raise LiveCLIError(
                "INVALID_LIVE_ANALYSIS_REQUEST",
                "jurisdiction cannot be blank",
            )


LiveRuntimeConfigFactory = Callable[[LiveAnalysisRequest], LivePrimaryRuntimeConfig]
LiveRuntimeRunner = Callable[[LivePrimaryRuntimeConfig], ControlledRunResult]


def parse_analysis_command(command: str) -> str:
    text = command.strip()
    prefix = "분석시작"
    if text == prefix:
        raise LiveCLIError(
            "COMPANY_REQUIRED",
            "'분석시작' 뒤에 기업명을 입력해야 합니다",
        )
    if not text.startswith(prefix + " "):
        raise LiveCLIError(
            "INVALID_ANALYSIS_COMMAND",
            "분석 명령은 '분석시작 <기업>' 형식이어야 합니다",
        )
    company = text[len(prefix) :].strip()
    if not company:
        raise LiveCLIError(
            "COMPANY_REQUIRED",
            "분석 대상 기업명이 비어 있습니다",
        )
    return company


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def resolve_provider_factory_spec(
    explicit_spec: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    spec = (
        explicit_spec or environment.get(_PROVIDER_FACTORY_ENV, "")
    ).strip()
    if not spec:
        raise LiveCLIError(
            "LIVE_PROVIDER_FACTORY_REQUIRED",
            "LIVE_PRIMARY 실행에는 --provider-factory module:callable 또는 "
            f"{_PROVIDER_FACTORY_ENV} 설정이 필요합니다. OCI 회귀는 "
            "--legacy-oci를 명시해야 합니다",
        )
    return spec


def load_live_runtime_config_factory(spec: str) -> LiveRuntimeConfigFactory:
    module_name, separator, attribute_path = spec.strip().partition(":")
    if separator != ":" or not module_name or not attribute_path:
        raise LiveCLIError(
            "INVALID_PROVIDER_FACTORY",
            "provider factory는 'python.module:callable' 형식이어야 합니다",
        )
    try:
        value: object = import_module(module_name)
        for attribute in attribute_path.split("."):
            if not attribute:
                raise AttributeError("blank attribute component")
            value = getattr(value, attribute)
    except Exception as exc:
        raise LiveCLIError(
            "PROVIDER_FACTORY_LOAD_FAILED",
            f"provider factory {spec!r}를 불러오지 못했습니다 "
            f"({type(exc).__name__}); 상세 예외는 터미널에 출력하지 않습니다",
        ) from exc
    if not callable(value):
        raise LiveCLIError(
            "PROVIDER_FACTORY_NOT_CALLABLE",
            f"provider factory {spec!r}는 호출 가능해야 합니다",
        )
    return value


def _normalized_jurisdiction(value: str | None) -> str | None:
    if value is None:
        return None
    return normalize_jurisdiction(value)


def _bind_packaged_runtime_registries(
    config: LivePrimaryRuntimeConfig,
) -> LivePrimaryRuntimeConfig:
    updates: dict[str, Path] = {}
    dataclass_fields = LivePrimaryRuntimeConfig.__dataclass_fields__
    for field_name, filename in _RUNTIME_REGISTRY_FIELDS.items():
        configured = Path(getattr(config, field_name))
        default_path = Path(dataclass_fields[field_name].default)
        if configured == default_path:
            try:
                updates[field_name] = runtime_registry_path(filename)
            except Exception as exc:
                raise LiveCLIError(
                    "LIVE_RUNTIME_REGISTRY_UNAVAILABLE",
                    f"packaged runtime registry를 불러오지 못했습니다: "
                    f"{field_name} ({type(exc).__name__})",
                ) from exc
            continue
        if not configured.is_file():
            raise LiveCLIError(
                "INVALID_LIVE_RUNTIME_CONFIG",
                f"명시된 runtime registry가 존재하지 않습니다: {field_name}",
            )
    return replace(config, **updates) if updates else config


def _lock_resolved_jurisdiction(
    providers: LivePrimaryProviders,
    *,
    locked_jurisdiction: str | None,
) -> LivePrimaryProviders:
    if locked_jurisdiction is None:
        return providers
    expected = normalize_jurisdiction(locked_jurisdiction)
    resolver = providers.company_resolver

    def resolve(request):
        identity = resolver(request)
        if not isinstance(identity, ResolvedCompanyIdentity):
            return identity
        actual = normalize_jurisdiction(identity.jurisdiction)
        if actual != expected:
            raise ValueError(
                "resolved company jurisdiction does not match the locked request"
            )
        return identity

    return replace(providers, company_resolver=resolve)


def build_live_runtime_config(
    request: LiveAnalysisRequest,
    factory: LiveRuntimeConfigFactory,
) -> LivePrimaryRuntimeConfig:
    request.validate()
    try:
        config = factory(request)
    except Exception as exc:
        raise LiveCLIError(
            "PROVIDER_FACTORY_FAILED",
            "LIVE_PRIMARY provider factory 실행에 실패했습니다 "
            f"({type(exc).__name__}); 상세 예외는 터미널에 출력하지 않습니다",
        ) from exc
    if not isinstance(config, LivePrimaryRuntimeConfig):
        raise LiveCLIError(
            "INVALID_LIVE_RUNTIME_CONFIG",
            "provider factory는 LivePrimaryRuntimeConfig를 반환해야 합니다",
        )

    try:
        if config.run_id != request.run_id:
            raise LiveCLIError(
                "LIVE_RUNTIME_IDENTITY_MISMATCH",
                "provider factory가 CLI run_id를 변경했습니다",
            )
        requested_root = request.state_root.expanduser().resolve()
        configured_root = Path(config.state_root).expanduser().resolve()
        if configured_root != requested_root:
            raise LiveCLIError(
                "LIVE_RUNTIME_STATE_ROOT_MISMATCH",
                "provider factory가 CLI state_root를 변경했습니다",
            )
        if config.company_request.query.strip() != request.company_query:
            raise LiveCLIError(
                "LIVE_RUNTIME_COMPANY_MISMATCH",
                "provider factory의 CompanyResolutionRequest가 CLI 기업명과 다릅니다",
            )
        requested_jurisdiction = _normalized_jurisdiction(
            request.jurisdiction
        )
        configured_jurisdiction = _normalized_jurisdiction(
            config.company_request.jurisdiction
        )
        if (
            requested_jurisdiction is not None
            and configured_jurisdiction != requested_jurisdiction
        ):
            raise LiveCLIError(
                "LIVE_RUNTIME_JURISDICTION_MISMATCH",
                "provider factory의 jurisdiction이 CLI 요청과 다릅니다",
            )

        config = _bind_packaged_runtime_registries(config)
        locked_jurisdiction = (
            request.jurisdiction
            if request.jurisdiction is not None
            else config.company_request.jurisdiction
        )
        config = replace(
            config,
            providers=_lock_resolved_jurisdiction(
                config.providers,
                locked_jurisdiction=locked_jurisdiction,
            ),
        )
        config.validate()
    except LiveCLIError:
        raise
    except Exception as exc:
        raise LiveCLIError(
            "INVALID_LIVE_RUNTIME_CONFIG",
            "LIVE_PRIMARY runtime config 검증에 실패했습니다 "
            f"({type(exc).__name__}); 상세 예외는 터미널에 출력하지 않습니다",
        ) from exc
    return config


def execute_live_analysis(
    command: str,
    *,
    state_root: str | Path,
    provider_factory: LiveRuntimeConfigFactory,
    run_id: str | None = None,
    jurisdiction: str | None = None,
    runner: LiveRuntimeRunner = run_prism,
    major_gate_reporter: MajorGateReporter | None = None,
) -> ControlledRunResult:
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
        result = runner(config)
    except Exception as exc:
        raise LiveCLIError(
            "LIVE_PRIMARY_EXECUTION_FAILED",
            "LIVE_PRIMARY 실행에 실패했습니다 "
            f"({type(exc).__name__}); 상세 예외는 터미널에 출력하지 않습니다",
        ) from exc
    if not isinstance(result, ControlledRunResult):
        raise LiveCLIError(
            "INVALID_LIVE_RUNTIME_RESULT",
            "LIVE_PRIMARY runner는 ControlledRunResult를 반환해야 합니다",
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


def _blocked_codes(result: ControlledRunResult) -> tuple[str, ...]:
    codes = tuple(
        dict.fromkeys(
            f"{trace.stage}:{trace.status.name}"
            for trace in result.stage_traces
            if trace.blocking or trace.status in _BLOCKING_STATUSES
        )
    )
    return codes or ("LIVE_PRIMARY:BLOCKED",)


def render_major_gate_summary(summary: MajorGateSummary) -> str:
    return "\n".join(
        (
            f"## 대형 게이트 {summary.ordinal}/{summary.gate_count} — {summary.title}",
            f"- 상태: {status_label_ko(summary.status)} "
            f"({summary.completed_stage_count}/{summary.expected_stage_count})",
            f"- 핵심 결과: {localize_stage_references(summary.decisive_result)}",
            f"- 잔여위험: {localize_stage_references(summary.residual_risk)}",
            f"- 다음 단계: {next_action_label_ko(summary.next_action)}",
        )
    )


def render_controlled_run(
    result: ControlledRunResult,
    *,
    include_gate_summaries: bool = True,
) -> str:
    lines: list[str] = []
    if include_gate_summaries and result.major_gate_summaries:
        for summary in result.major_gate_summaries:
            if lines:
                lines.append("")
            lines.append(render_major_gate_summary(summary))
    elif include_gate_summaries:
        total = len(result.stage_traces)
        lines.extend(
            f"[{index:02d}/{total:02d}] {trace.stage}: {trace.status.value}"
            for index, trace in enumerate(result.stage_traces, start=1)
        )
    if result.blocked_reasons:
        lines.extend(("", "# VALUATION BLOCKED", "", "## 차단 코드"))
        lines.extend(f"- {code}" for code in _blocked_codes(result))
        lines.extend(
            (
                "",
                "Provider 예외 상세와 Intrinsic Value·Street·현재가 결과는 "
                "차단된 실행의 터미널 출력에서 제외합니다.",
            )
        )
        return "\n".join(lines) + "\n"

    if result.reporting_warnings:
        lines.extend(("", "## Reporting warnings"))
        lines.extend(f"- {item}" for item in result.reporting_warnings)

    report = result.data.get("final_report")
    if not isinstance(report, str) or not report.strip():
        raise LiveCLIError(
            "LIVE_REPORT_MISSING",
            "완료된 LIVE_PRIMARY 실행에 final_report가 없습니다",
        )
    lines.extend(("", report.rstrip()))
    return "\n".join(lines) + "\n"
