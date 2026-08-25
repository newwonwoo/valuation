from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = REPO_ROOT / "ops" / "project_portfolio.yaml"
ALLOWED_STATUSES = {"ACTIVE", "READY", "BLOCKED", "BACKLOG", "MERGED_PENDING_ACCEPTANCE"}
PR_REF = re.compile(r"\bPR\s+#?(\d+)\b", re.IGNORECASE)


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be a mapping")
    return payload


def _accepted_milestones(portfolio: dict) -> list[dict]:
    return [
        milestone
        for department in portfolio.get("departments") or []
        for milestone in (department.get("accepted_milestones") or [])
        if milestone.get("status") == "VERIFIED"
    ]


def accepted_pr_numbers(portfolio: dict) -> tuple[int, ...]:
    numbers: list[int] = []
    for milestone in _accepted_milestones(portfolio):
        values = list(milestone.get("implementation_refs") or []) + list(
            milestone.get("validation_evidence") or []
        )
        for value in values:
            numbers.extend(int(match) for match in PR_REF.findall(str(value)))
    return tuple(dict.fromkeys(numbers))


def _detect_cycles(edges: dict[str, tuple[str, ...]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in visited:
            return
        if node in visiting:
            start = trail.index(node) if node in trail else 0
            errors.append("dependency cycle: " + " -> ".join(trail[start:] + (node,)))
            return
        visiting.add(node)
        for dependency in edges.get(node, ()):
            visit(dependency, trail + (node,))
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

    work_ids = [str(item.get("id", "")) for item in work_items]
    milestone_ids = [str(item.get("id", "")) for item in _accepted_milestones(portfolio)]
    for label, ids in (("work item", work_ids), ("accepted milestone", milestone_ids)):
        if any(not item_id for item_id in ids):
            errors.append(f"every {label} requires a non-empty id")
        duplicates = sorted(item_id for item_id, count in Counter(ids).items() if item_id and count > 1)
        if duplicates:
            errors.append(f"duplicate {label} ids: {', '.join(duplicates)}")

    overlap = sorted(set(work_ids).intersection(milestone_ids))
    if overlap:
        errors.append("work items already accepted as VERIFIED: " + ", ".join(overlap))

    known_dependencies = set(work_ids).union(milestone_ids)
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
        if pr_number is not None and (not isinstance(pr_number, int) or pr_number <= 0):
            errors.append(f"{item_id}: github_pr must be a positive integer")
        if status == "MERGED_PENDING_ACCEPTANCE" and not isinstance(pr_number, int):
            errors.append(f"{item_id}: MERGED_PENDING_ACCEPTANCE requires github_pr")

        dependencies = item.get("dependencies") or []
        if not isinstance(dependencies, list) or not all(
            isinstance(dependency, str) and dependency for dependency in dependencies
        ):
            errors.append(f"{item_id}: dependencies must be a list of non-empty strings")
            continue
        if len(dependencies) != len(set(dependencies)):
            errors.append(f"{item_id}: duplicate dependencies")
        if item_id in dependencies:
            errors.append(f"{item_id}: self dependency")
        unknown = sorted(set(dependencies).difference(known_dependencies))
        if unknown:
            errors.append(f"{item_id}: unknown dependencies: {', '.join(unknown)}")
        edges[item_id] = tuple(dependency for dependency in dependencies if dependency in work_ids)

    for owner, count in sorted(active_by_owner.items()):
        if owner and count > 1:
            errors.append(f"{owner}: more than one ACTIVE work item ({count})")
    errors.extend(_detect_cycles(edges))
    return errors


def validate_work_item_pr(item: dict, snapshot: dict) -> list[str]:
    item_id = str(item.get("id", ""))
    status = str(item.get("status", ""))
    merged = bool(snapshot.get("merged_at"))
    state = str(snapshot.get("state", ""))
    pr_number = item.get("github_pr")
    errors: list[str] = []
    if merged and status != "MERGED_PENDING_ACCEPTANCE":
        errors.append(f"{item_id}: PR #{pr_number} is merged but portfolio status is {status}")
    if status == "MERGED_PENDING_ACCEPTANCE" and not merged:
        errors.append(f"{item_id}: portfolio says MERGED_PENDING_ACCEPTANCE but PR #{pr_number} is not merged")
    if status == "ACTIVE" and not merged and state != "open":
        errors.append(f"{item_id}: ACTIVE PR #{pr_number} is not open")
    return errors


def validate_accepted_pr(pr_number: int, snapshot: dict) -> list[str]:
    if snapshot.get("merged_at"):
        return []
    return [f"VERIFIED milestone references PR #{pr_number}, but that PR is not merged"]


def _fetch_pr(repository: str, token: str, pr_number: int) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/pulls/{pr_number}",
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


def validate_github(portfolio: dict, *, repository: str, token: str) -> list[str]:
    errors: list[str] = []
    cache: dict[int, dict] = {}

    def snapshot(pr_number: int) -> dict:
        if pr_number not in cache:
            cache[pr_number] = _fetch_pr(repository, token, pr_number)
        return cache[pr_number]

    for item in portfolio.get("work_items") or []:
        pr_number = item.get("github_pr")
        if isinstance(pr_number, int):
            errors.extend(validate_work_item_pr(item, snapshot(pr_number)))
    for pr_number in accepted_pr_numbers(portfolio):
        errors.extend(validate_accepted_pr(pr_number, snapshot(pr_number)))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PRISM PM project portfolio")
    parser.add_argument("--portfolio", type=Path, default=DEFAULT_PORTFOLIO)
    parser.add_argument("--github", action="store_true", help="verify linked PR state against GitHub")
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
