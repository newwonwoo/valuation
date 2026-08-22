#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path: str):
    with (ROOT / path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    source_registry = load("config/broker_research_source_registry.yaml")
    report_types = load("config/broker_report_type_registry.yaml")
    docs = load("data/broker_research_seed_documents.yaml")
    universe = load("config/broker_universe_watchlist.yaml")
    aliases = load("config/broker_underlying_data_aliases.yaml")
    debates = load("data/investor_debate_seed.yaml")
    alt = load("data/alternative_data_candidate_registry.yaml")

    source_ids = {s["id"] for s in source_registry["sources"]}
    report_type_ids = set(report_types["report_types"])
    errors: list[str] = []

    rows = docs["documents"] if isinstance(docs, dict) else docs
    for row in rows:
        if row["source_id"] not in source_ids:
            errors.append(f"{row['id']}: unknown source_id {row['source_id']}")
        if row["report_type"] not in report_type_ids:
            errors.append(f"{row['id']}: unknown report_type {row['report_type']}")

    seen = set()
    for section in ("korea", "global"):
        for row in universe[section]:
            key = (section, row["broker"])
            if key in seen:
                errors.append(f"duplicate broker universe entry {key}")
            seen.add(key)
            sid = row.get("canonical_source_id")
            if sid and sid not in source_ids:
                errors.append(f"{row['broker']}: canonical_source_id not in source registry: {sid}")

    alias_seen: dict[str, str] = {}
    for family, spec in aliases["families"].items():
        for alias in spec["aliases"]:
            norm = alias.strip().lower()
            prior = alias_seen.get(norm)
            if prior and prior != family:
                errors.append(f"underlying-data alias collision: {alias} -> {prior}, {family}")
            alias_seen[norm] = family

    for d in debates["debates"]:
        if not d.get("resolution_evidence"):
            errors.append(f"{d['id']}: missing resolution_evidence")
    for d in alt["datasets"]:
        for key in ("methodology", "coverage", "lag", "license"):
            if not d.get(key):
                errors.append(f"{d['id']}: missing {key}")

    if errors:
        print("\n".join(errors))
        return 1
    print(
        f"PASS sources={len(source_ids)} seed_docs={len(rows)} "
        f"universe={sum(len(universe[x]) for x in ('korea','global'))} "
        f"aliases={len(aliases['families'])} debates={len(debates['debates'])} "
        f"alt_data={len(alt['datasets'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
