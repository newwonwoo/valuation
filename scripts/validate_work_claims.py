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
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.work_claims import (  # noqa: E402
    DEFAULT_WORK_CLAIM_REGISTRY,
    ForeignClaim,
    WorkClaim,
    active_claims,
    changed_paths_from_diff,
    check_against_open_requests,
    check_changed_paths,
    claim_for,
    load_work_claim_registry,
)


def _foreign_violations(args, changed):
    """Read other open requests' registries, if CI collected them for us."""
    if not args.open_requests:
        return ()
    path = Path(args.open_requests)
    if not path.is_file():
        print(f"\nopen-request registries were not collected ({path}); "
              "a claim added in another open request cannot be seen here")
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    foreign: list[ForeignClaim] = []
    for number, rows in (payload or {}).items():
        for row in rows or ():
            foreign.append(
                ForeignClaim(
                    pull_request=int(number),
                    claim=WorkClaim(
                        claim_id=str(row.get("claim_id") or ""),
                        owner=str(row.get("owner") or ""),
                        pull_request=int(row.get("pull_request") or number),
                        paths=tuple(str(item) for item in row.get("paths") or ()),
                        status=str(row.get("status") or ""),
                    ),
                )
            )
    if foreign:
        print(f"  {len(foreign)} active claims from other open pull requests")
    return check_against_open_requests(
        changed, foreign, pull_request=args.pull_request
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_WORK_CLAIM_REGISTRY))
    parser.add_argument("--base", help="base ref to diff against, e.g. origin/main")
    parser.add_argument("--pull-request", type=int, help="this pull request's number")
    parser.add_argument(
        "--open-requests",
        help=(
            "JSON file mapping other open pull request numbers to their copy "
            "of the registry, so a claim added in a request that has not "
            "merged yet is still visible here"
        ),
    )
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

    # Two commits, not a merge base: a CI checkout is shallow, so asking for the
    # common ancestor of a base branch and a head that share no fetched history
    # fails outright. Comparing the two commits needs only both objects to be
    # present, and on a pull-request checkout the head already contains the
    # base, so the difference is the request's own changes.
    completed = subprocess.run(
        ["git", "diff", "--name-status", "-M", args.base, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr.strip() or f"git exited {completed.returncode}")
        print(f"\ncould not diff against {args.base!r}: {detail.splitlines()[0]}")
        print(
            "Fetch that commit before this check runs — a shallow checkout does "
            "not have it."
        )
        return 1
    diff = completed.stdout
    changed = changed_paths_from_diff(diff)
    violations = check_changed_paths(
        registry, changed, pull_request=args.pull_request
    )
    # A claim another open request added is not in this checkout's registry, so
    # without this its collision would only surface when the second one merges.
    violations += _foreign_violations(args, changed)

    if not violations:
        held = claim_for(registry, args.pull_request) if args.pull_request else ()
        print(
            f"{len(changed)} changed paths; guarded ones are covered"
            + (f" by {', '.join(item.claim_id for item in held)}" if held else "")
        )
        return 0

    if violations:
        print("\nunclaimed changes in guarded areas:")
        for violation in violations:
            print(f"  - {violation.describe()}")
        print(
            "\nAdd a claim to config/work_claims.yaml naming this pull request, "
            "or coordinate with the request that already holds the path."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
