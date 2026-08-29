"""The injected model boundary for the generic LLM staff.

A transport is the *only* part of the LLM staff that is not in this repository:
one callable that turns a prompt into text. The engine never imports a vendor
SDK and never holds a credential — the same doctrine that keeps API keys out of
the provider snapshots keeps them out of here.

Nothing a transport returns carries authority. Its text must parse into the
typed proposal contracts, every cited Evidence ID must exist in the run's
ledger, and every proposed number is re-derived by the deterministic compiler
before it can become an assumption. A transport can therefore make proposals
better or worse, but it cannot widen what proposals are allowed to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


ROLE_INTELLIGENCE = "intelligence_officer"
ROLE_RED_TEAM = "red_team_officer"
ROLE_BRIDGE = "bridge_analyst"

STAFF_ROLES = (ROLE_INTELLIGENCE, ROLE_RED_TEAM, ROLE_BRIDGE)


class TransportError(RuntimeError):
    """Raised when a transport cannot produce a response."""


@runtime_checkable
class ProposalTransport(Protocol):
    """One completion call. ``role`` names which staff seat is asking."""

    def complete(self, *, role: str, prompt: str) -> str:
        ...


@dataclass
class ScriptedTransport:
    """Deterministic transport for tests and offline runs.

    ``responses`` maps a role to the sequence of responses it will give, in
    order. Exhausting a role's script is an error, never a silent empty string:
    a test that makes more calls than it scripted has a real bug.
    """

    responses: Mapping[str, tuple[str, ...]]
    _cursor: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, *, role: str, prompt: str) -> str:
        self.calls.append((role, prompt))
        script = tuple(self.responses.get(role, ()))
        index = self._cursor.get(role, 0)
        if index >= len(script):
            raise TransportError(
                f"scripted transport has no response #{index + 1} for role {role}"
            )
        self._cursor[role] = index + 1
        return script[index]


def empty_scripted_transport() -> ScriptedTransport:
    """A transport with no scripts: every staff call fails loudly.

    Useful for dry runs that should exercise collection and fail closed at the
    first LLM seat rather than silently skipping it — and as the safe default
    when no live transport has been configured.
    """
    return ScriptedTransport({})
