from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from .decision_impact import ResearchEffort
from .impact_orchestrator import ModuleExperimentSpec
from .industry_dna import IndustryDNAProfile


@dataclass(frozen=True)
class ScannerRequirement:
    scanner_id: str
    mandatory: bool
    interaction_group: str | None
    origins: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scanner_id:
            raise ValueError("scanner_id is required")
        if not self.origins:
            raise ValueError("scanner requirement requires at least one origin")


@dataclass(frozen=True)
class ModuleRequirementPlan:
    segment_id: str
    sector_adapter: str
    archetypes: tuple[str, ...]
    common_units: tuple[str, ...]
    scanners: tuple[ScannerRequirement, ...]
    required_evidence: tuple[str, ...]
    required_kpis: tuple[str, ...]
    normalization_rules: tuple[str, ...]
    beta_peer_features: tuple[str, ...]
    per_peer_features: tuple[str, ...]
    scenario_variables: tuple[str, ...]
    funding_scans: tuple[str, ...]
    terminal_policies: tuple[str, ...]
    allowed_valuation_methods: tuple[str, ...]
    forbidden_methods: tuple[str, ...]
    double_count_traps: tuple[str, ...]
    special_risks: tuple[str, ...]
    kill_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.segment_id or not self.sector_adapter or not self.archetypes:
            raise ValueError("module requirement plan requires segment, adapter and archetypes")
        if not self.required_evidence:
            raise ValueError("module requirement plan requires evidence")
        if not self.allowed_valuation_methods:
            raise ValueError("module requirement plan requires at least one allowed method")
        scanner_ids = tuple(scanner.scanner_id for scanner in self.scanners)
        if len(scanner_ids) != len(set(scanner_ids)):
            raise ValueError("module requirement plan scanners must be unique")

    @property
    def mandatory_scanner_ids(self) -> tuple[str, ...]:
        return tuple(scanner.scanner_id for scanner in self.scanners if scanner.mandatory)

    @property
    def optional_scanner_ids(self) -> tuple[str, ...]:
        return tuple(scanner.scanner_id for scanner in self.scanners if not scanner.mandatory)


@dataclass
class _ScannerAccumulator:
    scanner_id: str
    mandatory: bool = False
    interaction_group: str | None = None
    origins: list[str] = field(default_factory=list)

    def merge(self, *, mandatory: bool, interaction_group: str | None, origin: str) -> None:
        self.mandatory = self.mandatory or mandatory
        if self.interaction_group and interaction_group and self.interaction_group != interaction_group:
            raise ValueError(
                f"scanner {self.scanner_id} has conflicting interaction groups: "
                f"{self.interaction_group}, {interaction_group}"
            )
        self.interaction_group = self.interaction_group or interaction_group
        if origin not in self.origins:
            self.origins.append(origin)

    def freeze(self) -> ScannerRequirement:
        return ScannerRequirement(
            self.scanner_id,
            self.mandatory,
            self.interaction_group,
            tuple(self.origins),
        )


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def build_module_requirement_plan(
    profile: IndustryDNAProfile,
    *,
    archetype_registry: Mapping[str, Any],
    sector_adapter_registry: Mapping[str, Any],
    scanner_map: Mapping[str, Any],
) -> ModuleRequirementPlan:
    """Compile Industry DNA into deterministic evidence, scanner and method requirements."""
    profile.validate()
    modules = archetype_registry.get("modules")
    adapters = sector_adapter_registry.get("adapters")
    scanner_rules = scanner_map.get("archetype_scanners")
    if not isinstance(modules, dict) or not isinstance(adapters, dict) or not isinstance(scanner_rules, dict):
        raise ValueError("module, adapter and scanner registries require mapping sections")

    adapter = adapters.get(profile.sector_adapter)
    if not isinstance(adapter, dict):
        raise ValueError(f"unknown sector adapter: {profile.sector_adapter}")

    selected_archetypes = tuple(archetype.value for archetype in profile.archetypes)
    declared_archetypes = set(_strings(adapter.get("default_archetypes"))) | set(
        _strings(adapter.get("optional_archetypes"))
    )
    undeclared = tuple(sorted(set(selected_archetypes) - declared_archetypes))
    if undeclared:
        raise ValueError(
            f"sector adapter {profile.sector_adapter} does not permit archetypes: "
            + ", ".join(undeclared)
        )

    required_evidence: list[str] = list(_strings(adapter.get("key_evidence")))
    normalization: list[str] = []
    beta_features: list[str] = []
    per_features: list[str] = []
    scenario_variables: list[str] = []
    funding_scans: list[str] = []
    terminal_policies: list[str] = []
    allowed_methods: list[str] = []
    forbidden_methods: list[str] = []
    double_count_traps: list[str] = []
    kill_conditions: list[str] = []
    scanner_accumulators: dict[str, _ScannerAccumulator] = {}

    for archetype in selected_archetypes:
        module = modules.get(archetype)
        if not isinstance(module, dict):
            raise ValueError(f"missing archetype module contract: {archetype}")
        rule = scanner_rules.get(archetype)
        if not isinstance(rule, dict):
            raise ValueError(f"missing scanner map for archetype: {archetype}")

        _extend(required_evidence, _strings(module.get("required_evidence")))
        _extend(normalization, _strings(module.get("normalization")))
        _extend(beta_features, _strings(module.get("beta_peer_features")))
        _extend(per_features, _strings(module.get("per_peer_features")))
        _extend(scenario_variables, _strings(module.get("scenario_variables")))
        _extend(funding_scans, _strings(module.get("funding_scan")))
        _extend(terminal_policies, _strings(module.get("terminal_policy")))
        _extend(allowed_methods, _strings(module.get("allowed_valuation_methods")))
        _extend(forbidden_methods, _strings(module.get("forbidden_methods")))
        _extend(double_count_traps, _strings(module.get("double_count_traps")))
        _extend(kill_conditions, _strings(rule.get("kill_conditions")))

        default_group = _optional_string(rule.get("interaction_group"))
        scanners = rule.get("scanners")
        if not isinstance(scanners, list) or not scanners:
            raise ValueError(f"archetype {archetype} requires scanner definitions")
        for raw in scanners:
            if not isinstance(raw, dict) or not raw.get("scanner_id"):
                raise ValueError(f"invalid scanner rule for archetype {archetype}")
            scanner_id = str(raw["scanner_id"])
            accumulator = scanner_accumulators.setdefault(
                scanner_id, _ScannerAccumulator(scanner_id)
            )
            accumulator.merge(
                mandatory=bool(raw.get("mandatory", False)),
                interaction_group=_optional_string(raw.get("interaction_group")) or default_group,
                origin=f"archetype:{archetype}",
            )

    special_risks = _strings(adapter.get("special_risks"))
    risk_aliases = scanner_map.get("risk_scanner_aliases", {})
    if not isinstance(risk_aliases, dict):
        raise ValueError("risk_scanner_aliases must be a mapping")
    for risk in special_risks:
        scanner_id = risk_aliases.get(risk)
        if not scanner_id:
            continue
        scanner_id = str(scanner_id)
        accumulator = scanner_accumulators.setdefault(
            scanner_id, _ScannerAccumulator(scanner_id)
        )
        accumulator.merge(
            mandatory=False,
            interaction_group=f"risk:{risk}",
            origin=f"sector_risk:{risk}",
        )

    scanners = tuple(
        scanner_accumulators[scanner_id].freeze()
        for scanner_id in sorted(scanner_accumulators)
    )
    common_units = _strings(scanner_map.get("common_units"))
    required_kpis = _dedupe((*required_evidence, *scenario_variables))

    return ModuleRequirementPlan(
        segment_id=profile.segment_id,
        sector_adapter=profile.sector_adapter,
        archetypes=selected_archetypes,
        common_units=common_units,
        scanners=scanners,
        required_evidence=_dedupe(required_evidence),
        required_kpis=required_kpis,
        normalization_rules=_dedupe(normalization),
        beta_peer_features=_dedupe(beta_features),
        per_peer_features=_dedupe(per_features),
        scenario_variables=_dedupe(scenario_variables),
        funding_scans=_dedupe(funding_scans),
        terminal_policies=_dedupe(terminal_policies),
        allowed_valuation_methods=_dedupe(allowed_methods),
        forbidden_methods=_dedupe(forbidden_methods),
        double_count_traps=_dedupe(double_count_traps),
        special_risks=special_risks,
        kill_conditions=_dedupe(kill_conditions),
    )


def build_module_requirement_plan_from_repo(
    profile: IndustryDNAProfile,
    *,
    repo_root: str | Path = ".",
) -> ModuleRequirementPlan:
    root = Path(repo_root)
    return build_module_requirement_plan(
        profile,
        archetype_registry=load_yaml_mapping(root / "config/archetype_module_registry.yaml"),
        sector_adapter_registry=load_yaml_mapping(root / "config/sector_adapter_registry.yaml"),
        scanner_map=load_yaml_mapping(root / "config/module_requirement_scanner_map.yaml"),
    )


def experiment_specs_from_plan(
    plan: ModuleRequirementPlan,
    *,
    effort_by_scanner: Mapping[str, ResearchEffort] | None = None,
    condition_by_scanner: Mapping[str, bool] | None = None,
    sample_due_by_scanner: Mapping[str, bool] | None = None,
) -> tuple[ModuleExperimentSpec, ...]:
    """Create Decision Impact experiment specs for the plan's research scanners.

    `mandatory` means mission-required for this Industry DNA; it is passed separately to the
    Control Plane as `plan.mandatory_scanner_ids`. It is not mislabeled as a permanent
    guardrail, because research priority may still be recalibrated after sufficient history.
    """
    effort_by_scanner = effort_by_scanner or {}
    condition_by_scanner = condition_by_scanner or {}
    sample_due_by_scanner = sample_due_by_scanner or {}
    return tuple(
        ModuleExperimentSpec(
            module_id=scanner.scanner_id,
            applicable=True,
            mandatory_guardrail=False,
            interaction_group=scanner.interaction_group,
            condition_met=condition_by_scanner.get(scanner.scanner_id, True),
            sample_due=sample_due_by_scanner.get(scanner.scanner_id, True),
            effort=effort_by_scanner.get(scanner.scanner_id, ResearchEffort()),
        )
        for scanner in plan.scanners
    )


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item is not None and str(item))
    raise ValueError(f"expected string or list, got {type(value).__name__}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extend(target: list[str], values: tuple[str, ...]) -> None:
    target.extend(values)


def _dedupe(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))
