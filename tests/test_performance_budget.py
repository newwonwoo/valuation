from valuation_engine.performance_budget import (
    RuntimePerformanceBudget,
    RuntimePerformanceMetrics,
    evaluate_runtime_budget,
)


def budget():
    return RuntimePerformanceBudget(
        max_freeze_p95_ms=2.0,
        max_workflow_p95_ms=12.0,
        max_workflow_peak_kib=2048.0,
        max_context_copy_calls=2,
    )


def test_runtime_budget_passes_at_or_below_limits():
    metrics = RuntimePerformanceMetrics(2.0, 12.0, 2048.0, 2)
    assert evaluate_runtime_budget(metrics, budget()) == ()


def test_runtime_budget_reports_each_breached_metric_with_observed_and_limit():
    metrics = RuntimePerformanceMetrics(2.1, 12.1, 2049.0, 3)
    breaches = evaluate_runtime_budget(metrics, budget())
    assert tuple(item.metric for item in breaches) == (
        "freeze_p95",
        "workflow_p95",
        "workflow_peak_memory",
        "context_copy_calls",
    )
    rendered = " | ".join(item.render() for item in breaches)
    assert "observed=" in rendered
    assert "limit=" in rendered
