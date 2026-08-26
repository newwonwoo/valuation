from __future__ import annotations

from typing import Callable

from .control_plane import StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .probability_calibration import CalibrationSnapshot
from .records import CalibrationStatus


CalibrationSnapshotLoader = Callable[[OrchestratorContext], CalibrationSnapshot]


def probability_calibration_load_adapter(
    *,
    loader: CalibrationSnapshotLoader,
    expected_cohort_key: str,
) -> StageAdapter:
    """Load a versioned calibration snapshot without creating a new canonical workflow stage.

    The adapter is intended to be chained into an existing pre-Scenario state/knowledge load.
    Non-calibrated snapshots remain useful monitoring artifacts but never emit a certificate.
    """
    if not expected_cohort_key:
        raise ValueError("expected_cohort_key is required")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            snapshot = loader(context)
            if not isinstance(snapshot, CalibrationSnapshot):
                raise TypeError("calibration loader must return CalibrationSnapshot")
            if snapshot.cohort_key != expected_cohort_key:
                raise ValueError(
                    f"calibration cohort {snapshot.cohort_key} does not match {expected_cohort_key}"
                )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"probability calibration load failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {"probability_calibration_snapshot": snapshot}
        if snapshot.status is CalibrationStatus.CALIBRATED:
            try:
                certificate = snapshot.certificate()
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
