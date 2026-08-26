from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .records import CriticalIssue, EvidenceRecord, HypothesisRecord


@dataclass(frozen=True)
class ResearchContext:
    company: str
    ticker: str
    prior_thesis: str
    evidence: tuple[EvidenceRecord, ...]
    hypotheses: tuple[HypothesisRecord, ...]
    round_number: int


@dataclass(frozen=True)
class ResearcherOutput:
    hypotheses: tuple[HypothesisRecord, ...]
    thesis: str
    requested_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RedTeamContext:
    company: str
    ticker: str
    evidence: tuple[EvidenceRecord, ...]
    hypotheses: tuple[HypothesisRecord, ...]
    round_number: int


@dataclass(frozen=True)
class RedTeamOutput:
    issues: tuple[CriticalIssue, ...]
    strongest_counter_thesis: str
    missing_evidence: tuple[str, ...] = ()


Researcher = Callable[[ResearchContext], ResearcherOutput]
RedTeam = Callable[[RedTeamContext], RedTeamOutput]


def run_research_loop(
    context: ResearchContext,
    researcher: Researcher,
    red_team: RedTeam,
    *,
    max_rounds: int = 3,
) -> tuple[ResearcherOutput, RedTeamOutput, int]:
    if not 1 <= max_rounds <= 3:
        raise ValueError("research loop is limited to 1..3 rounds")
    current = context
    latest_research: ResearcherOutput | None = None
    latest_redteam: RedTeamOutput | None = None
    for round_number in range(1, max_rounds + 1):
        current = ResearchContext(
            current.company, current.ticker, current.prior_thesis,
            current.evidence, current.hypotheses, round_number,
        )
        latest_research = researcher(current)
        blind_context = RedTeamContext(
            company=current.company,
            ticker=current.ticker,
            evidence=tuple(item for item in current.evidence if item.source_layer.value != "market_comparison"),
            hypotheses=latest_research.hypotheses,
            round_number=round_number,
        )
        latest_redteam = red_team(blind_context)
        unresolved = [item for item in latest_redteam.issues if item.blocking and not item.resolved]
        if not unresolved:
            return latest_research, latest_redteam, round_number
        current = ResearchContext(
            current.company, current.ticker, latest_research.thesis,
            current.evidence, latest_research.hypotheses, round_number,
        )
    assert latest_research is not None and latest_redteam is not None
    return latest_research, latest_redteam, max_rounds


def default_fixture_researcher(context: ResearchContext) -> ResearcherOutput:
    return ResearcherOutput(
        hypotheses=context.hypotheses,
        thesis=(context.prior_thesis or "OCI v1.1 regression thesis retained pending fresh primary evidence."),
        requested_evidence=("Fresh DART filing", "Latest OCI IR", "Applicable policy primary source"),
    )


def default_fixture_red_team(context: RedTeamContext) -> RedTeamOutput:
    return RedTeamOutput(
        issues=(),
        strongest_counter_thesis="Legacy fixture is reproducible but is not a live-investment conclusion.",
        missing_evidence=("Live evidence collection is intentionally outside the offline fixture.",),
    )
