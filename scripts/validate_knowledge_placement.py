from __future__ import annotations

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    foundation = yaml.safe_load((ROOT / "config/foundation_source_registry.yaml").read_text(encoding="utf-8"))
    policy = yaml.safe_load((ROOT / "config/knowledge_placement_policy.yaml").read_text(encoding="utf-8"))
    layers = set(policy["layers"])
    ids = set()
    for source in foundation["sources"]:
        sid = source["id"]
        if sid in ids:
            raise SystemExit(f"duplicate foundation source: {sid}")
        ids.add(sid)
        if source["layer"] not in layers:
            raise SystemExit(f"unknown layer for {sid}: {source['layer']}")
        if not source.get("use_for") or not source.get("never_for"):
            raise SystemExit(f"foundation source needs use_for/never_for: {sid}")
        if not source.get("url"):
            raise SystemExit(f"foundation source needs url: {sid}")
    print(f"PASS foundation_sources={len(ids)} layers={len(layers)} cross_layer_rules={len(policy.get('cross_layer_rules', []))}")


if __name__ == "__main__":
    main()
