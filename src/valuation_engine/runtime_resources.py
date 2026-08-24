from __future__ import annotations

import atexit
from contextlib import ExitStack
from functools import lru_cache
from importlib import resources
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_RESOURCE_PACKAGE = "valuation_engine._registry_data"
_RESOURCE_CONTEXTS = ExitStack()
atexit.register(_RESOURCE_CONTEXTS.close)


def _source_checkout_root() -> Path | None:
    required = (
        _REPO_ROOT / "pyproject.toml",
        _REPO_ROOT / "src" / "valuation_engine" / "runtime_resources.py",
        _REPO_ROOT / "config" / "__init__.py",
        _REPO_ROOT / "config" / "control_plane_stage_registry.yaml",
    )
    return _REPO_ROOT if all(path.is_file() for path in required) else None


@lru_cache(maxsize=None)
def runtime_registry_path(filename: str) -> Path:
    """Return a stable filesystem path for a canonical runtime registry.

    An importable installed registry package is authoritative. If that package is present but
    one YAML member is missing, execution fails closed instead of consulting an unrelated
    parent-level ``config`` directory. Repository fallback is allowed only after positively
    identifying a source checkout through independent project/source/config markers.
    ``resources.as_file`` is retained for the process lifetime so non-filesystem importers
    remain supported.
    """
    if not isinstance(filename, str) or not filename:
        raise ValueError("runtime registry filename is required")
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".yaml":
        raise ValueError(
            "runtime registry filename must be one YAML basename without directories"
        )

    try:
        package_root = resources.files(_REGISTRY_RESOURCE_PACKAGE)
    except ModuleNotFoundError:
        package_root = None

    if package_root is not None:
        resource = package_root.joinpath(filename)
        if not resource.is_file():
            raise FileNotFoundError(
                f"packaged runtime registry member is unavailable: {filename}"
            )
        materialized = Path(
            _RESOURCE_CONTEXTS.enter_context(resources.as_file(resource))
        )
        if not materialized.is_file():
            raise FileNotFoundError(
                f"packaged runtime registry could not be materialized: {filename}"
            )
        return materialized

    checkout_root = _source_checkout_root()
    if checkout_root is None:
        raise FileNotFoundError(
            f"runtime registry package is unavailable outside a verified checkout: {filename}"
        )
    repository_path = checkout_root / "config" / filename
    if not repository_path.is_file():
        raise FileNotFoundError(
            f"runtime registry is missing from verified checkout: {filename}"
        )
    return repository_path
