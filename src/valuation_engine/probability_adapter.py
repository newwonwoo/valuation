from __future__ import annotations

from typing import Callable

from .binary_event_probability import BinaryEventProbabilityCalibrationSnapshot
from .continuous_probability_snapshot import ContinuousProbabilityCalibrationSnapshot
from .control_plane import StageStatus
from .hierarchical_calibration_certificate import HierarchicalCalibrationSnapshot
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .probability_calibration import CalibrationSnapshot
from .records import CalibrationStatus


# A frozen distribution that binds itself into the scenario set as an external
# probability source, rather than authorising an Evidence-carried probability
# assumption path. Both continuous financial-path (Route B) and binary-event
# (Route A) snapshots are of this kind.
# Each external route publishes its snapshot under its own context key, so a run
# carries exactly the artifact its route produced and no run's context grows a
# key because another route exists.
EXTERNAL_PROBABILITY_SNAPSHOT_KEYS: tuple[tuple[type, str], ...] = (
    (
        ContinuousProbabilityCalibrationSnapshot,
        "continuous_probability_calibration_snapshot",
    ),
    (
        BinaryEventProbabilityCalibrationSnapshot,
        "binary_event_probability_calibration_snapshot",
    ),
)
EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS = tuple(
    contract for contract, _ in EXTERNAL_PROBABILITY_SNAPSHOT_KEYS
)
# Every snapshot type the SCENARIO_BUILD calibration socket accepts. Adding a
# probability engine means adding its sealed snapshot type here; the socket
# itself stays route-agnostic.
CALIBRATION_SNAPSHOT_CONTRACTS = (
    CalibrationSnapshot,
    HierarchicalCalibrationSnapshot,
) + EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS

CalibrationSnapshotType = (
    CalibrationSnapshot
    | HierarchicalCalibrationSnapshot
    | ContinuousProbabilityCalibrationSnapshot
    | BinaryEventProbabilityCalibrationSnapshot
)
CalibrationSnapshotLoader = Callable[[OrchestratorContext], CalibrationSnapshotType]


def probability_calibration_load_adapter(
    *,
    loader: CalibrationSnapshotLoader,
    expected_cohort_key: str,
) -> StageAdapter:
    """Load a versioned calibration snapshot inside the canonical SCENARIO_BUILD stage.

    Single-cohort v1, hierarchical v2, binary-event v3 and continuous
    financial-path v3.2 snapshots share the same certificate boundary. Whichever
    engine produced the distribution, the socket asks the same three questions:
    is it a registered snapshot contract, does it belong to the expected cohort,
    and does it issue a certificate that passes ``validate_for_weighting``.
    Non-calibrated snapshots remain monitoring artifacts and never authorize
    intrinsic probability weighting.
    """
    if not expected_cohort_key:
        raise ValueError("expected_cohort_key is required")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            snapshot = loader(context)
            if not isinstance(snapshot, CALIBRATION_SNAPSHOT_CONTRACTS):
                raise TypeError(
                    "calibration loader must return a supported typed calibration snapshot"
                )
            if snapshot.cohort_key != expected_cohort_key:
                raise ValueError(
                    f"calibration cohort {snapshot.cohort_key} does not match {expected_cohort_key}"
                )
            if isinstance(snapshot, EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS):
                snapshot.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"probability calibration load failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs: dict[str, object] = {"probability_calibration_snapshot": snapshot}
        for contract, key in EXTERNAL_PROBABILITY_SNAPSHOT_KEYS:
            if isinstance(snapshot, contract):
                outputs[key] = snapshot
        if snapshot.status is CalibrationStatus.CALIBRATED:
            try:
                certificate = snapshot.certificate()
                certificate.validate_for_weighting()
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"calibration certificate issuance failed: {type(exc).__name__}: {exc}",
                    outputs,
                    blocking=True,
                )
            outputs["probability_calibration_certificate"] = certificate
            return StageExecutionResult(
                StageStatus.PASS,
                "calibration promotion gate passed; certificate is available for LIVE_PRIMARY probability weighting",
                outputs,
            )

        return StageExecutionResult(
            StageStatus.WARNING,
            f"calibration cohort is {snapshot.status.value}; scenario probabilities remain descriptive/unweighted",
            outputs,
        )

    return run
