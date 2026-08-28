#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.calibration_hierarchy import (
    CalibrationHierarchyLevel,
    CalibrationHierarchyRegistry,
)


def main() -> int:
    registry = CalibrationHierarchyRegistry.from_yaml(
        ROOT / "config" / "calibration_hierarchy_registry.yaml"
    )
    payload = yaml.safe_load(
        (ROOT / "config" / "semiconductor_calibration_seed.yaml").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(payload, dict):
        raise ValueError("semiconductor calibration seed root must be a mapping")
    if payload.get("status") != "MIGRATION_ONLY_NOT_PRODUCTION_CALIBRATION":
        raise ValueError("semiconductor historical seed must remain migration-only")
    if payload.get("may_authorize_production_weighting") is not False:
        raise ValueError("historical migration seed must not authorize production weighting")
    if payload.get("hierarchy_mapping_version") != registry.mapping_version:
        raise ValueError("semiconductor seed hierarchy mapping version drift")

    allowed_events = tuple(
        str(item) for item in payload.get("allowed_event_classes") or ()
    )
    unknown_events = sorted(set(allowed_events) - set(registry.event_classes))
    if unknown_events:
        raise ValueError(
            f"semiconductor seed has unknown event classes: {unknown_events}"
        )

    subindustry_nodes = {
        item.label: item.node_id
        for item in registry.nodes
        if item.level is CalibrationHierarchyLevel.SUB_INDUSTRY
        and item.parent_id == "industry_semiconductor"
    }
    companies = payload.get("companies")
    if not isinstance(companies, list) or len(companies) != 30:
        raise ValueError(
            "semiconductor migration seed must contain exactly 30 companies"
        )

    tickers: set[str] = set()
    corp_codes: set[str] = set()
    for row in companies:
        if not isinstance(row, dict):
            raise ValueError("semiconductor company row must be a mapping")
        ticker = str(row.get("ticker") or "")
        corp_code = str(row.get("corp_code") or "")
        if len(ticker) != 6 or not ticker.isdigit():
            raise ValueError(
                f"invalid Korean ticker in semiconductor seed: {ticker!r}"
            )
        if len(corp_code) != 8 or not corp_code.isdigit():
            raise ValueError(
                f"invalid OpenDART corp code in semiconductor seed: {corp_code!r}"
            )
        if ticker in tickers or corp_code in corp_codes:
            raise ValueError("semiconductor seed has duplicate ticker/corp code")
        tickers.add(ticker)
        corp_codes.add(corp_code)
        labels = tuple(str(item) for item in row.get("sub_industries") or ())
        if not labels:
            raise ValueError(
                f"semiconductor seed company {ticker} has no sub-industry"
            )
        for label in labels:
            terminal = subindustry_nodes.get(label)
            if terminal is None:
                raise ValueError(
                    f"semiconductor seed company {ticker} references unknown sub-industry {label}"
                )
            for event_class in allowed_events:
                registry.build_path(
                    event_class=event_class,
                    horizon="12m",
                    terminal_node_id=terminal,
                )

    print(
        "semiconductor calibration migration seed: PASS "
        f"companies={len(companies)} event_classes={len(allowed_events)} "
        "production_weighting=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
