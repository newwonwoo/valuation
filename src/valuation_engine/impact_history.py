from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from .decision_impact import ImpactPolicy, ModuleHistoryEntry, ModuleImpactAssessment, ResearchEffort
from .impact_orchestrator import (
    AblationReport,
    AdaptiveLoadoutPlan,
    ModuleExperimentSpec,
    ModuleHistory,
    build_adaptive_loadout,
)


@dataclass(frozen=True)
class ImpactHistoryRecord:
    record_id: str
    run_id: str
    module_id: str
    assessment: ModuleImpactAssessment
    effort: ResearchEffort
    applicable: bool
    research_performed: bool
    mandatory_guardrail: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.record_id or not self.run_id or not self.module_id:
            raise ValueError("impact history identity fields are required")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.assessment.module_id != self.module_id:
            raise ValueError("history record module_id must match assessment")

    @property
    def history_entry(self) -> ModuleHistoryEntry:
        return ModuleHistoryEntry(
            assessment=self.assessment,
            effort=self.effort,
            applicable=self.applicable,
            research_performed=self.research_performed,
            mandatory_guardrail=self.mandatory_guardrail,
        )


class ModuleImpactHistoryLedger:
    """Append-only module-impact history used by State/Learning and next-run loadouts."""

    def __init__(self, records: Iterable[ImpactHistoryRecord] = ()) -> None:
        self._records: dict[str, ImpactHistoryRecord] = {}
        for record in records:
            self.append(record)

    def append(self, record: ImpactHistoryRecord) -> None:
        if record.record_id in self._records:
            raise ValueError(f"duplicate impact history record: {record.record_id}")
        self._records[record.record_id] = record

    def append_ablation_report(
        self,
        *,
        run_id: str,
        report: AblationReport,
        specs: tuple[ModuleExperimentSpec, ...],
        observed_at: datetime | None = None,
    ) -> tuple[ImpactHistoryRecord, ...]:
        if not run_id:
            raise ValueError("run_id is required")
        observed_at = observed_at or datetime.now(timezone.utc)
        spec_map = {spec.module_id: spec for spec in specs}
        if len(spec_map) != len(specs):
            raise ValueError("specs must have unique module_id values")

        appended: list[ImpactHistoryRecord] = []
        for row in report.module_results:
            spec = spec_map.get(row.module_id)
            if spec is None:
                raise ValueError(f"missing experiment spec for result {row.module_id}")
            record = ImpactHistoryRecord(
                record_id=f"{run_id}:{row.module_id}",
                run_id=run_id,
                module_id=row.module_id,
                assessment=row.assessment,
                effort=spec.effort,
                applicable=spec.applicable,
                research_performed=spec.research_performed,
                mandatory_guardrail=spec.mandatory_guardrail,
                observed_at=observed_at,
            )
            self.append(record)
            appended.append(record)
        return tuple(appended)

    def records(self) -> tuple[ImpactHistoryRecord, ...]:
        return tuple(
            sorted(
                self._records.values(),
                key=lambda record: (record.observed_at, record.record_id),
            )
        )

    def for_module(self, module_id: str) -> tuple[ImpactHistoryRecord, ...]:
        return tuple(record for record in self.records() if record.module_id == module_id)

    def module_histories(self) -> tuple[ModuleHistory, ...]:
        module_ids = sorted({record.module_id for record in self._records.values()})
        return tuple(
            ModuleHistory(
                module_id,
                tuple(record.history_entry for record in self.for_module(module_id)),
            )
            for module_id in module_ids
        )

    def adaptive_loadout(
        self,
        specs: tuple[ModuleExperimentSpec, ...],
        *,
        policy: ImpactPolicy | None = None,
    ) -> AdaptiveLoadoutPlan:
        return build_adaptive_loadout(specs, self.module_histories(), policy=policy)

    def to_list(self) -> list[dict]:
        return [_enum_values(asdict(record)) for record in self.records()]


def _enum_values(value):
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
