from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import yaml

from .live_primary_adapters import ResolvedCompanyIdentity
from .module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan


_PRIMARY_SOURCE_ROLES = frozenset({"observed_state", "company_primary"})
_BROAD_SOURCE_INDUSTRIES = frozenset({"cross_industry", "listed_companies"})
_PLAN_VERSION = "0.5.2"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key in source registry: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping

_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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
        jurisdiction_key = normalize_jurisdiction(jurisdiction)
        supported = {normalize_jurisdiction(value) for value in self.jurisdictions}
        return metric in self.supported_metrics and ("GLOBAL" in supported or jurisdiction_key in supported)


@dataclass(frozen=True)
class CollectionRequirement:
    requirement_id: str
    segment_id: str
    metric: str
    kind: CollectionRequirementKind
    mandatory: bool
    source_candidates: tuple[SourceCandidate, ...]
    collector_ids: tuple[str, ...]

    @property
    def readiness(self) -> CollectionReadiness:
        if self.collector_ids:
            return CollectionReadiness.COLLECTOR_READY
        if self.source_candidates:
            return CollectionReadiness.SOURCE_CANDIDATE_ONLY
        return CollectionReadiness.NO_SOURCE_CANDIDATE


@dataclass(frozen=True)
class CollectionTask:
    task_id: str
    collector_id: str
    source_id: str
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyCollectionPlan:
    plan_id: str
    version: str
    company: ResolvedCompanyIdentity
    routing_hash: str
    requirements: tuple[CollectionRequirement, ...]
    tasks: tuple[CollectionTask, ...]

    def validate(self) -> None:
        self.company.validate()
        if not self.plan_id or not self.version or not self.routing_hash or not self.requirements:
            raise ValueError("CompanyCollectionPlan requires identity, version, routing hash and requirements")
        requirement_ids = tuple(item.requirement_id for item in self.requirements)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("CompanyCollectionPlan contains duplicate requirement IDs")
        task_ids = tuple(item.task_id for item in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("CompanyCollectionPlan contains duplicate task IDs")
        known_requirements = set(requirement_ids)
        for task in self.tasks:
            if not task.collector_id or not task.source_id or not task.requirement_ids:
                raise ValueError("CollectionTask is incomplete")
            unknown = set(task.requirement_ids) - known_requirements
            if unknown:
                raise ValueError(f"CollectionTask references unknown requirements: {sorted(unknown)}")

    @property
    def required_evidence(self) -> tuple[CollectionRequirement, ...]:
        return tuple(item for item in self.requirements if item.mandatory)

    @property
    def supporting_kpis(self) -> tuple[CollectionRequirement, ...]:
        return tuple(item for item in self.requirements if not item.mandatory)

    @property
    def missing_required_requirements(self) -> tuple[str, ...]:
        return tuple(
            item.requirement_id
            for item in self.required_evidence
            if item.readiness is not CollectionReadiness.COLLECTOR_READY
        )

    @property
    def missing_required_metrics(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.metric for item in self.required_evidence
            if item.readiness is not CollectionReadiness.COLLECTOR_READY
        ))

    @property
    def no_source_required_metrics(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.metric for item in self.required_evidence
            if item.readiness is CollectionReadiness.NO_SOURCE_CANDIDATE
        ))

    @property
    def runnable_collector_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(task.collector_id for task in self.tasks))

    def authorized_segment_metrics_for_collector(self, collector_id: str) -> tuple[tuple[str, str], ...]:
        if not collector_id:
            raise ValueError("collector_id is required")
        return tuple(
            (item.segment_id, item.metric)
            for item in self.requirements
            if collector_id in item.collector_ids
        )


def module_plan_routing_hash(plan: ModuleRequirementPlan) -> str:
    plan.validate()
    rows = [
        {
            "segment_id": segment.segment_id,
            "sector_adapter": segment.sector_adapter,
            "archetypes": segment.archetypes,
            "required_evidence": segment.required_evidence,
            "required_kpis": segment.required_kpis,
            "allowed_valuation_methods": segment.allowed_valuation_methods,
        }
        for segment in plan.segments
    ]
    return _stable_hash(rows)


def load_source_descriptors(path: str | Path) -> tuple[SourceDescriptor, ...]:
    payload = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
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


def compile_company_collection_plan(
    plan: ModuleRequirementPlan,
    *,
    company: ResolvedCompanyIdentity,
    source_registry_path: str | Path,
    collector_capabilities: tuple[CollectorCapability, ...] = (),
    target_is_listed: bool = True,
) -> CompanyCollectionPlan:
    plan.validate()
    company.validate()
    for capability in collector_capabilities:
        capability.validate()
    collector_ids = tuple(item.collector_id for item in collector_capabilities)
    if len(collector_ids) != len(set(collector_ids)):
        raise ValueError("collector capabilities contain duplicate collector IDs")
    capability_by_id = {item.collector_id: item for item in collector_capabilities}

    sources = load_source_descriptors(source_registry_path)
    requirements: list[CollectionRequirement] = []
    for segment in plan.segments:
        required = tuple(segment.required_evidence)
        supporting = tuple(metric for metric in segment.required_kpis if metric not in set(required))
        for kind, mandatory, metrics in (
            (CollectionRequirementKind.REQUIRED_EVIDENCE, True, required),
            (CollectionRequirementKind.SUPPORTING_KPI, False, supporting),
        ):
            for metric in metrics:
                candidates = _source_candidates(
                    metric,
                    sources=sources,
                    jurisdiction=company.jurisdiction,
                    segment=segment,
                    target_is_listed=target_is_listed,
                )
                candidate_ids = {item.source_id for item in candidates}
                runnable = tuple(sorted(
                    capability.collector_id
                    for capability in collector_capabilities
                    if capability.source_id in candidate_ids
                    and capability.supports(metric=metric, jurisdiction=company.jurisdiction)
                ))
                requirements.append(CollectionRequirement(
                    requirement_id=f"{segment.segment_id}:{kind.value}:{metric}",
                    segment_id=segment.segment_id,
                    metric=metric,
                    kind=kind,
                    mandatory=mandatory,
                    source_candidates=candidates,
                    collector_ids=runnable,
                ))

    routing_hash = module_plan_routing_hash(plan)
    tasks: list[CollectionTask] = []
    for collector_id in sorted({cid for item in requirements for cid in item.collector_ids}):
        capability = capability_by_id[collector_id]
        requirement_ids = tuple(item.requirement_id for item in requirements if collector_id in item.collector_ids)
        tasks.append(CollectionTask(
            task_id=f"TASK_{sha256((collector_id + '|' + '|'.join(requirement_ids)).encode('utf-8')).hexdigest()[:16]}",
            collector_id=collector_id,
            source_id=capability.source_id,
            requirement_ids=requirement_ids,
        ))

    plan_payload = {
        "version": _PLAN_VERSION,
        "target_id": company.target_id,
        "jurisdiction": normalize_jurisdiction(company.jurisdiction),
        "routing_hash": routing_hash,
        "requirements": [
            {
                "id": item.requirement_id,
                "segment": item.segment_id,
                "metric": item.metric,
                "kind": item.kind.value,
                "collectors": item.collector_ids,
                "sources": tuple(candidate.source_id for candidate in item.source_candidates),
            }
            for item in requirements
        ],
    }
    result = CompanyCollectionPlan(
        plan_id=f"COLLECTION_{_stable_hash(plan_payload)[:20]}",
        version=_PLAN_VERSION,
        company=company,
        routing_hash=routing_hash,
        requirements=tuple(requirements),
        tasks=tuple(tasks),
    )
    result.validate()
    return result


def _source_candidates(
    metric: str,
    *,
    sources: tuple[SourceDescriptor, ...],
    jurisdiction: str,
    segment: SegmentModuleRequirementPlan,
    target_is_listed: bool,
) -> tuple[SourceCandidate, ...]:
    exact: list[SourceCandidate] = []
    fallback: list[SourceCandidate] = []
    for source in sources:
        if not set(source.roles).intersection(_PRIMARY_SOURCE_ROLES):
            continue
        if not _jurisdiction_matches(source.source_id, jurisdiction):
            continue
        if not _source_matches_route(source, segment):
            continue
        if metric in source.metrics:
            exact.append(SourceCandidate(source.source_id, source.authority, source.access, SourceMatchKind.EXACT_METRIC))
            continue
        if target_is_listed and "company_primary" in source.roles and "listed_companies" in source.industries:
            fallback.append(SourceCandidate(source.source_id, source.authority, source.access, SourceMatchKind.COMPANY_PRIMARY_FALLBACK))
    return tuple(sorted(
        (*exact, *fallback),
        key=lambda item: (0 if item.match_kind is SourceMatchKind.EXACT_METRIC else 1, item.source_id),
    ))


def _source_matches_route(source: SourceDescriptor, segment: SegmentModuleRequirementPlan) -> bool:
    normalized_industries = {value.strip().lower() for value in source.industries}
    if normalized_industries.intersection(_BROAD_SOURCE_INDUSTRIES):
        return True
    source_tokens = _industry_tokens(source.industries)
    route_tokens = _industry_tokens((segment.sector_adapter, *segment.archetypes))
    return bool(source_tokens.intersection(route_tokens))


def _industry_tokens(values: tuple[str, ...]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for raw in re.split(r"[^a-zA-Z0-9]+", value.lower()):
            if not raw:
                continue
            token = _canonical_industry_token(raw)
            if token:
                tokens.add(token)
    return tokens


def _canonical_industry_token(token: str) -> str:
    aliases = {
        "auto": "automotive",
        "automotive": "automotive",
        "bank": "financial",
        "banks": "financial",
        "financial": "financial",
        "financials": "financial",
        "insurance": "financial",
        "insurer": "financial",
        "insurers": "financial",
        "securities": "financial",
        "pharma": "healthcare",
        "biotech": "healthcare",
        "biohealth": "healthcare",
        "medtech": "healthcare",
        "healthcare": "healthcare",
        "ship": "maritime",
        "ships": "maritime",
        "shipbuilder": "maritime",
        "shipbuilding": "maritime",
        "shipping": "maritime",
    }
    value = aliases.get(token, token)
    if value.endswith("s") and len(value) > 4:
        value = value[:-1]
    return value


def _stable_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def normalize_jurisdiction(value: str) -> str:
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
    target = normalize_jurisdiction(jurisdiction)
    prefix = source_id.split("_", 1)[0].upper()
    if prefix in {"GLOBAL", "INTL", "INT", "OECD", "IEA"}:
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
