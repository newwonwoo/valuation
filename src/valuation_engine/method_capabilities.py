from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from yaml.resolver import BaseResolver


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_METHOD_CAPABILITY_PATH = (
    _REPO_ROOT / "config" / "valuation_method_capability_registry.yaml"
)
_DEFAULT_ARCHETYPE_REGISTRY_PATH = (
    _REPO_ROOT / "config" / "archetype_module_registry.yaml"
)
_REGISTRY_RESOURCE_PACKAGE = "valuation_engine._registry_data"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node,
    deep: bool = False,
):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _read_registry_text(path: Any) -> str:
    if isinstance(path, (str, Path)):
        return Path(path).read_text(encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _load_yaml_unique(path: Any) -> dict[str, Any]:
    value = yaml.load(
        _read_registry_text(path),
        Loader=_UniqueKeyLoader,
    )
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return value


def _default_registry_source(filename: str) -> Any:
    repository_path = _REPO_ROOT / "config" / filename
    if repository_path.is_file():
        return repository_path
    return resources.files(_REGISTRY_RESOURCE_PACKAGE).joinpath(filename)


def _default_repository_root() -> Path | None:
    required = (
        _DEFAULT_METHOD_CAPABILITY_PATH,
        _DEFAULT_ARCHETYPE_REGISTRY_PATH,
    )
    return _REPO_ROOT if all(path.is_file() for path in required) else None


class MethodKind(str, Enum):
    SEGMENT_EVALUATOR = "segment_evaluator"
    CROSS_METHOD_ENGINE = "cross_method_engine"
    AGGREGATOR = "aggregator"


class MethodRuntimeStatus(str, Enum):
    RUNTIME_READY = "RUNTIME_READY"
    PARTIAL_RUNTIME = "PARTIAL_RUNTIME"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class ExecutionFamilySpec:
    family: str
    kind: MethodKind
    runtime_status: MethodRuntimeStatus
    requires_beta: bool
    requires_wacc: bool
    canonical_refs: tuple[str, ...]
    stage: str | None = None

    def validate(self) -> None:
        if not self.family or not self.canonical_refs:
            raise ValueError("execution family requires name and canonical refs")
        if (
            self.kind in {MethodKind.CROSS_METHOD_ENGINE, MethodKind.AGGREGATOR}
            and not self.stage
        ):
            raise ValueError(
                f"execution family {self.family} requires a workflow stage"
            )
        if (
            self.kind is MethodKind.CROSS_METHOD_ENGINE
            and self.stage != "HIERARCHICAL_WARRANTED_PER"
        ):
            raise ValueError(
                f"cross-method family {self.family} has invalid workflow stage"
            )
        if self.requires_wacc and not self.requires_beta:
            raise ValueError(
                f"execution family {self.family} cannot require WACC while "
                "declaring Beta unnecessary under the current industrial risk contract"
            )


@dataclass(frozen=True)
class MethodCapability:
    archetype: str
    method: str
    execution_family: str
    kind: MethodKind
    runtime_status: MethodRuntimeStatus
    output_kind: str
    requires_beta: bool
    requires_wacc: bool
    canonical_refs: tuple[str, ...]
    stage: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.archetype, self.method)

    def validate(self) -> None:
        if not self.archetype or not self.method or not self.execution_family:
            raise ValueError(
                "method capability requires archetype, method and execution family"
            )
        if not self.output_kind or not self.canonical_refs:
            raise ValueError(f"method capability {self.identity!r} is incomplete")
        if (
            self.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
            and self.kind is MethodKind.SEGMENT_EVALUATOR
            and self.output_kind not in {"enterprise_value", "equity_value"}
        ):
            raise ValueError(
                f"implemented segment evaluator {self.identity!r} has invalid "
                f"output_kind={self.output_kind}"
            )
        if (
            self.kind is MethodKind.CROSS_METHOD_ENGINE
            and self.stage != "HIERARCHICAL_WARRANTED_PER"
        ):
            raise ValueError(
                f"cross-method capability {self.identity!r} has invalid workflow stage"
            )
        if self.kind is MethodKind.AGGREGATOR and not self.stage:
            raise ValueError(
                f"aggregator capability {self.identity!r} requires a workflow stage"
            )


@dataclass(frozen=True)
class MethodCoverageSummary:
    total: int
    runtime_ready: tuple[str, ...]
    partial_runtime: tuple[str, ...]
    not_implemented: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.partial_runtime and not self.not_implemented


@dataclass(frozen=True)
class MethodCapabilityRegistry:
    families: tuple[ExecutionFamilySpec, ...]
    capabilities: tuple[MethodCapability, ...]

    def validate(
        self,
        *,
        archetype_registry_path: str | Path,
        repo_root: str | Path | None = None,
    ) -> None:
        if not self.families or not self.capabilities:
            raise ValueError(
                "valuation method capability registry cannot be empty"
            )
        family_names = tuple(item.family for item in self.families)
        if len(family_names) != len(set(family_names)):
            raise ValueError(
                "valuation method capability registry contains duplicate execution families"
            )
        for item in self.families:
            item.validate()

        identities = tuple(item.identity for item in self.capabilities)
        if len(identities) != len(set(identities)):
            raise ValueError(
                "valuation method capability registry contains duplicate "
                "archetype/method bindings"
            )
        for item in self.capabilities:
            item.validate()

        archetype_payload = _load_yaml_unique(archetype_registry_path)
        modules = archetype_payload.get("modules")
        if not isinstance(modules, dict) or not modules:
            raise ValueError(
                "archetype module registry requires non-empty modules"
            )
        exposed_pairs = {
            (archetype, method)
            for archetype, spec in modules.items()
            if isinstance(spec, dict)
            for method in spec.get("allowed_valuation_methods", [])
        }
        configured_pairs = set(identities)
        missing = tuple(sorted(exposed_pairs - configured_pairs))
        extra = tuple(sorted(configured_pairs - exposed_pairs))
        if missing or extra:
            raise ValueError(
                f"valuation method capability drift: missing={missing}, extra={extra}"
            )

        if repo_root is not None:
            root = Path(repo_root)
            missing_refs = tuple(
                sorted(
                    {
                        ref
                        for family in self.families
                        for ref in family.canonical_refs
                        if not (root / ref).exists()
                    }
                )
            )
            if missing_refs:
                raise ValueError(
                    "valuation method capability registry references missing files: "
                    + ", ".join(missing_refs)
                )

        for capability in self.capabilities:
            family = self.family(capability.execution_family)
            if (
                capability.kind is not family.kind
                or capability.runtime_status is not family.runtime_status
                or capability.requires_beta != family.requires_beta
                or capability.requires_wacc != family.requires_wacc
                or capability.stage != family.stage
                or capability.canonical_refs != family.canonical_refs
            ):
                raise ValueError(
                    f"method capability {capability.identity!r} drifted from "
                    f"execution family {family.family}"
                )

        for capability in self.capabilities:
            if (
                capability.method == "warranted_per"
                and capability.kind is not MethodKind.CROSS_METHOD_ENGINE
            ):
                raise ValueError(
                    "warranted_per must never be compiled as a segment ModelKey"
                )
            if (
                capability.method == "sotp"
                and capability.kind is not MethodKind.AGGREGATOR
            ):
                raise ValueError(
                    "sotp must never be compiled as a segment ModelKey"
                )

    def family(self, family: str) -> ExecutionFamilySpec:
        for item in self.families:
            if item.family == family:
                return item
        raise KeyError(family)

    def get(self, archetype: str, method: str) -> MethodCapability:
        for item in self.capabilities:
            if item.archetype == archetype and item.method == method:
                return item
        raise KeyError((archetype, method))

    def coverage_summary(self) -> MethodCoverageSummary:
        return _coverage_summary(self.capabilities)


def _coverage_summary(
    capabilities: tuple[MethodCapability, ...],
) -> MethodCoverageSummary:
    def label(item: MethodCapability) -> str:
        return f"{item.archetype}/{item.method}"

    ready = tuple(
        sorted(
            label(item)
            for item in capabilities
            if item.runtime_status is MethodRuntimeStatus.RUNTIME_READY
        )
    )
    partial = tuple(
        sorted(
            label(item)
            for item in capabilities
            if item.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME
        )
    )
    missing = tuple(
        sorted(
            label(item)
            for item in capabilities
            if item.runtime_status is MethodRuntimeStatus.NOT_IMPLEMENTED
        )
    )
    return MethodCoverageSummary(len(capabilities), ready, partial, missing)


def _bool_field(spec: dict[str, Any], key: str) -> bool:
    value = spec.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"execution family field {key} must be boolean")
    return value


def load_method_capability_registry(
    path: str | Path,
) -> MethodCapabilityRegistry:
    payload = _load_yaml_unique(path)
    raw_families = payload.get("execution_families")
    raw_bindings = payload.get("bindings")
    if not isinstance(raw_families, dict) or not raw_families:
        raise ValueError(
            "valuation method capability registry requires execution_families"
        )
    if not isinstance(raw_bindings, dict) or not raw_bindings:
        raise ValueError("valuation method capability registry requires bindings")

    families: list[ExecutionFamilySpec] = []
    for family, spec in raw_families.items():
        if (
            not isinstance(family, str)
            or not family
            or not isinstance(spec, dict)
        ):
            raise ValueError("execution family rows must be named mappings")
        refs = spec.get("canonical_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)
        ):
            raise ValueError(f"execution family {family} requires canonical_refs")
        try:
            kind = MethodKind(str(spec["kind"]))
            status = MethodRuntimeStatus(str(spec["runtime_status"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"invalid execution family type for {family}"
            ) from exc
        stage_raw = spec.get("stage")
        families.append(
            ExecutionFamilySpec(
                family=family,
                kind=kind,
                runtime_status=status,
                requires_beta=_bool_field(spec, "requires_beta"),
                requires_wacc=_bool_field(spec, "requires_wacc"),
                canonical_refs=tuple(refs),
                stage=None if stage_raw in (None, "") else str(stage_raw),
            )
        )

    family_by_name = {item.family: item for item in families}
    capabilities: list[MethodCapability] = []
    for archetype, method_rows in raw_bindings.items():
        if (
            not isinstance(archetype, str)
            or not archetype
            or not isinstance(method_rows, dict)
        ):
            raise ValueError("method bindings must be archetype mappings")
        for method, binding in method_rows.items():
            if (
                not isinstance(method, str)
                or not method
                or not isinstance(binding, dict)
            ):
                raise ValueError(f"invalid method binding in {archetype}")
            family_name = str(binding.get("execution_family") or "")
            family = family_by_name.get(family_name)
            if family is None:
                raise ValueError(
                    f"method binding {archetype}/{method} references unknown "
                    f"execution family {family_name!r}"
                )
            output_kind = str(binding.get("output_kind") or "").strip()
            capabilities.append(
                MethodCapability(
                    archetype=archetype,
                    method=method,
                    execution_family=family.family,
                    kind=family.kind,
                    runtime_status=family.runtime_status,
                    output_kind=output_kind,
                    requires_beta=family.requires_beta,
                    requires_wacc=family.requires_wacc,
                    canonical_refs=family.canonical_refs,
                    stage=family.stage,
                )
            )
    return MethodCapabilityRegistry(tuple(families), tuple(capabilities))


def load_default_method_capability_registry() -> MethodCapabilityRegistry:
    registry = load_method_capability_registry(
        _default_registry_source("valuation_method_capability_registry.yaml")
    )
    registry.validate(
        archetype_registry_path=_default_registry_source(
            "archetype_module_registry.yaml"
        ),
        repo_root=_default_repository_root(),
    )
    return registry


def _validate_injected_registry(
    registry: MethodCapabilityRegistry,
) -> MethodCapabilityRegistry:
    """Accept only a fully validated copy of the canonical method contract.

    The injection hook exists for deterministic testing/provider composition, not for
    redefining canonical execution roles at runtime. Equality with the validated default
    registry prevents an unsupported pair or a reserved cross-method/aggregator role from
    being relabelled as a segment evaluator after startup validation.
    """
    if not isinstance(registry, MethodCapabilityRegistry):
        raise TypeError(
            "injected capability_registry must be MethodCapabilityRegistry"
        )
    registry.validate(
        archetype_registry_path=_default_registry_source(
            "archetype_module_registry.yaml"
        ),
        repo_root=_default_repository_root(),
    )
    canonical = load_default_method_capability_registry()
    if registry != canonical:
        raise ValueError(
            "injected capability_registry does not match the validated canonical registry"
        )
    return registry


def require_execution_family(
    *,
    archetype: str,
    method: str,
    expected_family: str,
    registry: MethodCapabilityRegistry | None = None,
) -> MethodCapability:
    effective = (
        load_default_method_capability_registry()
        if registry is None
        else _validate_injected_registry(registry)
    )
    try:
        capability = effective.get(archetype, method)
    except KeyError as exc:
        raise ValueError(
            f"no method capability binding for {archetype}/{method}"
        ) from exc
    if capability.execution_family != expected_family:
        raise ValueError(
            f"method {archetype}/{method} belongs to execution family "
            f"{capability.execution_family}, not {expected_family}"
        )
    if capability.kind is not MethodKind.SEGMENT_EVALUATOR:
        raise ValueError(
            f"method {archetype}/{method} is {capability.kind.value}, "
            "not a segment evaluator"
        )
    return capability
