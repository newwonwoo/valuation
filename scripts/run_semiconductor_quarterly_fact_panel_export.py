#!/usr/bin/env python3
from __future__ import annotations

import re

import export_semiconductor_quarterly_fact_panel as exporter


def period_aware_flat_column(column: object) -> str:
    if not isinstance(column, tuple):
        return str(column)
    parts = [
        str(item)
        for item in column
        if str(item) != "nan" and not str(item).startswith("Unnamed")
    ]
    for part in parts:
        if re.search(r"(?:Q[1-4]|FY)\s*20\d{2}", part):
            return part
    return parts[0] if parts else str(column[-1])


exporter.flat_column = period_aware_flat_column

if __name__ == "__main__":
    raise SystemExit(exporter.main())
