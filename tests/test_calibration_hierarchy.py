from datetime import date, datetime, timezone

import pytest

from valuation_engine.calibration_hierarchy import (
    CalibrationEventClassification,
    CalibrationHierarchyKnowledgeMode,
    CalibrationHierarchyLevel,
    CalibrationHierarchyNode,
    CalibrationHierarchyPath,
    CalibrationHierarchyRegistry,
)


def registry() -> CalibrationHierarchyRegistry:
    return CalibrationHierarchyRegistry(
        mapping_version="map-v2",
        event_classes=("margin_compression", "capacity_ramp_delay"),
        nodes=(
            CalibrationHierarchyNode(
                "global", CalibrationHierarchyLevel.GLOBAL_EVENT, "global", None, "map-v2"
            ),
            CalibrationHierarchyNode(
                "capacity",
                CalibrationHierarchyLevel.ECONOMIC_ARCHETYPE,
                "capacity_manufacturing",
                "global",
                "map-v2",
            ),
            CalibrationHierarchyNode(
                "semi",
                CalibrationHierarchyLevel.INDUSTRY_FAMILY,
                "semiconductor",
                "capacity",
                "map-v2",
            ),
            CalibrationHierarchyNode(
                "memory",
                CalibrationHierarchyLevel.SUB_INDUSTRY,
                "memory",
                "semi",
                "map-v2",
            ),
        ),
    )


def test_registry_builds_exact_ordered_path():
    path = registry().build_path(
        event_class="margin_compression",
        horizon="12m",
        terminal_node_id="memory",
    )
    assert tuple(item.node_id for item in path.nodes) == (
        "global",
        "capacity",
        "semi",
        "memory",
    )
    assert path.path_key == "margin_compression|12m|global>capacity>semi>memory"


def test_registry_rejects_level_skips():
    with pytest.raises(ValueError, match="descend exactly one level"):
        CalibrationHierarchyRegistry(
            mapping_version="map-v2",
            event_classes=("margin_compression",),
            nodes=(
                CalibrationHierarchyNode(
                    "global",
                    CalibrationHierarchyLevel.GLOBAL_EVENT,
                    "global",
                    None,
                    "map-v2",
                ),
                CalibrationHierarchyNode(
                    "memory",
                    CalibrationHierarchyLevel.SUB_INDUSTRY,
                    "memory",
                    "global",
                    "map-v2",
                ),
            ),
        )


def test_event_classification_respects_first_seen_boundary():
    path = registry().build_path(
        event_class="margin_compression",
        horizon="12m",
        terminal_node_id="memory",
    )
    classification = CalibrationEventClassification(
        classification_id="C1",
        event_key="EVENT-1",
        company_id="KRX:000660",
        event_class="margin_compression",
        horizon="12m",
        path=path,
        mapping_version="map-v2",
        effective_from=date(2026, 1, 10),
        effective_to=None,
        first_seen_at=datetime(2026, 1, 10, 9, tzinfo=timezone.utc),
    )
    assert not classification.applies_at(
        effective_on=date(2026, 1, 10),
        cutoff=datetime(2026, 1, 10, 8, tzinfo=timezone.utc),
    )
    assert classification.applies_at(
        effective_on=date(2026, 1, 10),
        cutoff=datetime(2026, 1, 10, 10, tzinfo=timezone.utc),
    )


def test_retroactive_classification_requires_static_taxonomy_mode():
    path = registry().build_path(
        event_class="margin_compression",
        horizon="12m",
        terminal_node_id="memory",
    )
    with pytest.raises(ValueError, match="cannot become effective before first_seen_at"):
        CalibrationEventClassification(
            classification_id="C1",
            event_key="EVENT-1",
            company_id="KRX:000660",
            event_class="margin_compression",
            horizon="12m",
            path=path,
            mapping_version="map-v2",
            effective_from=date(2021, 1, 1),
            effective_to=None,
            first_seen_at=datetime(2026, 1, 10, 9, tzinfo=timezone.utc),
        ).validate()

    static = CalibrationEventClassification(
        classification_id="C2",
        event_key="EVENT-1",
        company_id="KRX:000660",
        event_class="margin_compression",
        horizon="12m",
        path=path,
        mapping_version="map-v2",
        effective_from=date(2021, 1, 1),
        effective_to=None,
        first_seen_at=datetime(2026, 1, 10, 9, tzinfo=timezone.utc),
        knowledge_mode=CalibrationHierarchyKnowledgeMode.STATIC_TAXONOMY,
    )
    static.validate()
    assert static.applies_at(
        effective_on=date(2021, 3, 31),
        cutoff=datetime(2021, 4, 1, tzinfo=timezone.utc),
    )


def test_path_rejects_parent_identity_mismatch():
    r = registry()
    path = CalibrationHierarchyPath(
        event_class="margin_compression",
        horizon="12m",
        nodes=(r.get("global"), r.get("semi")),
        mapping_version="map-v2",
    )
    with pytest.raises(ValueError, match="parent mismatch|adjacent"):
        path.validate()
