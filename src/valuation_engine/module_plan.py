from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .industry_dna import IndustryDNAProfile


@dataclass(frozen=True)
class SegmentModuleRequirementPlan:
    segment_id: str
    sector_adapter: str
    archetypes: tuple[str, ...]
    required_evidence: tuple[str, ...]
    required_kpis: tuple[str, ...]
    mandatory_scanners: tuple[str, ...]
    kill_conditions: tuple[str, ...]
    normalization_rules: tuple[str, ...]
    beta_peer_features: tuple[str, ...]
    per_peer_features: tuple[str, ...]
    scenario_variables: tuple[str, ...]
    funding_scans: tuple[str, ...]
    terminal_policies: tuple[str, ...]
    double_count_traps: tuple[str, ...]
    forbidden_methods: tuple[str, ...]
    allowed_valuation_methods: tuple[str, ...]

    def validate(self) -> None:
        if not self.segment_id or not self.sector_adapter or not self.archetypes:
            raise ValueError("segment module plan requires segment, sector adapter and archetypes")
        if not self.required_evidence:
            raise ValueError(f"segment {self.segment_id} has no required evidence")
        if not self.required_kpis:
            raise ValueError(f"segment {self.segment_id} has no required KPIs")
        if not self.mandatory_scanners:
            raise ValueError(f"segment {self.segment_id} has no mandatory scanner loadout")
        if not self.kill_conditions:
            raise ValueError(f"segment {self.segment_id} has no kill conditions")
        if not self.allowed_valuation_methods:
            raise ValueError(f"segment {self.segment_id} has no allowed valuation methods")
        overlap = set(self.allowed_valuation_methods).intersection(self.forbidden_methods)
        if overlap:
            raise ValueError(
                f"segment {self.segment_id} method is both allowed and forbidden: {sorted(overlap)}"
            )


@dataclass(frozen=True)
class ModuleRequirementPlan:
    segments: tuple[SegmentModuleRequirementPlan, ...]
    common_core_modules: tuple[str, ...]
    required_evidence: tuple[str, ...]
    required_kpis: tuple[str, ...]
    mandatory_scanners: tuple[str, ...]
    kill_conditions: tuple[str, ...]
    scenario_variables: tuple[str, ...]
    double_count_traps: tuple[str, ...]
    forbidden_methods: tuple[str, ...]

    def validate(self) -> None:
        if not self.segments:
            raise ValueError("module requirement plan requires at least one segment")
        ids = tuple(segment.segment_id for segment in self.segments)
        if len(ids) != len(set(ids)):
            raise ValueError("module requirement plan contains duplicate segment IDs")
        if not self.common_core_modules:
            raise ValueError("module requirement plan requires common core modules")
        for segment in self.segments:
            segment.validate()

    def plan_for_segment(self, segment_id: str) -> SegmentModuleRequirementPlan:
        for segment in self.segments:
            if segment.segment_id == segment_id:
                return segment
        raise KeyError(segment_id)


COMMON_CORE_MODULES = (
    "industry_knowledge_freshness",
    "evidence_gate",
    "accounting_normalization",
    "hierarchical_beta",
    "wacc_validation",
    "upstream_funding",
    "scenario_distribution",
    "warranted_per_if_allowed",
    "double_count_audit",
    "intrinsic_value_freeze",
)


def _ordered_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _load_mapping(path: str | Path, key: str, *, label: str) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    mapping = payload.get(key)
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"{label} requires non-empty {key}")
    return mapping


def load_archetype_module_registry(path: str | Path) -> dict[str, dict[str, Any]]:
    return _load_mapping(path, "modules", label="archetype module registry")


def load_archetype_control_requirements(path: str | Path) -> dict[str, dict[str, Any]]:
    return _load_mapping(path, "requirements", label="archetype control requirements")


def _list_field(spec: dict[str, Any], key: str) -> list[str]:
    value = spec.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"archetype field {key} must be a string list")
    return value


def _scalar_field(spec: dict[str, Any], key: str) -> list[str]:
    value = spec.get(key)
    if value in (None, ""):
        return []
    if not isinstance(value, str):
        raise ValueError(f"archetype field {key} must be a string")
    return [value]


def build_module_requirement_plan(
    profiles: tuple[IndustryDNAProfile, ...],
    *,
    registry_path: str | Path,
    control_requirements_path: str | Path,
) -> ModuleRequirementPlan:
    """Compile Industry DNA into the complete research/valuation deployment contract.

    `archetype_module_registry` owns operating economics and valuation-method requirements.
    `archetype_control_requirements` owns only Control Plane deployment fields: required KPIs,
    mandatory scanners and generic kill-condition templates. Neither file duplicates the
    other's responsibility.
    """
    if not profiles:
        raise ValueError("Industry DNA profiles are required")
    registry = load_archetype_module_registry(registry_path)
    controls = load_archetype_control_requirements(control_requirements_path)
    segment_plans: list[SegmentModuleRequirementPlan] = []

    for profile in profiles:
        profile.validate()
        required_evidence: list[str] = []
        required_kpis: list[str] = []
        mandatory_scanners: list[str] = []
        kill_conditions: list[str] = []
        normalization: list[str] = []
        beta_features: list[str] = []
        per_features: list[str] = []
        scenario_variables: list[str] = []
        funding_scans: list[str] = []
        terminal_policies: list[str] = []
        double_count_traps: list[str] = []
        forbidden_methods: list[str] = []
        allowed_methods: list[str] = []
        archetypes = tuple(archetype.value for archetype in profile.archetypes)

        for archetype in archetypes:
            raw = registry.get(archetype)
            control = controls.get(archetype)
            if not isinstance(raw, dict):
                raise ValueError(f"archetype registry missing module: {archetype}")
            if not isinstance(control, dict):
                raise ValueError(f"archetype control requirements missing module: {archetype}")
            required_evidence.extend(_list_field(raw, "required_evidence"))
            required_kpis.extend(_list_field(control, "required_kpis"))
            mandatory_scanners.extend(_list_field(control, "mandatory_scanners"))
            kill_conditions.extend(_list_field(control, "kill_conditions"))
            normalization.extend(_list_field(raw, "normalization"))
            beta_features.extend(_list_field(raw, "beta_peer_features"))
            per_features.extend(_list_field(raw, "per_peer_features"))
            scenario_variables.extend(_list_field(raw, "scenario_variables"))
            funding_scans.extend(_scalar_field(raw, "funding_scan"))
            terminal_policies.extend(_scalar_field(raw, "terminal_policy"))
            double_count_traps.extend(_list_field(raw, "double_count_traps"))
            forbidden_methods.extend(_list_field(raw, "forbidden_methods"))
            allowed_methods.extend(_list_field(raw, "allowed_valuation_methods"))

        segment_plan = SegmentModuleRequirementPlan(
            segment_id=profile.segment_id,
            sector_adapter=profile.sector_adapter,
            archetypes=archetypes,
            required_evidence=_ordered_unique(required_evidence),
            required_kpis=_ordered_unique(required_kpis),
            mandatory_scanners=_ordered_unique(mandatory_scanners),
            kill_conditions=_ordered_unique(kill_conditions),
            normalization_rules=_ordered_unique(normalization),
            beta_peer_features=_ordered_unique(beta_features),
            per_peer_features=_ordered_unique(per_features),
            scenario_variables=_ordered_unique(scenario_variables),
            funding_scans=_ordered_unique(funding_scans),
            terminal_policies=_ordered_unique(terminal_policies),
            double_count_traps=_ordered_unique(double_count_traps),
            forbidden_methods=_ordered_unique(forbidden_methods),
            allowed_valuation_methods=_ordered_unique(allowed_methods),
        )
        segment_plan.validate()
        segment_plans.append(segment_plan)

    plan = ModuleRequirementPlan(
        segments=tuple(segment_plans),
        common_core_modules=COMMON_CORE_MODULES,
        required_evidence=_ordered_unique([item for segment in segment_plans for item in segment.required_evidence]),
        required_kpis=_ordered_unique([item for segment in segment_plans for item in segment.required_kpis]),
        mandatory_scanners=_ordered_unique([item for segment in segment_plans for item in segment.mandatory_scanners]),
        kill_conditions=_ordered_unique([item for segment in segment_plans for item in segment.kill_conditions]),
        scenario_variables=_ordered_unique([item for segment in segment_plans for item in segment.scenario_variables]),
        double_count_traps=_ordered_unique([item for segment in segment_plans for item in segment.double_count_traps]),
        forbidden_methods=_ordered_unique([item for segment in segment_plans for item in segment.forbidden_methods]),
    )
    plan.validate()
    return plan
