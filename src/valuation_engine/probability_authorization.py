from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProbabilityWeightingCertificate(Protocol):
    """Typed authorization contract shared by v1 and hierarchical calibration."""

    cohort_key: str
    snapshot_hash: str
    dataset_hash: str

    def validate_for_weighting(self) -> None:
        ...
