#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.calibration_hierarchy import CalibrationHierarchyRegistry


def main() -> int:
    registry = CalibrationHierarchyRegistry.from_yaml(
        ROOT / "config" / "calibration_hierarchy_registry.yaml"
    )
    registry.validate()
    memory = registry.build_path(
        event_class="margin_compression",
        horizon="12m",
        terminal_node_id="semiconductor_memory",
    )
    expected = (
        "global_event",
        "archetype_capacity_manufacturing",
        "industry_semiconductor",
        "semiconductor_memory",
    )
    actual = tuple(item.node_id for item in memory.nodes)
    if actual != expected:
        raise ValueError(
            f"semiconductor memory calibration path drift: expected {expected}, got {actual}"
        )
    print(
        "calibration hierarchy registry: PASS "
        f"nodes={len(registry.nodes)} events={len(registry.event_classes)} "
        f"mapping={registry.mapping_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
