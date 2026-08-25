from __future__ import annotations

from collections.abc import Mapping as MappingABC
from types import MappingProxyType
import re
from typing import Any, Mapping

from .ledger import EvidenceLedger


_AUTHORIZATION = re.compile(
    r"(?i)\bAuthorization\b\s*[:=]\s*['\"]?(?:(?:Basic|Bearer|Token|Digest|ApiKey)\s+)?[^\s,'\";}]+['\"]?"
)
_FREE_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+")
_SENSITIVE_KV = re.compile(
    r"(?i)(['\"]?(?:api[_-]?key|crtfc[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret)['\"]?\s*[:=]\s*)(['\"]?)([^'\",\s;}]+)(['\"]?)"
)


def sanitize_runtime_text(value: object) -> str:
    text = str(value)
    text = _AUTHORIZATION.sub("Authorization: [REDACTED]", text)
    text = _FREE_BEARER.sub("Bearer [REDACTED]", text)

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2) or match.group(4)
        return f"{prefix}{quote}[REDACTED]{quote}"

    return _SENSITIVE_KV.sub(replace, text)


def read_only_data_view(data: dict[str, Any]) -> Mapping[str, Any]:
    """Return a top-level read-only view with mutable built-ins isolated per stage.

    Custom typed runtime objects stay shared so adapters keep their exact contracts. Mutable
    builtin containers are recursively copied, preventing an adapter from modifying canonical
    upstream state through an alias hidden inside the read-only top-level mapping.
    """
    isolated = {key: _isolate_builtin(value) for key, value in data.items()}
    return MappingProxyType(isolated)


def evidence_ledgers(data: Mapping[str, Any]) -> tuple[EvidenceLedger, ...]:
    seen_objects: set[int] = set()
    seen_ledgers: set[int] = set()
    ledgers: list[EvidenceLedger] = []

    def visit(value: Any) -> None:
        identity = id(value)
        if identity in seen_objects:
            return
        seen_objects.add(identity)
        if isinstance(value, EvidenceLedger):
            if identity not in seen_ledgers:
                seen_ledgers.add(identity)
                ledgers.append(value)
            return
        if isinstance(value, MappingABC):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)

    visit(data)
    return tuple(ledgers)


def mutable_guard_snapshot(data: Mapping[str, Any]) -> dict[str, object]:
    """Capture values whose in-place mutation could bypass append-only output checks."""
    result: dict[str, object] = {}
    for key, value in data.items():
        if _needs_guard(value):
            result[str(key)] = _guard_component(value)
    return result


def mutated_guard_keys(
    before: Mapping[str, object],
    data: Mapping[str, Any],
) -> tuple[str, ...]:
    changed: list[str] = []
    for key, token in before.items():
        if key not in data:
            changed.append(key)
            continue
        if _guard_component(data[key]) != token:
            changed.append(key)
    return tuple(sorted(changed))


def _isolate_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _isolate_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_isolate_builtin(item) for item in value]
    if isinstance(value, set):
        return {_isolate_builtin(item) for item in value}
    if isinstance(value, bytearray):
        return bytearray(value)
    if isinstance(value, tuple):
        return tuple(_isolate_builtin(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_isolate_builtin(item) for item in value)
    return value


def _needs_guard(value: Any) -> bool:
    return isinstance(value, (dict, list, set, bytearray, EvidenceLedger)) or hasattr(
        value, "mutation_version"
    )


def _guard_component(value: Any) -> object:
    if isinstance(value, EvidenceLedger):
        return (
            "EvidenceLedger",
            value.mutation_version,
            tuple(repr(item) for item in value.records()),
        )
    if isinstance(value, dict):
        return (
            "dict",
            tuple(
                sorted(
                    (
                        repr(key),
                        _guard_component(item) if _needs_guard(item) else repr(item),
                    )
                    for key, item in value.items()
                )
            ),
        )
    if isinstance(value, list):
        return (
            "list",
            tuple(
                _guard_component(item) if _needs_guard(item) else repr(item)
                for item in value
            ),
        )
    if isinstance(value, set):
        return (
            "set",
            tuple(
                sorted(
                    repr(_guard_component(item) if _needs_guard(item) else item)
                    for item in value
                )
            ),
        )
    if isinstance(value, bytearray):
        return ("bytearray", bytes(value))
    if hasattr(value, "mutation_version"):
        return (
            type(value).__module__,
            type(value).__qualname__,
            getattr(value, "mutation_version"),
            repr(value),
        )
    return repr(value)
