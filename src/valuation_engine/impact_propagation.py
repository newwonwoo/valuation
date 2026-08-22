from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Iterable


@dataclass(frozen=True)
class ImpactEdge:
    upstream: str
    downstream: str
    relation: str


@dataclass(frozen=True)
class RevalidationRequest:
    trigger_id: str
    dirty_nodes: tuple[str, ...]
    affected_mechanisms: tuple[str, ...]
    affected_assumptions: tuple[str, ...]
    affected_company_segments: tuple[str, ...]
    requires_new_intrinsic_run: bool


class ImpactGraph:
    def __init__(self, edges: Iterable[ImpactEdge]):
        self.edges = tuple(edges)
        self._children: dict[str, list[ImpactEdge]] = defaultdict(list)
        for edge in self.edges:
            self._children[edge.upstream].append(edge)

    def descendants(self, starts: Iterable[str]) -> tuple[str, ...]:
        seen = set(starts)
        queue = deque(starts)
        out: list[str] = []
        while queue:
            node = queue.popleft()
            for edge in self._children.get(node, ()):
                if edge.downstream in seen:
                    continue
                seen.add(edge.downstream)
                out.append(edge.downstream)
                queue.append(edge.downstream)
        return tuple(out)


def build_revalidation_request(trigger_id: str, dirty_nodes: Iterable[str], graph: ImpactGraph) -> RevalidationRequest:
    starts = tuple(dict.fromkeys(dirty_nodes))
    desc = graph.descendants(starts)
    all_nodes = starts + desc
    mechanisms = tuple(sorted(n for n in all_nodes if n.startswith("mechanism:")))
    assumptions = tuple(sorted(n for n in all_nodes if n.startswith("assumption:")))
    companies = tuple(sorted(n for n in all_nodes if n.startswith("company_segment:")))
    return RevalidationRequest(
        trigger_id=trigger_id,
        dirty_nodes=starts,
        affected_mechanisms=mechanisms,
        affected_assumptions=assumptions,
        affected_company_segments=companies,
        requires_new_intrinsic_run=bool(assumptions or companies),
    )
