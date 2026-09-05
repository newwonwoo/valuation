#!/usr/bin/env python3
"""Check the work-claim registry, and that a change stays inside its claims.

    PYTHONPATH=src python scripts/validate_work_claims.py
    PYTHONPATH=src python scripts/validate_work_claims.py \
        --base origin/main --pull-request 171

With no arguments it checks the registry itself: unique ids, real pull request
numbers, known statuses, and no two active claims holding the same paths.

With ``--base`` it also checks this change. Every changed path inside a guarded
area must be covered by an active claim belonging to this pull request. Paths
outside the guarded areas need no claim — the registry exists to stop the
collisions that have actually cost something, not to make every edit paperwork.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.work_claims import (  # noqa: E402
    DEFAULT_WORK_CLAIM_REGISTRY,
    active_claims,
    changed_paths_from_diff,
    check_changed_paths,
    claim_for,
    load_work_claim_registry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_WORK_CLAIM_REGISTRY))
    parser.add_argument("--base", help="base ref to diff against, e.g. origin/main")
    parser.add_argument("--pull-request", type=int, help="this pull request's number")
    args = parser.parse_args()

    registry = load_work_claim_registry(args.registry)
    live = active_claims(registry)
    print(
        f"work claims: {len(registry.guarded_areas)} guarded areas, "
        f"{len(live)} active of {len(registry.claims)} claims"
    )
    for claim in live:
        print(f"  #{claim.pull_request} {claim.claim_id} ({claim.owner})")

    if not args.base:
        return 0

    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    changed = changed_paths_from_diff(diff)
    violations = check_changed_paths(
        registry, changed, pull_request=args.pull_request
    )
    if not violations:
        held = claim_for(registry, args.pull_request) if args.pull_request else ()
        print(
            f"{len(changed)} changed paths; guarded ones are covered"
            + (f" by {', '.join(item.claim_id for item in held)}" if held else "")
        )
        return 0

    print("\nunclaimed changes in guarded areas:")
    for violation in violations:
        print(f"  - {violation.describe()}")
    print(
        "\nAdd a claim to config/work_claims.yaml naming this pull request, or "
        "coordinate with the request that already holds the path."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
