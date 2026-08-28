from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

import yaml


class CalibrationHierarchyLevel(str, Enum):
    GLOBAL_EVENT = "GLOBAL_EVENT"
    ECONOMIC_ARCHETYPE = "ECONOMIC_ARCHETYPE"
    INDUSTRY_FAMILY = "INDUSTRY_FAMILY"
    SUB_INDUSTRY = "SUB_INDUSTRY"
    COMPANY_EVIDENCE_UPDATE = "COMPANY_EVIDENCE_UPDATE"


_LEVEL_ORDER = {
    level: index
    for index, level in enumerate(
        (
            CalibrationHierarchyLevel.GLOBAL_EVENT,
            CalibrationHierarchyLevel.ECONOMIC_ARCHETYPE,
            CalibrationHierarchyLevel.INDUSTRY_FAMILY,
            CalibrationHierarchyLevel.SUB_INDUSTRY,
            CalibrationHierarchyLevel.COMPANY_EVIDENCE_UPDATE,
        )
    )
}


class CalibrationHierarchyKnowledgeMode(str, Enum):
    AS_KNOWN = "AS_KNOWN"
    STATIC_TAXONOMY = "STATIC_TAXONOMY"


@dataclass(frozen=True)
class CalibrationHierarchyNode:
    node_id: str
    level: CalibrationHierarchyLevel
    label: str
    parent_id: str | None
    mapping_version: str

    def validate(self) -> None:
        if not self.node_id or not self.label or not self.mapping_version:
            raise ValueError("calibration hierarchy node requires id, label and mapping version")
        if self.level is CalibrationHierarchyLevel.GLOBAL_EVENT:
            if self.parent_id is not None:
                raise ValueError("GLOBAL_EVENT hierarchy node cannot have a parent")
        elif not self.parent_id:
            raise ValueError(f"{self.level.value} hierarchy node requires a parent")


@dataclass(frozen=True)
class CalibrationHierarchyPath:
    event_class: str
    horizon: str
    nodes: tuple[CalibrationHierarchyNode, ...]
    mapping_version: str

    @property
    def path_key(self) -> str:
        return (
            f"{self.event_class}|{self.horizon}|"
            + ">".join(item.node_id for item in self.nodes)
        )

    @property
    def terminal_node(self) -> CalibrationHierarchyNode:
        if not self.nodes:
            raise ValueError("calibration hierarchy path has no nodes")
        return self.nodes[-1]

    def validate(self) -> None:
        if not self.event_class or not self.horizon or not self.mapping_version:
            raise ValueError("calibration hierarchy path requires event class, horizon and mapping version")
        if not self.nodes:
            raise ValueError("calibration hierarchy path requires at least one node")
        if self.nodes[0].level is not CalibrationHierarchyLevel.GLOBAL_EVENT:
            raise ValueError("calibration hierarchy path must start at GLOBAL_EVENT")
        for index, node in enumerate(self.nodes):
            node.validate()
            if node.mapping_version != self.mapping_version:
                raise ValueError("hierarchy path mixes mapping versions")
            if index == 0:
                continue
            parent = self.nodes[index - 1]
            if node.parent_id != parent.node_id:
                raise ValueError(
                    f"hierarchy path parent mismatch: {node.node_id} expects "
                    f"{node.parent_id}, got {parent.node_id}"
                )
            if _LEVEL_ORDER[node.level] != _LEVEL_ORDER[parent.level] + 1:
                raise ValueError("hierarchy path levels must be adjacent and ordered")


@dataclass(frozen=True)
class CalibrationEventClassification:
    classification_id: str
    event_key: str
    company_id: str
    event_class: str
    horizon: str
    path: CalibrationHierarchyPath
    mapping_version: str
    effective_from: date
    effective_to: date | None
    first_seen_at: datetime
    knowledge_mode: CalibrationHierarchyKnowledgeMode = (
        CalibrationHierarchyKnowledgeMode.AS_KNOWN
    )

    def validate(self) -> None:
        if not all(
            (
                self.classification_id,
                self.event_key,
                self.company_id,
                self.event_class,
                self.horizon,
                self.mapping_version,
            )
        ):
            raise ValueError("calibration event classification identity is incomplete")
        if self.first_seen_at.tzinfo is None or self.first_seen_at.utcoffset() is None:
            raise ValueError("classification first_seen_at must be timezone-aware")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("classification effective_to cannot precede effective_from")
        self.path.validate()
        if self.path.event_class != self.event_class or self.path.horizon != self.horizon:
            raise ValueError("classification event class/horizon does not match hierarchy path")
        if self.path.mapping_version != self.mapping_version:
            raise ValueError("classification mapping version does not match hierarchy path")
        if (
            self.knowledge_mode is CalibrationHierarchyKnowledgeMode.AS_KNOWN
            and self.effective_from < self.first_seen_at.date()
        ):
            raise ValueError(
                "AS_KNOWN classification cannot become effective before first_seen_at; "
                "use STATIC_TAXONOMY only for predeclared static mappings"
            )

    def applies_at(self, *, effective_on: date, cutoff: datetime) -> bool:
        self.validate()
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("classification cutoff must be timezone-aware")
        if effective_on < self.effective_from:
            return False
        if self.effective_to is not None and effective_on > self.effective_to:
            return False
        if self.knowledge_mode is CalibrationHierarchyKnowledgeMode.STATIC_TAXONOMY:
            return True
        return self.first_seen_at <= cutoff


class CalibrationHierarchyRegistry:
    def __init__(
        self,
        *,
        mapping_version: str,
        nodes: Iterable[CalibrationHierarchyNode],
        event_classes: Iterable[str],
    ) -> None:
        if not mapping_version:
            raise ValueError("calibration hierarchy registry requires mapping_version")
        self.mapping_version = mapping_version
        self._nodes = tuple(nodes)
        self._event_classes = tuple(event_classes)
        self._by_id = {item.node_id: item for item in self._nodes}
        self.validate()

    @property
    def nodes(self) -> tuple[CalibrationHierarchyNode, ...]:
        return self._nodes

    @property
    def event_classes(self) -> tuple[str, ...]:
        return self._event_classes

    def get(self, node_id: str) -> CalibrationHierarchyNode:
        try:
            return self._by_id[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown calibration hierarchy node: {node_id}") from exc

    def build_path(
        self,
        *,
        event_class: str,
        horizon: str,
        terminal_node_id: str,
    ) -> CalibrationHierarchyPath:
        if event_class not in self._event_classes:
            raise ValueError(f"unregistered calibration event class: {event_class}")
        if not horizon:
            raise ValueError("hierarchy path requires horizon")
        node = self.get(terminal_node_id)
        lineage = [node]
        seen = {node.node_id}
        while node.parent_id is not None:
            node = self.get(node.parent_id)
            if node.node_id in seen:
                raise ValueError("calibration hierarchy contains a parent cycle")
            seen.add(node.node_id)
            lineage.append(node)
        path = CalibrationHierarchyPath(
            event_class=event_class,
            horizon=horizon,
            nodes=tuple(reversed(lineage)),
            mapping_version=self.mapping_version,
        )
        path.validate()
        return path

    def validate(self) -> None:
        if not self._nodes:
            raise ValueError("calibration hierarchy registry cannot be empty")
        if not self._event_classes or any(not item for item in self._event_classes):
            raise ValueError("calibration hierarchy registry requires event classes")
        if len(self._event_classes) != len(set(self._event_classes)):
            raise ValueError("calibration hierarchy registry has duplicate event classes")
        if len(self._nodes) != len(self._by_id):
            raise ValueError("calibration hierarchy registry has duplicate node IDs")
        for node in self._nodes:
            node.validate()
            if node.mapping_version != self.mapping_version:
                raise ValueError("calibration hierarchy node mapping version mismatch")
            if node.parent_id is None:
                continue
            parent = self._by_id.get(node.parent_id)
            if parent is None:
                raise ValueError(
                    f"calibration hierarchy node {node.node_id} references missing parent "
                    f"{node.parent_id}"
                )
            if _LEVEL_ORDER[node.level] != _LEVEL_ORDER[parent.level] + 1:
                raise ValueError(
                    f"calibration hierarchy node {node.node_id} must descend exactly one level"
                )
        roots = tuple(
            item
            for item in self._nodes
            if item.level is CalibrationHierarchyLevel.GLOBAL_EVENT
        )
        if len(roots) != 1:
            raise ValueError("calibration hierarchy registry requires exactly one GLOBAL_EVENT root")
        for node in self._nodes:
            visited = {node.node_id}
            current = node
            while current.parent_id is not None:
                current = self._by_id[current.parent_id]
                if current.node_id in visited:
                    raise ValueError("calibration hierarchy contains a parent cycle")
                visited.add(current.node_id)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CalibrationHierarchyRegistry":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("calibration hierarchy registry root must be a mapping")
        mapping_version = str(payload.get("mapping_version") or "")
        raw_nodes = payload.get("nodes")
        raw_events = payload.get("event_classes")
        if not isinstance(raw_nodes, list) or not isinstance(raw_events, list):
            raise ValueError("calibration hierarchy registry requires nodes and event_classes lists")
        nodes: list[CalibrationHierarchyNode] = []
        for row in raw_nodes:
            if not isinstance(row, dict):
                raise ValueError("calibration hierarchy node row must be a mapping")
            nodes.append(
                CalibrationHierarchyNode(
                    node_id=str(row.get("node_id") or ""),
                    level=CalibrationHierarchyLevel(str(row.get("level") or "")),
                    label=str(row.get("label") or ""),
                    parent_id=(
                        str(row["parent_id"])
                        if row.get("parent_id") is not None
                        else None
                    ),
                    mapping_version=str(row.get("mapping_version") or mapping_version),
                )
            )
        return cls(
            mapping_version=mapping_version,
            nodes=nodes,
            event_classes=(str(item) for item in raw_events),
        )
