#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.calibration_hierarchy import CalibrationHierarchyRegistry
from valuation_engine.hierarchical_calibration import load_child_specialization_policy


def main() -> int:
    registry = CalibrationHierarchyRegistry.from_yaml(
        ROOT / "config" / "calibration_hierarchy_registry.yaml"
    )
    registry.validate()
    policy = load_child_specialization_policy(
        ROOT / "config" / "hierarchical_probability_calibration_policy.yaml"
    )
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
    if policy.parent_strength_source != "training_oos_only":
        raise ValueError("hierarchical parent strength must remain training/OOS-only")
    if policy.min_resolved_events >= 200:
        raise ValueError(
            "child specialization must use partial pooling instead of copying the root 200-event gate"
        )
    print(
        "calibration hierarchy registry: PASS "
        f"nodes={len(registry.nodes)} events={len(registry.event_classes)} "
        f"mapping={registry.mapping_version} child_min={policy.min_resolved_events}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
