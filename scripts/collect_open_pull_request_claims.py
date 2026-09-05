#!/usr/bin/env python3
"""Collect the active work claims of every other open pull request.

Each pull request's CI reads config/work_claims.yaml from its own checkout, so
a claim added by a request that has not merged yet is invisible there: two
requests can each claim the same guarded path and both pass. This gathers the
registries of the other open requests so that collision is caught while both
are still open.

It closes the window, it does not abolish it — a request opened one second
after this ran cannot be seen by it. The registry check on the default branch
remains the backstop: two active claims on one path fail there.

Writes ``{"<pr number>": [claim, …]}`` only after all pages and registries have
been read. An incomplete read fails the check; it cannot establish disjointness.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

_API = "https://api.github.com"


def _get(url: str, token: str) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "prism-work-claims/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def active_claims_from_contents(blob: object) -> list[dict[str, object]]:
    """Read the active claims out of a GitHub contents response.

    Kept separate from the request so the parsing is testable without a
    network: a check that silently reads nothing is worse than no check.
    """
    if not isinstance(blob, dict) or not isinstance(blob.get("content"), str):
        raise ValueError("registry contents response has no content")
    content = "".join(blob["content"].split())
    payload = yaml.safe_load(base64.b64decode(content, validate=True).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("registry contents must be a mapping")
    rows = payload.get("claims", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("registry claims must be a list of mappings")
    return [
        row
        for row in rows
        if str(row.get("status")) == "active"
    ]


def _collect_snapshot(repository: str, token: str, exclude: int | None,
                      registry_path: str):
    collected: dict[str, list[dict[str, object]]] = {}
    membership = {}
    page = 1
    while True:
        pulls = _get(
            f"{_API}/repos/{repository}/pulls?state=open&per_page=100&page={page}", token
        )
        if not isinstance(pulls, list):
            raise ValueError("open pull request response must be a list")
        for pull in pulls:
            if not isinstance(pull, dict):
                raise ValueError("invalid pull request response")
            number = int(pull.get("number") or 0)
            ref = (pull.get("head") or {}).get("sha")
            if number < 1 or not isinstance(ref, str) or not ref:
                raise ValueError("pull request requires number and head SHA")
            if number in membership:
                raise ValueError("pull request listing shifted during pagination")
            membership[number] = ref
            if number == exclude:
                continue
            # A 404 can mean absent content or insufficient access. Neither
            # establishes that the other request has no claims. Legacy heads
            # must incorporate the registry before this global check can pass.
            blob = _get(
                f"{_API}/repos/{repository}/contents/{registry_path}?ref={ref}", token
            )
            # A branch also contains claims copied from its base. Only its own
            # declaration represents this open request's ownership; otherwise
            # an old copy resurrects a released claim or blocks its actual owner.
            rows = [row for row in active_claims_from_contents(blob)
                    if int(row.get("pull_request") or 0) == number]
            if rows:
                collected[str(number)] = rows
        if len(pulls) < 100:
            return collected, membership
        page += 1


def collect_claims(repository: str, token: str, exclude: int | None,
                   registry_path: str) -> dict[str, list[dict[str, object]]]:
    """Require consecutive complete scans to agree on membership and heads."""
    previous = None
    for _ in range(4):
        current = _collect_snapshot(repository, token, exclude, registry_path)
        if current == previous:
            return current[0]
        previous = current
    raise ValueError("open pull request membership did not stabilize")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--exclude", type=int, help="this pull request's number, left out of the result"
    )
    parser.add_argument("--registry-path", default="config/work_claims.yaml")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = Path(args.out)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not args.repository:
        print("no GitHub token or repository; other open requests were not read")
        return 1

    try:
        collected = collect_claims(args.repository, token, args.exclude, args.registry_path)
    except (HTTPError, URLError, TimeoutError, ValueError, yaml.YAMLError) as error:
        print(f"could not read other open requests: {error}")
        return 1

    out.write_text(json.dumps(collected, indent=2) + "\n", encoding="utf-8")
    print(
        f"read {len(collected)} other open pull requests carrying active claims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
