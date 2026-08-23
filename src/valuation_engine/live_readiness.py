from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

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


def load_live_primary_readiness(
    *,
    readiness_path: str | Path,
    stage_registry_path: str | Path,
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
    return LivePrimaryReadinessReport(tuple(result))
