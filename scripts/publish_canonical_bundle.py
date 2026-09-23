#!/usr/bin/env python3
"""Publish one verified bundle with a compare-and-swap Git commit."""

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
    LATEST_MANIFEST_SCHEMA,
)
from valuation_engine.transactional_publisher import (  # noqa: E402
    AtomicPublicationError,
    publish_verified_bundle,
)


def _load_latest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AtomicPublicationError(f"latest manifest is unreadable: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != LATEST_MANIFEST_SCHEMA:
        raise AtomicPublicationError("latest manifest must use kr-live-latest-report/v2")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path, nargs="?")
    parser.add_argument("--latest-manifest", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--stage-registry",
        type=Path,
        default=ROOT / "config" / "control_plane_stage_registry.yaml",
    )
    parser.add_argument("--branch", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument(
        "--destination-prefix",
        help="repository path prefix; defaults to canonical-runs/<ticker>/<artifact_id>",
    )
    parser.add_argument(
        "--latest-destination",
        help="repository path for the atomic latest pointer",
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--commit-message", default="Publish canonical valuation run bundle")
    args = parser.parse_args()
    try:
        latest = _load_latest(args.latest_manifest)
        bundle_dir = args.bundle_dir
        if bundle_dir is None:
            relative = latest.get("bundle_directory")
            if not isinstance(relative, str) or not relative:
                raise AtomicPublicationError("latest manifest has no bundle_directory")
            bundle_dir = (args.latest_manifest.parent / relative).resolve()
        destination_prefix = args.destination_prefix or (
            f"canonical-runs/{latest['ticker']}/{latest['artifact_id']}"
        )
        latest_destination = args.latest_destination or (
            f"canonical-runs/{latest['ticker']}/LATEST_REPORT.json"
        )
        result = publish_verified_bundle(
            args.repo_root,
            bundle_dir,
            stage_registry_path=args.stage_registry,
            branch=args.branch,
            expected_head=args.expected_head,
            destination_prefix=destination_prefix,
            latest_manifest_path=args.latest_manifest,
            latest_destination=latest_destination,
            commit_message=args.commit_message,
            remote=args.remote,
            push=args.push,
        )
    except (AtomicPublicationError, CompletionProofError, OSError, KeyError) as exc:
        print(f"canonical publication: BLOCKED — {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "PUBLISHED",
                "commit_sha": result.commit_sha,
                "branch": result.branch,
                "changed_paths": result.changed_paths,
                "completion": result.completion.to_dict() if result.completion else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
