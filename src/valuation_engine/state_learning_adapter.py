from __future__ import annotations

from .ablation import AblationBatchResult
from .control_plane import StageStatus, authorize_post_freeze
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .research_learning import ResearchLearningStore


def load_research_learning_adapter(*, store: ResearchLearningStore) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        ticker = context.data.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ticker missing before research-learning state load",
                blocking=True,
            )
        try:
            history = store.load_prior_history(ticker)
            recommendations = store.load_latest_recommendations(ticker)
            count = store.record_count(ticker)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"research-learning state load failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            f"loaded {count} immutable module-impact learning record(s)",
            {
                "module_impact_prior_history": history,
                "prior_research_loadout_recommendations": recommendations,
                "research_learning_record_count": count,
            },
        )

    return run


def save_research_learning_adapter(*, store: ResearchLearningStore) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        ticker = context.data.get("ticker")
        batch = context.data.get("decision_impact_batch")
        if not isinstance(ticker, str) or not ticker:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "ticker missing before learning save", blocking=True)
        if not isinstance(batch, AblationBatchResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Decision Impact batch missing before learning save",
                blocking=True,
            )
        if context.freeze_token is None:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "research-learning state may be persisted only after Intrinsic Freeze",
                blocking=True,
            )
        try:
            authorize_post_freeze(context.freeze_token, run_id=context.run_id)
            ref = store.save_batch(ticker=ticker, run_id=context.run_id, batch=batch)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"research-learning state save failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "persisted immutable module-impact history for next-run research deployment",
            {
                "research_learning_record_path": ref.path,
                "research_learning_record_hash": ref.content_hash,
                "research_learning_recorded_at": ref.recorded_at,
            },
        )

    return run
