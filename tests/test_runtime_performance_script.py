import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_runtime_performance_budget.py"
SPEC = importlib.util.spec_from_file_location("runtime_perf_budget_script", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _call(expression: str) -> ast.Call:
    node = ast.parse(expression, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


def test_copy_budget_detects_standard_full_context_copy_forms():
    assert MODULE._is_runtime_copy_call(_call("dict(context.data)"))
    assert MODULE._is_runtime_copy_call(_call("context.data.copy()"))
    assert MODULE._is_runtime_copy_call(_call("deepcopy(context.data)"))
    assert MODULE._is_runtime_copy_call(_call("copy.deepcopy(context.data)"))
    assert MODULE._is_runtime_copy_call(_call("copy.copy(initial_data)"))


def test_copy_budget_ignores_unrelated_small_copies():
    assert not MODULE._is_runtime_copy_call(_call("dict(local_payload)"))
    assert not MODULE._is_runtime_copy_call(_call("copy.deepcopy(local_payload)"))
