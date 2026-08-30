"""A live Anthropic Messages API transport for the staff seats.

This is deployment glue, deliberately OUTSIDE ``src/valuation_engine``: the
engine imports no vendor SDK and holds no model credential (the doctrine in
``llm_transport.py``). This module is what ``VALUATION_LLM_TRANSPORT`` points
at when a real model takes the staff seats:

.. code-block:: bash

   export ANTHROPIC_API_KEY=...            # never logged, never echoed
   export VALUATION_LLM_MODEL=claude-sonnet-5
   export VALUATION_LLM_TRANSPORT=anthropic_transport:build

Nothing this transport returns carries authority: the engine parses the text
into typed proposal contracts, refuses unknown evidence IDs, and re-derives
every number deterministically. The transport's only job is to complete a
prompt; it is built on stdlib ``urllib`` so the repository gains no vendor
dependency.

The HTTP call is injectable (``post=``) so the contract is testable offline;
``build()`` wires the real endpoint from the environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import ssl
from typing import Callable
import urllib.error
import urllib.request

from valuation_engine.llm_transport import TransportError

_API_VERSION = "2023-06-01"
_DEFAULT_MODEL = "claude-sonnet-5"
_CA_BUNDLE = "/root/.ccr/ca-bundle.crt"

#: The seat contract, restated at the transport layer so a live model hears it
#: even before the engine's own prompts: propose, never assert.
_SYSTEM = (
    "You are one staff seat inside a deterministic valuation engine. "
    "Respond with exactly the JSON the prompt's contract asks for - no "
    "markdown fences, no commentary. Cite only evidence IDs that the prompt "
    "itself lists; never invent identifiers, numbers, or sources. Your output "
    "is a proposal: the engine re-derives every number and refuses anything "
    "that does not verify."
)


def _default_post(url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    context = (
        ssl.create_default_context(cafile=_CA_BUNDLE)
        if os.path.exists(_CA_BUNDLE)
        else ssl.create_default_context()
    )
    request = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@dataclass
class AnthropicMessagesTransport:
    """ProposalTransport over the Anthropic Messages API (stdlib HTTP)."""

    api_key: str
    model: str = _DEFAULT_MODEL
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 4096
    post: Callable[[str, bytes, dict[str, str]], tuple[int, bytes]] = field(
        default=_default_post
    )
    #: (role, prompt-length) per call — auditable without retaining prompts.
    calls: list[tuple[str, int]] = field(default_factory=list)

    def complete(self, *, role: str, prompt: str) -> str:
        self.calls.append((role, len(prompt)))
        payload = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "system": _SYSTEM,
                "metadata": {"user_id": f"prism-staff:{role}"},
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": _API_VERSION,
        }
        status, body = self.post(
            f"{self.base_url.rstrip('/')}/v1/messages", payload, headers
        )
        if status != 200:
            # The body may name the failure class; the key must never appear.
            raise TransportError(
                f"model call for role {role!r} failed with HTTP {status}: "
                f"{body[:200].decode('utf-8', errors='replace')}"
            )
        try:
            message = json.loads(body)
            text = "".join(
                block["text"]
                for block in message["content"]
                if block.get("type") == "text"
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise TransportError(
                f"model response for role {role!r} was not a Messages payload"
            ) from exc
        if not text.strip():
            raise TransportError(f"model returned no text for role {role!r}")
        return text


def build() -> AnthropicMessagesTransport:
    """The ``VALUATION_LLM_TRANSPORT`` entry point: environment in, seat out."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise TransportError(
            "ANTHROPIC_API_KEY is not set; the live staff transport reads its "
            "credential from the environment only"
        )
    return AnthropicMessagesTransport(
        api_key=api_key,
        model=os.environ.get("VALUATION_LLM_MODEL", _DEFAULT_MODEL).strip()
        or _DEFAULT_MODEL,
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()
        or "https://api.anthropic.com",
        max_tokens=int(os.environ.get("VALUATION_LLM_MAX_TOKENS", "4096")),
    )
