from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class MethodKind(str, Enum):
    SEGMENT_EVALUATOR = "segment_evaluator"
    CROSS_METHOD_ENGINE = "cross_method_engine"
    AGGREGATOR = "aggregator"


class ExecutionFamily(str, Enum):
    NORMALIZED_MULTIPLE = "normalized_multiple"
    EXPLICIT_FCFF_DCF = "explicit_fcff_dcf"
    FINITE_LIFE_NPV = "finite_life_npv"
    CALIBRATED_SINGLE_EVENT_RNPV = "calibrated_single_event_rnpv"
    WARRANTED_PER = "warranted_per"
    SOTP = "sotp"
    NOT_IMPLEMENTED = "not_implemented"


class MethodRuntimeStatus(str, Enum):
    RUNTIME_READY = "RUNTIME_READY"
    PARTIAL_RUNTIME = "PARTIAL_RUNTIME"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


_FAMILY_STATUS = {
    ExecutionFamily.NORMALIZED_MULTIPLE: MethodRuntimeStatus.RUNTIME_READY,
    ExecutionFamily.EXPLICIT_FCFF_DCF: MethodRuntimeStatus.PARTIAL_RUNTIME,
    ExecutionFamily.FINITE_LIFE_NPV: MethodRuntimeStatus.RUNTIME_READY,
    ExecutionFamily.CALIBRATED_SINGLE_EVENT_RNPV: MethodRuntimeStatus.PARTIAL_RUNTIME,
    ExecutionFamily.WARRANTED_PER: MethodRuntimeStatus.RUNTIME_READY,
    ExecutionFamily.SOTP: MethodRuntimeStatus.RUNTIME_READY,
    ExecutionFamily.NOT_IMPLEMENTED: MethodRuntimeStatus.NOT_IMPLEMENTED,
}


@dataclass(frozen=True)
class MethodCapability:
    method: str
    kind: MethodKind
    execution_family: ExecutionFamily
    output_kind: str
    requires_beta: bool
    requires_wacc: bool
    canonical_refs: tuple[str, ...]
    stage: str | None = None

    @property
    def runtime_status(self) -> MethodRuntimeStatus:
        return _FAMILY_STATUS[self.execution_family]

    def validate(self) -> None:
        if not self.method or not self.output_kind or not self.canonical_refs:
            raise ValueError(f"method capability {self.method!r} is incomplete")
        if self.kind is MethodKind.CROSS_METHOD_ENGINE:
            if self.stage != "HIERARCHICAL_WARRANTED_PER":
                raise ValueError(f"cross-method capability {self.method} must declare its workflow stage")
            if self.execution_family is not ExecutionFamily.WARRANTED_PER:
                raise ValueError(f"cross-method capability {self.method} has invalid execution family")
        if self.kind is MethodKind.AGGREGATOR and not self.stage:
            raise ValueError(f"aggregator capability {self.method} requires a workflow stage")
        if self.kind is MethodKind.SEGMENT_EVALUATOR and self.output_kind not in {
            "enterprise_value",
            "equity_value",
        }:
            raise ValueError(f"segment evaluator {self.method} has invalid output_kind={self.output_kind}")
        if self.requires_wacc and not self.requires_beta:
            raise ValueError(
                f"method {self.method} cannot require WACC while declaring Beta unnecessary under the current industrial risk contract"
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
    capabilities: tuple[MethodCapability, ...]

    def validate(
        self,
        *,
        archetype_registry_path: str | Path,
        repo_root: str | Path | None = None,
    ) -> None:
        if not self.capabilities:
            raise ValueError("valuation method capability registry cannot be empty")
        methods = tuple(item.method for item in self.capabilities)
        if len(methods) != len(set(methods)):
            raise ValueError("valuation method capability registry contains duplicate methods")
        for item in self.capabilities:
            item.validate()

        archetype_payload = yaml.safe_load(Path(archetype_registry_path).read_text(encoding="utf-8"))
        modules = archetype_payload.get("modules")
        if not isinstance(modules, dict) or not modules:
            raise ValueError("archetype module registry requires non-empty modules")
        exposed_methods = {
            method
            for spec in modules.values()
            if isinstance(spec, dict)
            for method in spec.get("allowed_valuation_methods", [])
        }
        configured_methods = set(methods)
        missing = tuple(sorted(exposed_methods - configured_methods))
        extra = tuple(sorted(configured_methods - exposed_methods))
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
                        for item in self.capabilities
                        for ref in item.canonical_refs
                        if not (root / ref).exists()
                    }
                )
            )
            if missing_refs:
                raise ValueError(
                    "valuation method capability registry references missing files: "
                    + ", ".join(missing_refs)
                )

        warranted = self.get("warranted_per")
        if warranted.kind is not MethodKind.CROSS_METHOD_ENGINE:
            raise ValueError("warranted_per must never be compiled as a segment ModelKey")
        sotp = self.get("sotp")
        if sotp.kind is not MethodKind.AGGREGATOR:
            raise ValueError("sotp must never be compiled as a segment ModelKey")

    def get(self, method: str) -> MethodCapability:
        for item in self.capabilities:
            if item.method == method:
                return item
        raise KeyError(method)

    def coverage_summary(self) -> MethodCoverageSummary:
        ready = tuple(
            sorted(
                item.method
                for item in self.capabilities
                if item.runtime_status is MethodRuntimeStatus.RUNTIME_READY
            )
        )
        partial = tuple(
            sorted(
                item.method
                for item in self.capabilities
                if item.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME
            )
        )
        missing = tuple(
            sorted(
                item.method
                for item in self.capabilities
                if item.runtime_status is MethodRuntimeStatus.NOT_IMPLEMENTED
            )
        )
        return MethodCoverageSummary(len(self.capabilities), ready, partial, missing)


def _bool_field(spec: dict[str, Any], key: str) -> bool:
    value = spec.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"method capability field {key} must be boolean")
    return value


def load_method_capability_registry(path: str | Path) -> MethodCapabilityRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw_methods = payload.get("methods")
    if not isinstance(raw_methods, dict) or not raw_methods:
        raise ValueError("valuation method capability registry requires non-empty methods")

    capabilities: list[MethodCapability] = []
    for method, spec in raw_methods.items():
        if not isinstance(method, str) or not method or not isinstance(spec, dict):
            raise ValueError("valuation method capability rows must be named mappings")
        refs = spec.get("canonical_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
            raise ValueError(f"method capability {method} requires canonical_refs")
        try:
            kind = MethodKind(str(spec["kind"]))
            family = ExecutionFamily(str(spec["execution_family"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid method capability type for {method}") from exc
        output_kind = str(spec.get("output_kind") or "").strip()
        stage_raw = spec.get("stage")
        stage = None if stage_raw in (None, "") else str(stage_raw)
        capabilities.append(
            MethodCapability(
                method=method,
                kind=kind,
                execution_family=family,
                output_kind=output_kind,
                requires_beta=_bool_field(spec, "requires_beta"),
                requires_wacc=_bool_field(spec, "requires_wacc"),
                canonical_refs=tuple(refs),
                stage=stage,
            )
        )
    return MethodCapabilityRegistry(tuple(capabilities))
