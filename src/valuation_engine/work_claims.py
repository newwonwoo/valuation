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
from fnmatch import fnmatch
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


def _matches(path: str, pattern: str) -> bool:
    """Glob match where ``dir/**`` also covers ``dir`` itself."""
    if fnmatch(path, pattern):
        return True
    if pattern.endswith("/**"):
        return path == pattern[:-3] or fnmatch(path, pattern[:-3] + "/*")
    return False


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
        active = [claim for claim in self.claims if claim.active]
        for index, claim in enumerate(active):
            for other in active[index + 1 :]:
                if claim.pull_request == other.pull_request:
                    continue
                shared = tuple(
                    pattern
                    for pattern in claim.paths
                    if any(_matches(pattern, item) for item in other.paths)
                    or any(_matches(item, pattern) for item in other.paths)
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
    """Read ``git diff --name-only`` output, ignoring blank lines."""
    return tuple(
        line.strip() for line in str(output or "").splitlines() if line.strip()
    )


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
