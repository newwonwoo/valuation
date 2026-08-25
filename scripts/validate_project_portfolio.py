from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = REPO_ROOT / "ops" / "project_portfolio.yaml"
ALLOWED_STATUSES = {
    "ACTIVE",
    "READY",
    "BLOCKED",
    "BACKLOG",
    "MERGED_PENDING_ACCEPTANCE",
}


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be a mapping")
    return payload


def _detect_cycles(edges: dict[str, tuple[str, ...]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle_start = trail.index(node) if node in trail else 0
            cycle = trail[cycle_start:] + (node,)
            errors.append("dependency cycle: " + " -> ".join(cycle))
            return
        visiting.add(node)
        for dep in edges.get(node, ()):
            if dep in edges:
                visit(dep, trail + (node,))
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node, ())
    return errors


def validate_local(portfolio: dict) -> list[str]:
    errors: list[str] = []
    work_items = portfolio.get("work_items") or []
    if not isinstance(work_items, list):
        return ["work_items must be a list"]

    ids = [str(item.get("id", "")) for item in work_items]
    if any(not item_id for item_id in ids):
        errors.append("every work item requires a non-empty id")
    duplicates = [item_id for item_id, count in Counter(ids).items() if item_id and count > 1]
    if duplicates:
        errors.append("duplicate work item ids: " + ", ".join(sorted(duplicates)))

    known_ids = set(ids)
    legacy_refs = portfolio.get("legacy_dependency_refs") or []
    if not isinstance(legacy_refs, list) or not all(isinstance(item, str) and item for item in legacy_refs):
        errors.append("legacy_dependency_refs must be a list of non-empty strings")
        legacy_refs = []
    known_dependency_ids = known_ids.union(legacy_refs)

    edges: dict[str, tuple[str, ...]] = {}
    active_by_owner: Counter[str] = Counter()
    for item in work_items:
        item_id = str(item.get("id", ""))
        status = str(item.get("status", ""))
        owner = str(item.get("owner", ""))
        if status not in ALLOWED_STATUSES:
            errors.append(f"{item_id}: unsupported status {status!r}")
        if status == "ACTIVE":
            active_by_owner[owner] += 1
        pr_number = item.get("github_pr")
        if status == "MERGED_PENDING_ACCEPTANCE" and not isinstance(pr_number, int):
            errors.append(f"{item_id}: MERGED_PENDING_ACCEPTANCE requires github_pr")
        if pr_number is not None and (not isinstance(pr_number, int) or pr_number <= 0):
            errors.append(f"{item_id}: github_pr must be a positive integer")

        deps = item.get("dependencies") or []
        if not isinstance(deps, list) or not all(isinstance(dep, str) and dep for dep in deps):
            errors.append(f"{item_id}: dependencies must be a list of non-empty strings")
            continue
        if len(deps) != len(set(deps)):
            errors.append(f"{item_id}: duplicate dependencies")
        if item_id in deps:
            errors.append(f"{item_id}: self dependency")
        unknown = sorted(set(deps).difference(known_dependency_ids))
        if unknown:
            errors.append(f"{item_id}: unknown dependencies: {', '.join(unknown)}")
        edges[item_id] = tuple(dep for dep in deps if dep in known_ids)

    for owner, count in sorted(active_by_owner.items()):
        if owner and count > 1:
            errors.append(f"{owner}: more than one ACTIVE work item ({count})")

    errors.extend(_detect_cycles(edges))
    return errors


def validate_pr_snapshot(item: dict, snapshot: dict) -> list[str]:
    item_id = str(item.get("id", ""))
    status = str(item.get("status", ""))
    merged = bool(snapshot.get("merged_at"))
    state = str(snapshot.get("state", ""))
    errors: list[str] = []

    if merged and status != "MERGED_PENDING_ACCEPTANCE":
        errors.append(
            f"{item_id}: PR #{item.get('github_pr')} is merged but portfolio status is {status}"
        )
    if status == "MERGED_PENDING_ACCEPTANCE" and not merged:
        errors.append(
            f"{item_id}: portfolio says MERGED_PENDING_ACCEPTANCE but PR #{item.get('github_pr')} is not merged"
        )
    if status == "ACTIVE" and not merged and state != "open":
        errors.append(f"{item_id}: ACTIVE PR #{item.get('github_pr')} is not open")
    return errors


def _github_fetcher(repository: str, token: str) -> Callable[[int], dict]:
    def fetch(pr_number: int) -> dict:
        url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "prism-project-portfolio-validator",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"unable to read GitHub PR #{pr_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"GitHub PR #{pr_number} returned a non-object payload")
        return payload

    return fetch


def validate_github(portfolio: dict, *, repository: str, token: str) -> list[str]:
    fetch = _github_fetcher(repository, token)
    errors: list[str] = []
    for item in portfolio.get("work_items") or []:
        pr_number = item.get("github_pr")
        if not isinstance(pr_number, int):
            continue
        errors.extend(validate_pr_snapshot(item, fetch(pr_number)))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PRISM PM project portfolio")
    parser.add_argument("--portfolio", type=Path, default=DEFAULT_PORTFOLIO)
    parser.add_argument("--github", action="store_true", help="also verify linked PR state against GitHub")
    args = parser.parse_args()

    portfolio = _load_yaml(args.portfolio)
    errors = validate_local(portfolio)
    if args.github:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not repository or not token:
            errors.append("--github requires GITHUB_REPOSITORY and GITHUB_TOKEN")
        else:
            try:
                errors.extend(validate_github(portfolio, repository=repository, token=token))
            except RuntimeError as exc:
                errors.append(str(exc))

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("project portfolio integrity OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
