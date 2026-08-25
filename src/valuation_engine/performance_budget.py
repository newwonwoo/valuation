from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimePerformanceMetrics:
    freeze_p95_ms: float
    workflow_p95_ms: float
    workflow_peak_kib: float
    context_copy_calls: int

    def __post_init__(self) -> None:
        if min(
            self.freeze_p95_ms,
            self.workflow_p95_ms,
            self.workflow_peak_kib,
            float(self.context_copy_calls),
        ) < 0:
            raise ValueError("runtime performance metrics cannot be negative")


@dataclass(frozen=True)
class RuntimePerformanceBudget:
    max_freeze_p95_ms: float
    max_workflow_p95_ms: float
    max_workflow_peak_kib: float
    max_context_copy_calls: int

    def __post_init__(self) -> None:
        if min(
            self.max_freeze_p95_ms,
            self.max_workflow_p95_ms,
            self.max_workflow_peak_kib,
            float(self.max_context_copy_calls),
        ) <= 0:
            raise ValueError("runtime performance budgets must be positive")


@dataclass(frozen=True)
class RuntimePerformanceBreach:
    metric: str
    observed: float
    limit: float
    unit: str

    def render(self) -> str:
        return (
            f"{self.metric} breached: observed={self.observed:.3f}{self.unit} "
            f"limit={self.limit:.3f}{self.unit}"
        )


def evaluate_runtime_budget(
    metrics: RuntimePerformanceMetrics,
    budget: RuntimePerformanceBudget,
) -> tuple[RuntimePerformanceBreach, ...]:
    checks = (
        (
            "freeze_p95",
            metrics.freeze_p95_ms,
            budget.max_freeze_p95_ms,
            "ms",
        ),
        (
            "workflow_p95",
            metrics.workflow_p95_ms,
            budget.max_workflow_p95_ms,
            "ms",
        ),
        (
            "workflow_peak_memory",
            metrics.workflow_peak_kib,
            budget.max_workflow_peak_kib,
            "KiB",
        ),
        (
            "context_copy_calls",
            float(metrics.context_copy_calls),
            float(budget.max_context_copy_calls),
            "calls",
        ),
    )
    return tuple(
        RuntimePerformanceBreach(metric, observed, limit, unit)
        for metric, observed, limit, unit in checks
        if observed > limit
    )
