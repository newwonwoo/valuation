from __future__ import annotations

import argparse
import ast
from math import ceil
from pathlib import Path
from time import perf_counter_ns
import tracemalloc

import yaml

from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    issue_freeze_token,
)
from valuation_engine.doctrine_runtime import load_default_unit_contract_registry
from valuation_engine.orchestrator import (
    StageExecutionResult,
    load_stage_sequence,
    run_controlled_workflow,
)
from valuation_engine.performance_budget import (
    RuntimePerformanceBudget,
    RuntimePerformanceMetrics,
    evaluate_runtime_budget,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "runtime_performance_budget.yaml"


def _percentile_ms(samples_ns: list[int], percentile: float) -> float:
    if not samples_ns:
        raise ValueError("performance sample set cannot be empty")
    ordered = sorted(samples_ns)
    index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
    return ordered[index] / 1_000_000


def _context_copy_calls() -> int:
    source = (ROOT / "src" / "valuation_engine" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_controlled_workflow"
    )
    count = 0
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "dict" and node.args:
            rendered = ast.dump(node.args[0], include_attributes=False)
            if "initial_data" in rendered or "context" in rendered:
                count += 1
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {"copy", "deepcopy"}:
            rendered = ast.dump(node.func.value, include_attributes=False)
            if "context" in rendered or "initial_data" in rendered:
                count += 1
    return count


def _freeze_once():
    coverage = tuple(
        DoctrineCoverageEntry(f"PERF_{index:02d}", StageStatus.PASS, "baseline")
        for index in range(32)
    )
    expected = tuple(item.module_id for item in coverage)
    return issue_freeze_token(
        run_id="PERF-FREEZE",
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=expected,
        ledger_snapshot_hash="ledger",
        assumption_set_hash="assumptions",
        valuation_hash="valuation",
        audit_hash="audit",
        industry_snapshot_hash="industry",
        source_snapshot_hash="source",
    )


def _canonical_workflow_once(stage_sequence, registry):
    def pass_stage(_):
        return StageExecutionResult(StageStatus.PASS, "performance baseline")

    def audit_stage(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "performance audit baseline",
            {
                "audit_passed": True,
                "audit_hash": "audit",
                "decision_impact_completed": True,
            },
        )

    adapters = {
        stage: (audit_stage if stage == "AUDIT_GATE" else pass_stage)
        for stage in stage_sequence
        if stage != "INTRINSIC_VALUE_FREEZE"
    }
    result = run_controlled_workflow(
        run_id="PERF-WORKFLOW",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=stage_sequence,
        adapters=adapters,
        required_stages=stage_sequence,
        initial_data={
            "ledger_snapshot_hash": "ledger",
            "assumption_set_hash": "assumptions",
            "valuation_hash": "valuation",
            "industry_snapshot_hash": "industry",
            "source_snapshot_hash": "source",
        },
        unit_contract_registry=registry,
    )
    if not result.completed or result.freeze_token is None:
        reason = " | ".join(result.blocked_reasons) or "canonical workflow did not complete"
        raise RuntimeError(reason)
    return result


def _load_policy(path: Path):
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    fixture = payload.get("fixture", {})
    budgets = payload.get("budgets", {})
    budget = RuntimePerformanceBudget(
        max_freeze_p95_ms=float(budgets["max_freeze_p95_ms"]),
        max_workflow_p95_ms=float(budgets["max_workflow_p95_ms"]),
        max_workflow_peak_kib=float(budgets["max_workflow_peak_kib"]),
        max_context_copy_calls=int(budgets["max_context_copy_calls"]),
    )
    return fixture, budget


def measure(policy_path: Path = DEFAULT_POLICY) -> tuple[RuntimePerformanceMetrics, RuntimePerformanceBudget]:
    fixture, budget = _load_policy(policy_path)
    warmups = int(fixture["warmup_runs"])
    freeze_samples = int(fixture["freeze_samples"])
    workflow_samples = int(fixture["workflow_samples"])
    if min(warmups, freeze_samples, workflow_samples) <= 0:
        raise ValueError("runtime performance sample counts must be positive")

    stage_sequence = load_stage_sequence(ROOT / "config" / "control_plane_stage_registry.yaml")
    registry = load_default_unit_contract_registry()
    for _ in range(warmups):
        _freeze_once()
        _canonical_workflow_once(stage_sequence, registry)

    freeze_ns: list[int] = []
    for _ in range(freeze_samples):
        started = perf_counter_ns()
        _freeze_once()
        freeze_ns.append(perf_counter_ns() - started)

    workflow_ns: list[int] = []
    tracemalloc.start()
    try:
        for _ in range(workflow_samples):
            started = perf_counter_ns()
            _canonical_workflow_once(stage_sequence, registry)
            workflow_ns.append(perf_counter_ns() - started)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    metrics = RuntimePerformanceMetrics(
        freeze_p95_ms=_percentile_ms(freeze_ns, 0.95),
        workflow_p95_ms=_percentile_ms(workflow_ns, 0.95),
        workflow_peak_kib=peak_bytes / 1024,
        context_copy_calls=_context_copy_calls(),
    )
    return metrics, budget


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    metrics, budget = measure(args.policy)
    print(
        "runtime-performance: "
        f"freeze_p95={metrics.freeze_p95_ms:.3f}ms "
        f"workflow_p95={metrics.workflow_p95_ms:.3f}ms "
        f"workflow_peak={metrics.workflow_peak_kib:.1f}KiB "
        f"context_copies={metrics.context_copy_calls}"
    )
    breaches = evaluate_runtime_budget(metrics, budget)
    if breaches:
        for breach in breaches:
            print("PERF_BUDGET_BREACH: " + breach.render())
        return 0 if args.report_only else 1
    print("runtime-performance-budget: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
