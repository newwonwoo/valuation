from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable

import yaml


_ALLOWED_UNIT_TYPES = {
    "doctrine",
    "controller",
    "source_adapter",
    "normalizer",
    "router",
    "scanner",
    "gate",
    "llm_role",
    "bridge",
    "compiler",
    "scenario_engine",
    "risk_engine",
    "valuation_engine",
    "aggregator",
    "audit",
    "market_layer",
    "monitor",
    "learning",
    "reporter",
    "governance",
}

_ALLOWED_EFFECT_TYPES = {
    "evidence_effect",
    "hypothesis_effect",
    "routing_effect",
    "assumption_effect",
    "timing_effect",
    "probability_effect",
    "method_effect",
    "value_effect",
    "guardrail_effect",
    "reporting_effect",
}

# These are explicit workflow/terminal boundaries, not maintenance units.
_VIRTUAL_CONSUMERS = {
    "USER",
    "ECONOMIC_TWIN_SELECTION",
    "STREET_REFERENCE_LOAD",
    "MARKET_PRICE_LOAD",
}


@dataclass(frozen=True)
class UnitContract:
    unit_id: str
    unit_type: str
    implementation_status: str
    stages: tuple[str, ...]
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    consumers: tuple[str, ...]
    effect_types: tuple[str, ...]
    final_outputs: tuple[str, ...]
    canonical_refs: tuple[str, ...]
    forbidden_effects: tuple[str, ...]

    def validate(self) -> None:
        if not self.unit_id:
            raise ValueError("unit_id is required")
        if self.unit_type not in _ALLOWED_UNIT_TYPES:
            raise ValueError(f"unsupported unit_type for {self.unit_id}: {self.unit_type}")
        if not self.implementation_status:
            raise ValueError(f"implementation_status is required for {self.unit_id}")
        if not self.stages:
            raise ValueError(f"stages are required for {self.unit_id}")
        if not self.purpose:
            raise ValueError(f"purpose is required for {self.unit_id}")
        if not self.inputs:
            raise ValueError(f"inputs are required for {self.unit_id}")
        if not self.outputs:
            raise ValueError(f"outputs are required for {self.unit_id}")
        if not self.consumers:
            raise ValueError(f"consumers are required for {self.unit_id}")
        if not self.effect_types:
            raise ValueError(f"effect_types are required for {self.unit_id}")
        unknown_effects = set(self.effect_types) - _ALLOWED_EFFECT_TYPES
        if unknown_effects:
            raise ValueError(f"unknown effect_types for {self.unit_id}: {sorted(unknown_effects)}")
        if not self.final_outputs:
            raise ValueError(f"final_outputs are required for {self.unit_id}")
        if not self.canonical_refs:
            raise ValueError(f"canonical_refs are required for {self.unit_id}")


@dataclass(frozen=True)
class UnitContractRegistry:
    version: str
    units: tuple[UnitContract, ...]

    @cached_property
    def _validation_complete(self) -> bool:
        if not self.version:
            raise ValueError("registry version is required")
        seen: set[str] = set()
        for unit in self.units:
            unit.validate()
            if unit.unit_id in seen:
                raise ValueError(f"duplicate unit_id: {unit.unit_id}")
            seen.add(unit.unit_id)

        for unit in self.units:
            for consumer in unit.consumers:
                if consumer in _VIRTUAL_CONSUMERS:
                    continue
                if consumer.isupper() and consumer not in seen:
                    raise ValueError(
                        f"unknown consumer {consumer} referenced by {unit.unit_id}"
                    )
        return True

    @cached_property
    def _by_id(self) -> dict[str, UnitContract]:
        self.validate()
        return {unit.unit_id: unit for unit in self.units}

    @cached_property
    def _forward_known_index(self) -> dict[str, tuple[str, ...]]:
        self.validate()
        known = set(self._by_id)
        return {
            unit.unit_id: tuple(
                consumer for consumer in unit.consumers if consumer in known
            )
            for unit in self.units
        }

    @cached_property
    def _reverse_index(self) -> dict[str, tuple[str, ...]]:
        self.validate()
        reverse: dict[str, list[str]] = {unit.unit_id: [] for unit in self.units}
        for unit in self.units:
            for consumer in self._forward_known_index[unit.unit_id]:
                reverse[consumer].append(unit.unit_id)
        return {
            unit_id: tuple(sorted(producers))
            for unit_id, producers in reverse.items()
        }

    def validate(self) -> None:
        # Validation is immutable for a frozen registry, so run it once and cache it.
        _ = self._validation_complete

    def get(self, unit_id: str) -> UnitContract:
        self.validate()
        try:
            return self._by_id[unit_id]
        except KeyError as exc:
            raise KeyError(unit_id) from exc

    def forward_dependencies(
        self,
        unit_id: str,
        *,
        transitive: bool = False,
    ) -> tuple[str, ...]:
        self.validate()
        direct = tuple(self.get(unit_id).consumers)
        if not transitive:
            return direct
        return self._walk_index(
            unit_id,
            self._forward_known_index,
        )

    def reverse_dependencies(
        self,
        unit_id: str,
        *,
        transitive: bool = False,
    ) -> tuple[str, ...]:
        self.validate()
        self.get(unit_id)
        direct = self._reverse_index[unit_id]
        if not transitive:
            return direct
        return self._walk_index(unit_id, self._reverse_index)

    @staticmethod
    def _walk_index(
        root: str,
        index: dict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        """Traverse a possibly cyclic dependency graph without returning the root.

        Unit Contract consumers describe influence/feedback relationships, not a strict
        execution DAG, so cycles are allowed. Traversal is deterministic, finite, and each
        reachable maintenance unit is returned exactly once.
        """
        visited = {root}
        reached: set[str] = set()
        queue = list(index[root])
        cursor = 0
        while cursor < len(queue):
            current = queue[cursor]
            cursor += 1
            if current in visited:
                continue
            visited.add(current)
            reached.add(current)
            queue.extend(index[current])
        return tuple(sorted(reached))

    def units_affecting(self, effect_type: str) -> tuple[str, ...]:
        if effect_type not in _ALLOWED_EFFECT_TYPES:
            raise ValueError(f"unknown effect_type: {effect_type}")
        return tuple(
            sorted(
                unit.unit_id
                for unit in self.units
                if effect_type in unit.effect_types
            )
        )

    def producers_of_output(self, output_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(unit.unit_id for unit in self.units if output_id in unit.outputs)
        )

    def consumers_of_output(self, output_id: str) -> tuple[str, ...]:
        producers = tuple(unit for unit in self.units if output_id in unit.outputs)
        return tuple(
            sorted({consumer for unit in producers for consumer in unit.consumers})
        )


def load_unit_contract_registry(path: str | Path) -> UnitContractRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    units = tuple(
        UnitContract(
            unit_id=str(row["unit_id"]),
            unit_type=str(row["unit_type"]),
            implementation_status=str(row["implementation_status"]),
            stages=tuple(str(x) for x in row["stages"]),
            purpose=str(row["purpose"]),
            inputs=tuple(str(x) for x in row["inputs"]),
            outputs=tuple(str(x) for x in row["outputs"]),
            consumers=tuple(str(x) for x in row["consumers"]),
            effect_types=tuple(str(x) for x in row["effect_types"]),
            final_outputs=tuple(str(x) for x in row["final_outputs"]),
            canonical_refs=tuple(str(x) for x in row["canonical_refs"]),
            forbidden_effects=tuple(
                str(x) for x in row.get("forbidden_effects", ())
            ),
        )
        for row in payload["units"]
    )
    registry = UnitContractRegistry(version=str(payload["version"]), units=units)
    registry.validate()
    return registry


def audit_expected_vs_actual_impact(
    registry: UnitContractRegistry,
    *,
    unit_id: str,
    actual_effect_types: Iterable[str],
    actual_connected: bool,
) -> tuple[str, ...]:
    """Compare static design authority to one run's observed effect classes.

    Numeric materiality remains in decision_impact.py. This audit asks whether the observed
    path is contractually allowed and whether an active non-guardrail unit silently failed
    to connect downstream.
    """
    contract = registry.get(unit_id)
    actual = set(actual_effect_types)
    expected = set(contract.effect_types)
    findings: list[str] = []
    undeclared = actual - expected
    if undeclared:
        findings.append(f"UNDECLARED_EFFECT:{','.join(sorted(undeclared))}")
    if not actual_connected and "guardrail_effect" not in expected:
        findings.append("NO_ACTUAL_IMPACT_PATH")
    if actual_connected and not actual:
        findings.append("CONNECTED_WITHOUT_EFFECT_CLASS")
    return tuple(findings)
