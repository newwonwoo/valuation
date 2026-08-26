from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CoverageEvidence:
    industry_node: str
    source_family: str
    role: str
    claim_count: int = 1
    watched: bool = False
    has_mechanism: bool = False


@dataclass(frozen=True)
class CoverageScore:
    industry_node: str
    independent_source_families: int
    roles: tuple[str, ...]
    claim_count: int
    watch_coverage: bool
    mechanism_coverage: bool
    score: int
    grade: str
    gaps: tuple[str, ...]


def score_coverage(industry_node: str, evidence: Iterable[CoverageEvidence]) -> CoverageScore:
    items = tuple(e for e in evidence if e.industry_node == industry_node)
    families = len({e.source_family for e in items})
    roles = tuple(sorted({e.role for e in items}))
    claims = sum(e.claim_count for e in items)
    watched = any(e.watched for e in items)
    mechanism = any(e.has_mechanism for e in items)
    structure_supported = "industry_structure" in roles or "mechanism_corroboration" in roles

    # This is a source-coverage heuristic, not an investment-quality score.
    # Mechanism evidence contributes family independence/structure coverage but not fake claim counts.
    points = min(families, 4) * 15
    points += 10 if "observed_state" in roles else 0
    points += 10 if structure_supported else 0
    points += 5 if "forward_hypothesis" in roles else 0
    points += 5 if "definition_standard" in roles else 0
    points += 5 if watched else 0
    points += 5 if mechanism else 0
    points = min(points, 100)

    if points >= 85:
        grade = "A"
    elif points >= 75:
        grade = "A-"
    elif points >= 65:
        grade = "B+"
    elif points >= 55:
        grade = "B"
    elif points >= 45:
        grade = "B-"
    elif points >= 35:
        grade = "C+"
    else:
        grade = "C"

    # Fail-closed grade caps: a high point total cannot hide a missing evidence dimension.
    grade_order = ["C", "C+", "B-", "B", "B+", "A-", "A"]
    def cap(current: str, maximum: str) -> str:
        return grade_order[min(grade_order.index(current), grade_order.index(maximum))]
    if families < 2:
        grade = cap(grade, "C+")
    elif "observed_state" not in roles or not structure_supported:
        grade = cap(grade, "B")
    elif not watched or not mechanism:
        grade = cap(grade, "B+")

    gaps: list[str] = []
    if families < 2:
        gaps.append("needs independent source family")
    if "observed_state" not in roles:
        gaps.append("missing observed-state source")
    if not structure_supported:
        gaps.append("missing structure/mechanism source")
    if not watched:
        gaps.append("no freshness watch")
    if not mechanism:
        gaps.append("no mechanism candidate")
    return CoverageScore(industry_node, families, roles, claims, watched, mechanism, points, grade, tuple(gaps))
