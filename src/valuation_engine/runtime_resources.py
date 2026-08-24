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

    Editable/repository execution uses ``config/<filename>`` directly. Installed wheels use
    ``valuation_engine._registry_data``. ``resources.as_file`` is retained for the process
    lifetime so the same contract also works for importers that materialize package data from
    a non-filesystem container.
    """
    if not isinstance(filename, str) or not filename:
        raise ValueError("runtime registry filename is required")
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".yaml":
        raise ValueError(
            "runtime registry filename must be one YAML basename without directories"
        )

    repository_path = _REPO_ROOT / "config" / filename
    if repository_path.is_file():
        return repository_path

    resource = resources.files(_REGISTRY_RESOURCE_PACKAGE).joinpath(filename)
    if not resource.is_file():
        raise FileNotFoundError(
            f"packaged runtime registry is unavailable: {filename}"
        )
    materialized = Path(
        _RESOURCE_CONTEXTS.enter_context(resources.as_file(resource))
    )
    if not materialized.is_file():
        raise FileNotFoundError(
            f"packaged runtime registry could not be materialized: {filename}"
        )
    return materialized
