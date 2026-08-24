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


@lru_cache(maxsize=None)
def runtime_registry_path(filename: str) -> Path:
    """Return a stable filesystem path for a packaged runtime registry.

    Installed and editable distributions prefer ``valuation_engine._registry_data`` so an
    unrelated ``config`` directory above ``site-packages`` cannot impersonate the canonical
    registry set. A source-only checkout without an installed package mapping falls back to
    the repository ``config`` directory. ``resources.as_file`` is retained for the process
    lifetime so non-filesystem importers remain supported.
    """
    if not isinstance(filename, str) or not filename:
        raise ValueError("runtime registry filename is required")
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".yaml":
        raise ValueError(
            "runtime registry filename must be one YAML basename without directories"
        )

    try:
        resource = resources.files(_REGISTRY_RESOURCE_PACKAGE).joinpath(
            filename
        )
    except (ModuleNotFoundError, TypeError):
        resource = None
    if resource is not None and resource.is_file():
        materialized = Path(
            _RESOURCE_CONTEXTS.enter_context(resources.as_file(resource))
        )
        if not materialized.is_file():
            raise FileNotFoundError(
                f"packaged runtime registry could not be materialized: {filename}"
            )
        return materialized

    repository_path = _REPO_ROOT / "config" / filename
    if repository_path.is_file():
        return repository_path
    raise FileNotFoundError(
        f"runtime registry is unavailable from package and checkout: {filename}"
    )
