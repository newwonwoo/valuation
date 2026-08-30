"""Shared strict-parsing helpers for every LLM proposal surface.

One contract, everywhere a model output enters the engine: a single JSON
object, unknown keys rejected, typed field checks, and a bounded repair loop
that feeds the exact validation error back once before failing closed. The
LLM staff officers and the filing-locator analyst all parse through these
helpers, so tightening a rule here tightens every proposal surface at once.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


class ProposalParseError(ValueError):
    """A model response that does not satisfy a typed proposal contract."""


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalParseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProposalParseError("response must be a single JSON object")
    return payload


def require_keys(
    payload: Mapping[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    label: str,
) -> None:
    unknown = set(payload) - set(required) - set(optional)
    if unknown:
        raise ProposalParseError(
            f"{label} contains unknown keys: {', '.join(sorted(unknown))}"
        )
    missing = tuple(key for key in required if key not in payload)
    if missing:
        raise ProposalParseError(
            f"{label} is missing required keys: {', '.join(missing)}"
        )


def str_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProposalParseError(f"{label} must be a list of strings")
    return tuple(value)


def text_field(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalParseError(f"{label} must be a non-empty string")
    return value


def number_field(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProposalParseError(f"{label} must be a number")
    return float(value)


def complete_with_repair(
    *,
    transport,
    role: str,
    prompt: str,
    parse,
    max_attempts: int,
):
    """Bounded repair: the validation error goes back verbatim, once each retry."""
    attempt_prompt = prompt
    last_error: ProposalParseError | None = None
    for _ in range(max_attempts):
        response = transport.complete(role=role, prompt=attempt_prompt)
        try:
            return parse(response)
        except ProposalParseError as exc:
            last_error = exc
            attempt_prompt = (
                f"{prompt}\n\nYour previous response was rejected: {exc}\n"
                "Return a corrected JSON object."
            )
    raise ProposalParseError(
        f"{role} proposal failed after {max_attempts} attempts: {last_error}"
    )
