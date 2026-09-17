#!/usr/bin/env python3
"""Fail-closed validator for a canonical LIVE_PRIMARY report bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.canonical_completion import (  # noqa: E402
    CompletionProofError,
    validate_completion_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument(
        "--latest-manifest",
        type=Path,
        help="v2 latest pointer to validate against the bundle",
    )
    parser.add_argument(
        "--stage-registry",
        type=Path,
        default=ROOT / "config" / "control_plane_stage_registry.yaml",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        proof = validate_completion_bundle(
            args.bundle_dir,
            stage_registry_path=args.stage_registry,
            latest_manifest_path=args.latest_manifest,
        )
    except CompletionProofError as exc:
        if args.as_json:
            print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        else:
            print(f"canonical completion: BLOCKED — {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"status": "VERIFIED", **proof.to_dict()}, ensure_ascii=False))
    else:
        print(
            "canonical completion: VERIFIED "
            f"{proof.ticker} {proof.artifact_id} "
            f"({proof.stage_count} stages)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
