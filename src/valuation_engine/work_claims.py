"""Who is already working where, so two agents collide in one file, not ten.

AGENTS.md requires parallel work to have disjoint write sets, and says overlap
must fail closed. Inside a single agent's plan that is enforceable. Between two
agents driving separate pull requests, nothing enforced it — and on 2026-09-04
the bill arrived: two independent Korea Zinc runs in the same directory, the
same segment-scoped evaluator defect fixed twice in incompatible ways, and one
classification map grown from two directions. Git merged most of the text
cleanly, which is precisely the danger: the conflict was in meaning, and it
surfaced only after both sides were finished.

This module makes that overlap visible before the work rather than after it. A
guarded area is a set of paths where a collision has actually cost something.
Changing a path inside one requires an active claim naming the pull request
that holds it. Two agents heading for the same area now meet in this registry,
in a few lines, before either writes code.

A claim is not a lock and does not reserve the repository. It says who is
already there, and it ends when its pull request does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .runtime_resources import runtime_registry_path


class WorkClaimError(ValueError):
    """Raised when the claim registry is not readable or not coherent."""


DEFAULT_WORK_CLAIM_REGISTRY = runtime_registry_path("work_claims.yaml")

#: A claim is live only while its pull request is. ``merged`` and ``released``
#: are kept rather than deleted, because the record of who built a thing is
#: worth more than a tidy file.
_ACTIVE = "active"
_STATUSES = frozenset({_ACTIVE, "merged", "released"})


#: A claim path is an exact file or a ``prefix/**`` subtree — nothing else.
#: General globs would make overlap undecidable in practice: ``runs/*/raw/**``
#: and ``runs/foo/**`` both cover ``runs/foo/raw/data.json`` while neither
#: pattern matches the other's text, so a naive comparison hands one write set
#: to two owners. Restricting the syntax makes containment a prefix question,
#: which is exact and readable by whoever has to resolve the overlap.
_SUBTREE_SUFFIX = "/**"


def _is_subtree(pattern: str) -> bool:
    return pattern.endswith(_SUBTREE_SUFFIX)


def _subtree_root(pattern: str) -> str:
    return pattern[: -len(_SUBTREE_SUFFIX)]


def validate_claim_path(pattern: str) -> None:
    """Refuse a pattern whose overlap with another cannot be decided."""
    text = str(pattern or "")
    if not text.strip():
        raise WorkClaimError("a claim path may not be blank")
    body = _subtree_root(text) if _is_subtree(text) else text
    if any(character in body for character in "*?[]"):
        raise WorkClaimError(
            f"claim path {text!r} is not decidable: use an exact path or a "
            "'prefix/**' subtree, so that two claims can be compared"
        )


def _matches(path: str, pattern: str) -> bool:
    """Does ``path`` fall inside ``pattern``?

    ``dir/**`` covers everything under ``dir`` and ``dir`` itself.
    """
    if not _is_subtree(pattern):
        return path == pattern
    root = _subtree_root(pattern)
    return path == root or path.startswith(root + "/")


def _patterns_overlap(left: str, right: str) -> bool:
    """Do two claim paths share any concrete path?

    With the syntax restricted to exact paths and subtrees, this is exactly
    "does one contain the other", which is a prefix test.
    """
    if _is_subtree(left) and _is_subtree(right):
        first, second = _subtree_root(left), _subtree_root(right)
        return (
            first == second
            or first.startswith(second + "/")
            or second.startswith(first + "/")
        )
    if _is_subtree(left):
        return _matches(right, left)
    if _is_subtree(right):
        return _matches(left, right)
    return left == right


@dataclass(frozen=True)
class GuardedArea:
    """Paths where an unclaimed change has cost something before."""

    paths: tuple[str, ...]
    reason: str

    def covers(self, path: str) -> bool:
        return any(_matches(path, pattern) for pattern in self.paths)


@dataclass(frozen=True)
class WorkClaim:
    """One pull request's declared write set."""

    claim_id: str
    owner: str
    pull_request: int
    paths: tuple[str, ...]
    status: str
    note: str = ""

    @property
    def active(self) -> bool:
        return self.status == _ACTIVE

    def covers(self, path: str) -> bool:
        return any(_matches(path, pattern) for pattern in self.paths)


@dataclass(frozen=True)
class WorkClaimRegistry:
    guarded_areas: tuple[GuardedArea, ...]
    claims: tuple[WorkClaim, ...]

    def validate(self) -> None:
        if not self.guarded_areas:
            raise WorkClaimError("the registry requires at least one guarded area")
        for area in self.guarded_areas:
            for pattern in area.paths:
                validate_claim_path(pattern)
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise WorkClaimError("claim ids must be unique")
        for claim in self.claims:
            if not claim.claim_id or not claim.owner:
                raise WorkClaimError("a claim requires claim_id and owner")
            if claim.pull_request < 1:
                raise WorkClaimError(
                    f"claim {claim.claim_id} requires its pull request number"
                )
            if claim.status not in _STATUSES:
                raise WorkClaimError(
                    f"claim {claim.claim_id} has unknown status {claim.status!r}; "
                    f"expected one of {', '.join(sorted(_STATUSES))}"
                )
            if not claim.paths or not all(claim.paths):
                raise WorkClaimError(f"claim {claim.claim_id} requires paths")
            for pattern in claim.paths:
                validate_claim_path(pattern)
        active = [claim for claim in self.claims if claim.active]
        for index, claim in enumerate(active):
            for other in active[index + 1 :]:
                if claim.pull_request == other.pull_request:
                    continue
                shared = tuple(
                    pattern
                    for pattern in claim.paths
                    if any(_patterns_overlap(pattern, item) for item in other.paths)
                )
                if shared:
                    raise WorkClaimError(
                        f"claims {claim.claim_id} (#{claim.pull_request}) and "
                        f"{other.claim_id} (#{other.pull_request}) both hold "
                        + ", ".join(shared)
                        + "; one of them has to go first"
                    )

    def guarded(self, path: str) -> GuardedArea | None:
        for area in self.guarded_areas:
            if area.covers(path):
                return area
        return None

    def holder(self, path: str) -> WorkClaim | None:
        for claim in self.claims:
            if claim.active and claim.covers(path):
                return claim
        return None


def load_work_claim_registry(
    path: str | Path = DEFAULT_WORK_CLAIM_REGISTRY,
) -> WorkClaimRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise WorkClaimError("the claim registry must be a mapping")
    areas = tuple(
        GuardedArea(
            paths=tuple(str(item) for item in (row or {}).get("paths") or ()),
            reason=str((row or {}).get("reason") or ""),
        )
        for row in payload.get("guarded_areas") or ()
    )
    claims = tuple(
        WorkClaim(
            claim_id=str((row or {}).get("claim_id") or ""),
            owner=str((row or {}).get("owner") or ""),
            pull_request=int((row or {}).get("pull_request") or 0),
            paths=tuple(str(item) for item in (row or {}).get("paths") or ()),
            status=str((row or {}).get("status") or ""),
            note=str((row or {}).get("note") or ""),
        )
        for row in payload.get("claims") or ()
    )
    registry = WorkClaimRegistry(guarded_areas=areas, claims=claims)
    registry.validate()
    return registry


@dataclass(frozen=True)
class ClaimViolation:
    """One changed path that no claim of this pull request covers."""

    path: str
    reason: str
    held_by: WorkClaim | None

    def describe(self) -> str:
        if self.held_by is not None:
            return (
                f"{self.path} is claimed by {self.held_by.claim_id} "
                f"(#{self.held_by.pull_request}, {self.held_by.owner}); "
                "coordinate with that pull request before changing it"
            )
        return (
            f"{self.path} is inside a guarded area with no claim from this "
            f"pull request — {self.reason.strip()}"
        )


@dataclass(frozen=True)
class ForeignClaim:
    """An active claim from another pull request's copy of the registry."""

    pull_request: int
    claim: WorkClaim


def check_against_open_requests(
    changed_paths: Iterable[str],
    foreign: Iterable[ForeignClaim],
    *,
    pull_request: int | None,
) -> tuple[ClaimViolation, ...]:
    """Catch two requests that opened from the same base and claimed one path.

    Each request's CI reads the registry from its own checkout, so a claim that
    another open request added is invisible there: both pass, and the collision
    only shows up when the second one merges. Feeding in the registries of the
    other open requests closes that window — as far as it can be closed, since
    a request opened one second later cannot be seen by a check that has
    already run.
    """
    violations: list[ClaimViolation] = []
    rows = tuple(foreign)
    for path in sorted(set(str(item) for item in changed_paths if str(item).strip())):
        for row in rows:
            if row.claim.pull_request != row.pull_request:
                continue  # inherited declaration, not this open request's claim
            if pull_request is not None and row.pull_request == int(pull_request):
                continue
            if not row.claim.active or not row.claim.covers(path):
                continue
            violations.append(
                ClaimViolation(
                    path=path,
                    reason=(
                        "another open pull request claims it; whichever merges "
                        "second would otherwise land on top of the first"
                    ),
                    held_by=row.claim,
                )
            )
            break
    return tuple(violations)


def check_changed_paths(
    registry: WorkClaimRegistry,
    changed_paths: Iterable[str],
    *,
    pull_request: int | None,
) -> tuple[ClaimViolation, ...]:
    """Every guarded path a change touches must be claimed by this request.

    An unguarded path needs no claim: the registry exists to stop the
    collisions that have actually happened, not to make every edit paperwork.
    """
    violations: list[ClaimViolation] = []
    for path in sorted(set(str(item) for item in changed_paths if str(item).strip())):
        area = registry.guarded(path)
        if area is None:
            continue
        holder = registry.holder(path)
        if holder is not None and pull_request is not None:
            if holder.pull_request == int(pull_request):
                continue
        violations.append(
            ClaimViolation(path=path, reason=area.reason, held_by=holder)
        )
    return tuple(violations)


def changed_paths_from_diff(output: str) -> tuple[str, ...]:
    """Read ``git diff --name-status -M`` output, both sides of a rename.

    A rename reports one status line with two paths, and only the destination
    would show in ``--name-only``. Moving a run directory or a contract file
    out of a guarded area is a change to that area — arguably the change most
    worth catching — so the source is read as well as the destination.
    """
    paths: list[str] = []
    for line in str(output or "").splitlines():
        if not line.strip():
            continue
        fields = [field for field in line.split("\t") if field.strip()]
        if len(fields) < 2:
            # ``--name-only`` output, or a status with no path: take what there is.
            paths.extend(field.strip() for field in fields)
            continue
        status = fields[0].strip().upper()
        if status.startswith(("R", "C")) and len(fields) >= 3:
            paths.append(fields[1].strip())
            paths.append(fields[2].strip())
            continue
        paths.extend(field.strip() for field in fields[1:])
    return tuple(dict.fromkeys(path for path in paths if path))


def render_registry(registry: WorkClaimRegistry) -> dict[str, Any]:
    return {
        "guarded_areas": [
            {"paths": list(area.paths), "reason": area.reason}
            for area in registry.guarded_areas
        ],
        "claims": [
            {
                "claim_id": claim.claim_id,
                "owner": claim.owner,
                "pull_request": claim.pull_request,
                "paths": list(claim.paths),
                "status": claim.status,
                "note": claim.note,
            }
            for claim in registry.claims
        ],
    }


def active_claims(registry: WorkClaimRegistry) -> tuple[WorkClaim, ...]:
    return tuple(claim for claim in registry.claims if claim.active)


def claim_for(
    registry: WorkClaimRegistry, pull_request: int
) -> tuple[WorkClaim, ...]:
    return tuple(
        claim
        for claim in registry.claims
        if claim.active and claim.pull_request == int(pull_request)
    )
