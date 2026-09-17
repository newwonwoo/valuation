from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

from .control_plane import ExecutionMode
from .distributional_runtime import DistributionalPrimaryValuationResult
from .orchestrator import ControlledRunResult
from .state import StateStore


def finalize_distributional_live_primary_run_artifacts(
    result: ControlledRunResult,
    *,
    state_root: str | Path,
) -> ControlledRunResult:
    """Finalize a completed distributional LIVE_PRIMARY run without fabricating
    a ``GenericValuationResult``.

    SAVE_STATE has already written the immutable valuation/audit/report bundle.
    This completion step mirrors the generic finalizer's atomic final-report and
    control-trace refresh, but keeps the typed distributional valuation as the
    authoritative result.
    """

    if result.blocked_reasons or result.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        return result
    if not result.stage_traces:
        return result
    if result.stage_traces[-1].stage != "FINAL_REPORT":
        raise ValueError(
            "completed distributional LIVE_PRIMARY run requires a terminal FINAL_REPORT trace"
        )

    valuation = result.data.get("distributional_primary_result")
    if not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError(
            "distributional completion requires DistributionalPrimaryValuationResult"
        )
    valuation.envelope.validate()

    report = result.data.get("final_report")
    if not isinstance(report, str) or not report:
        report = result.data.get("saved_report_markdown")
    if not isinstance(report, str) or not report:
        raise ValueError("completed distributional run requires a persisted final report")

    ticker = result.data.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("completed distributional run requires ticker for final persistence")

    StateStore(state_root).finalize_completed_run_artifacts(
        ticker=ticker,
        run_id=result.run_id,
        final_report=report,
        control_plane_trace=tuple(asdict(item) for item in result.stage_traces),
    )
    data = dict(result.data)
    data["saved_report_markdown"] = report
    data["final_report"] = report
    return replace(result, data=data)


__all__ = ["finalize_distributional_live_primary_run_artifacts"]
