from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from .method_capabilities import (
    MethodCapabilityRegistry,
    MethodCoverageSummary,
    MethodKind,
    MethodRuntimeStatus,
    load_method_capability_registry,
)
from .orchestrator import load_stage_sequence


class LiveReadinessStatus(str, Enum):
    LIVE_READY = "LIVE_READY"
    PARTIAL_LIVE = "PARTIAL_LIVE"
    RUNTIME_READY = "RUNTIME_READY"
    ADAPTER_REQUIRED = "ADAPTER_REQUIRED"
    SHADOW_ONLY = "SHADOW_ONLY"
    CONDITIONAL_NOT_IMPLEMENTED = "CONDITIONAL_NOT_IMPLEMENTED"


@dataclass(frozen=True)
class StageReadiness:
    stage: str
    status: LiveReadinessStatus
    reason: str


@dataclass(frozen=True)
class LivePrimaryReadinessReport:
    stages: tuple[StageReadiness, ...]
    deterministic_method_coverage: MethodCoverageSummary | None = None

    @property
    def canonical_live_ready_count(self) -> int:
        return sum(
            item.status in {
                LiveReadinessStatus.LIVE_READY,
                LiveReadinessStatus.RUNTIME_READY,
            }
            for item in self.stages
        )

    @property
    def unresolved_live_stages(self) -> tuple[StageReadiness, ...]:
        return tuple(
            item
            for item in self.stages
            if item.status in {
                LiveReadinessStatus.ADAPTER_REQUIRED,
                LiveReadinessStatus.SHADOW_ONLY,
                LiveReadinessStatus.CONDITIONAL_NOT_IMPLEMENTED,
            }
        )

    @property
    def partial_live_stages(self) -> tuple[StageReadiness, ...]:
        return tuple(item for item in self.stages if item.status is LiveReadinessStatus.PARTIAL_LIVE)


def _deterministic_method_coverage(
    registry: MethodCapabilityRegistry,
) -> MethodCoverageSummary:
    relevant = tuple(
        item
        for item in registry.capabilities
        if item.kind is not MethodKind.CROSS_METHOD_ENGINE
    )

    def label(item) -> str:
        return f"{item.archetype}/{item.method}"

    ready = tuple(
        sorted(
            label(item)
            for item in relevant
            if item.runtime_status is MethodRuntimeStatus.RUNTIME_READY
        )
    )
    partial = tuple(
        sorted(
            label(item)
            for item in relevant
            if item.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME
        )
    )
    missing = tuple(
        sorted(
            label(item)
            for item in relevant
            if item.runtime_status is MethodRuntimeStatus.NOT_IMPLEMENTED
        )
    )
    return MethodCoverageSummary(len(relevant), ready, partial, missing)


def validate_method_readiness_alignment(
    report: LivePrimaryReadinessReport,
    registry: MethodCapabilityRegistry,
) -> MethodCoverageSummary:
    coverage = _deterministic_method_coverage(registry)
    by_stage = {item.stage: item for item in report.stages}
    stage = by_stage.get("DETERMINISTIC_VALUATION")
    if stage is None:
        raise ValueError("DETERMINISTIC_VALUATION readiness row is required")

    if coverage.complete:
        if stage.status not in {
            LiveReadinessStatus.RUNTIME_READY,
            LiveReadinessStatus.LIVE_READY,
        }:
            raise ValueError(
                "DETERMINISTIC_VALUATION method coverage is complete but readiness is not runtime/live ready"
            )
    elif stage.status in {
        LiveReadinessStatus.RUNTIME_READY,
        LiveReadinessStatus.LIVE_READY,
    }:
        raise ValueError(
            "DETERMINISTIC_VALUATION cannot be promoted above PARTIAL_LIVE while valuation methods remain partial or unimplemented: "
            f"partial={coverage.partial_runtime}, not_implemented={coverage.not_implemented}"
        )
    return coverage


def load_live_primary_readiness(
    *,
    readiness_path: str | Path,
    stage_registry_path: str | Path,
    method_capability_path: str | Path | None = None,
    archetype_registry_path: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> LivePrimaryReadinessReport:
    payload = yaml.safe_load(Path(readiness_path).read_text(encoding="utf-8"))
    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, dict) or not raw_stages:
        raise ValueError("LIVE_PRIMARY readiness registry requires non-empty stages")

    canonical = load_stage_sequence(stage_registry_path)
    configured = tuple(raw_stages)
    missing = tuple(stage for stage in canonical if stage not in raw_stages)
    extra = tuple(stage for stage in configured if stage not in canonical)
    if missing or extra:
        raise ValueError(f"readiness/stage-registry mismatch: missing={missing}, extra={extra}")

    result: list[StageReadiness] = []
    for stage in canonical:
        row = raw_stages[stage]
        if not isinstance(row, dict):
            raise ValueError(f"readiness row must be a mapping: {stage}")
        try:
            status = LiveReadinessStatus(str(row["status"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid readiness status for {stage}") from exc
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"readiness reason is required for {stage}")
        result.append(StageReadiness(stage, status, reason))

    report = LivePrimaryReadinessReport(tuple(result))
    if method_capability_path is None:
        return report
    if archetype_registry_path is None:
        raise ValueError("archetype_registry_path is required with method_capability_path")

    registry = load_method_capability_registry(method_capability_path)
    registry.validate(
        archetype_registry_path=archetype_registry_path,
        repo_root=repo_root,
    )
    coverage = validate_method_readiness_alignment(report, registry)
    return LivePrimaryReadinessReport(report.stages, coverage)
