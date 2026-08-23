from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .module_plan import ModuleRequirementPlan


_PRIMARY_SOURCE_ROLES = frozenset({"observed_state", "company_primary"})


class CollectionRequirementKind(str, Enum):
    REQUIRED_EVIDENCE = "required_evidence"
    SUPPORTING_KPI = "supporting_kpi"


class SourceMatchKind(str, Enum):
    EXACT_METRIC = "exact_metric"
    COMPANY_PRIMARY_FALLBACK = "company_primary_fallback"


class CollectionReadiness(str, Enum):
    COLLECTOR_READY = "COLLECTOR_READY"
    SOURCE_CANDIDATE_ONLY = "SOURCE_CANDIDATE_ONLY"
    NO_SOURCE_CANDIDATE = "NO_SOURCE_CANDIDATE"


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    authority: str
    roles: tuple[str, ...]
    access: str
    industries: tuple[str, ...]
    metrics: tuple[str, ...]

    def validate(self) -> None:
        if not self.source_id or not self.authority or not self.roles or not self.access:
            raise ValueError(f"source descriptor {self.source_id!r} is incomplete")


@dataclass(frozen=True)
class SourceCandidate:
    source_id: str
    authority: str
    access: str
    match_kind: SourceMatchKind


@dataclass(frozen=True)
class CollectorCapability:
    collector_id: str
    source_id: str
    supported_metrics: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    implementation_ref: str

    def validate(self) -> None:
        if not all((self.collector_id, self.source_id, self.supported_metrics, self.jurisdictions, self.implementation_ref)):
            raise ValueError("collector capability requires identity, source, metric, jurisdiction and implementation")
        if len(self.supported_metrics) != len(set(self.supported_metrics)):
            raise ValueError(f"collector capability {self.collector_id} has duplicate metrics")

    def supports(self, *, metric: str, jurisdiction: str) -> bool:
        jurisdiction_key = _normalize_jurisdiction(jurisdiction)
        supported = {_normalize_jurisdiction(value) for value in self.jurisdictions}
        return metric in self.supported_metrics and ("GLOBAL" in supported or jurisdiction_key in supported)


@dataclass(frozen=True)
class MetricCollectionPlan:
    metric: str
    kind: CollectionRequirementKind
    candidates: tuple[SourceCandidate, ...]
    collector_ids: tuple[str, ...]

    @property
    def readiness(self) -> CollectionReadiness:
        if self.collector_ids:
            return CollectionReadiness.COLLECTOR_READY
        if self.candidates:
            return CollectionReadiness.SOURCE_CANDIDATE_ONLY
        return CollectionReadiness.NO_SOURCE_CANDIDATE


@dataclass(frozen=True)
class PrimaryCollectionPlan:
    target_id: str
    jurisdiction: str
    requirements: tuple[MetricCollectionPlan, ...]

    @property
    def required_evidence(self) -> tuple[MetricCollectionPlan, ...]:
        return tuple(item for item in self.requirements if item.kind is CollectionRequirementKind.REQUIRED_EVIDENCE)

    @property
    def supporting_kpis(self) -> tuple[MetricCollectionPlan, ...]:
        return tuple(item for item in self.requirements if item.kind is CollectionRequirementKind.SUPPORTING_KPI)

    @property
    def missing_required_metrics(self) -> tuple[str, ...]:
        return tuple(
            item.metric
            for item in self.required_evidence
            if item.readiness is not CollectionReadiness.COLLECTOR_READY
        )

    @property
    def no_source_required_metrics(self) -> tuple[str, ...]:
        return tuple(
            item.metric
            for item in self.required_evidence
            if item.readiness is CollectionReadiness.NO_SOURCE_CANDIDATE
        )

    @property
    def runnable_collector_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                collector_id
                for item in self.requirements
                for collector_id in item.collector_ids
            )
        )



def load_source_descriptors(path: str | Path) -> tuple[SourceDescriptor, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("industry source registry requires non-empty sources")
    result: list[SourceDescriptor] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("industry source registry rows must be mappings")
        source_id = str(row.get("id") or "").strip()
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        descriptor = SourceDescriptor(
            source_id=source_id,
            authority=str(row.get("authority") or "").strip(),
            roles=_strings(row.get("roles")),
            access=str(row.get("access") or "").strip(),
            industries=_strings(row.get("industries")),
            metrics=_strings(row.get("metrics")),
        )
        descriptor.validate()
        result.append(descriptor)
    return tuple(result)


def compile_primary_collection_plan(
    plan: ModuleRequirementPlan,
    *,
    target_id: str,
    jurisdiction: str,
    source_registry_path: str | Path,
    collector_capabilities: tuple[CollectorCapability, ...] = (),
    target_is_listed: bool = True,
) -> PrimaryCollectionPlan:
    plan.validate()
    if not target_id or not jurisdiction:
        raise ValueError("collection plan requires target_id and jurisdiction")
    for capability in collector_capabilities:
        capability.validate()
    collector_ids = tuple(item.collector_id for item in collector_capabilities)
    if len(collector_ids) != len(set(collector_ids)):
        raise ValueError("collector capabilities contain duplicate collector IDs")

    sources = load_source_descriptors(source_registry_path)
    required = tuple(plan.required_evidence)
    supporting = tuple(metric for metric in plan.required_kpis if metric not in set(required))
    requirements: list[MetricCollectionPlan] = []
    for kind, metrics in (
        (CollectionRequirementKind.REQUIRED_EVIDENCE, required),
        (CollectionRequirementKind.SUPPORTING_KPI, supporting),
    ):
        for metric in metrics:
            candidates = _source_candidates(
                metric,
                sources=sources,
                jurisdiction=jurisdiction,
                target_is_listed=target_is_listed,
            )
            candidate_ids = {item.source_id for item in candidates}
            runnable = tuple(
                sorted(
                    capability.collector_id
                    for capability in collector_capabilities
                    if capability.source_id in candidate_ids
                    and capability.supports(metric=metric, jurisdiction=jurisdiction)
                )
            )
            requirements.append(MetricCollectionPlan(metric, kind, candidates, runnable))
    return PrimaryCollectionPlan(target_id, jurisdiction, tuple(requirements))


def _source_candidates(
    metric: str,
    *,
    sources: tuple[SourceDescriptor, ...],
    jurisdiction: str,
    target_is_listed: bool,
) -> tuple[SourceCandidate, ...]:
    exact: list[SourceCandidate] = []
    fallback: list[SourceCandidate] = []
    for source in sources:
        if not set(source.roles).intersection(_PRIMARY_SOURCE_ROLES):
            continue
        if not _jurisdiction_matches(source.source_id, jurisdiction):
            continue
        if metric in source.metrics:
            exact.append(
                SourceCandidate(
                    source.source_id,
                    source.authority,
                    source.access,
                    SourceMatchKind.EXACT_METRIC,
                )
            )
            continue
        if (
            target_is_listed
            and "company_primary" in source.roles
            and "listed_companies" in source.industries
        ):
            fallback.append(
                SourceCandidate(
                    source.source_id,
                    source.authority,
                    source.access,
                    SourceMatchKind.COMPANY_PRIMARY_FALLBACK,
                )
            )
    return tuple(
        sorted(
            (*exact, *fallback),
            key=lambda item: (
                0 if item.match_kind is SourceMatchKind.EXACT_METRIC else 1,
                item.source_id,
            ),
        )
    )


def _normalize_jurisdiction(value: str) -> str:
    text = value.strip().upper().replace(" ", "_")
    aliases = {
        "KOR": "KR",
        "KOREA": "KR",
        "SOUTH_KOREA": "KR",
        "REPUBLIC_OF_KOREA": "KR",
        "USA": "US",
        "UNITED_STATES": "US",
        "UNITED_STATES_OF_AMERICA": "US",
    }
    return aliases.get(text, text)


def _jurisdiction_matches(source_id: str, jurisdiction: str) -> bool:
    target = _normalize_jurisdiction(jurisdiction)
    prefix = source_id.split("_", 1)[0].upper()
    if prefix in {"GLOBAL", "INTL", "OECD", "IEA"}:
        return True
    if len(prefix) == 2 and prefix.isalpha():
        return prefix == target
    return True


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item not in (None, ""))
    raise ValueError(f"expected string/list, got {type(value).__name__}")
