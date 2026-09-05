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

Writes ``{"<pr number>": [claim, …]}``. A failure to reach GitHub is reported
and yields an empty result rather than failing the build: the check this feeds
is an extra warning layer, and the local checks stand on their own.
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
    content = str((blob or {}).get("content") or "") if isinstance(blob, dict) else ""
    if not content.strip():
        return []
    payload = yaml.safe_load(base64.b64decode(content).decode("utf-8"))
    return [
        row
        for row in (payload or {}).get("claims") or ()
        if isinstance(row, dict) and str(row.get("status")) == "active"
    ]


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
        out.write_text("{}\n", encoding="utf-8")
        return 0

    collected: dict[str, list[dict[str, object]]] = {}
    try:
        pulls = _get(
            f"{_API}/repos/{args.repository}/pulls?state=open&per_page=100", token
        )
        for pull in pulls if isinstance(pulls, list) else ():
            number = int(pull.get("number") or 0)
            if not number or number == args.exclude:
                continue
            ref = (pull.get("head") or {}).get("sha") or ""
            if not ref:
                continue
            try:
                blob = _get(
                    f"{_API}/repos/{args.repository}/contents/"
                    f"{args.registry_path}?ref={ref}",
                    token,
                )
            except HTTPError as error:
                if error.code == 404:
                    continue  # that request predates the registry
                raise
            rows = active_claims_from_contents(blob)
            if rows:
                collected[str(number)] = rows
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        print(f"could not read other open requests: {error}")
        out.write_text("{}\n", encoding="utf-8")
        return 0

    out.write_text(json.dumps(collected, indent=2) + "\n", encoding="utf-8")
    print(
        f"read {len(collected)} other open pull requests carrying active claims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
