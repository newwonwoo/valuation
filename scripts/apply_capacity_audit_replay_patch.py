from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "src" / "valuation_engine" / "live_company_artifact.py",
    ROOT / "src" / "valuation_engine" / "live_company_capture.py",
)
HELPER_NAME = "_capacity_audit_replay_inputs"
HELPERS = '''\n\ndef _capacity_audit_replay_inputs(data: dict) -> tuple[tuple, tuple[str, ...]]:\n    """Return the exact Capacity guardrails consumed by canonical Generic Audit."""\n    report = data.get("capacity_audit_report")\n    audit_hash = data.get("capacity_audit_hash")\n    if report is None and audit_hash is None:\n        return (), ()\n    if not isinstance(report, AuditReport):\n        raise ValueError("capacity_audit_report must be an AuditReport for artifact replay")\n    if not isinstance(audit_hash, str) or not audit_hash:\n        raise ValueError("capacity_audit_hash is required for artifact replay")\n    return report.findings, (audit_hash,)\n'''


def _function_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _data_expression(call: ast.Call) -> str:
    keyword = next(
        (item for item in call.keywords if item.arg == "run_context_keys"),
        None,
    )
    if keyword is None:
        raise RuntimeError("audit_generic_intrinsic call has no run_context_keys")
    value = keyword.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "tuple"
        and value.args
    ):
        return ast.unparse(value.args[0])
    raise RuntimeError(
        "cannot infer run-data mapping from " + ast.unparse(value)
    )


def _ensure_audit_report_import(text: str) -> str:
    if "from .records import AuditReport" in text:
        return text
    marker = "from .records import "
    index = text.find(marker)
    if index >= 0:
        line_end = text.find("\n", index)
        line = text[index:line_end]
        if "(" not in line:
            imported = line[len(marker):]
            return (
                text[:index]
                + f"from .records import AuditReport, {imported}"
                + text[line_end:]
            )
        return text[: line_end + 1] + "    AuditReport,\n" + text[line_end + 1 :]
    future = "from __future__ import annotations\n"
    if future not in text:
        raise RuntimeError("future import marker is missing")
    return text.replace(
        future,
        future + "\nfrom .records import AuditReport\n",
        1,
    )


def _audit_calls(text: str) -> list[tuple[int, int, str]]:
    tree = ast.parse(text)
    result: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _function_name(node) != "audit_generic_intrinsic":
            continue
        if any(
            item.arg == "external_guardrail_findings"
            for item in node.keywords
        ):
            continue
        if node.end_lineno is None or node.end_col_offset is None:
            raise RuntimeError("audit call has no source position")
        result.append(
            (
                node.end_lineno,
                node.end_col_offset,
                _data_expression(node),
            )
        )
    return result


def _patch(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    calls = _audit_calls(text)
    if not calls:
        return False

    if HELPER_NAME not in text:
        text = _ensure_audit_report_import(text)
        tree = ast.parse(text)
        first_definition = min(
            (
                node.lineno
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            ),
            default=1,
        )
        lines = text.splitlines(keepends=True)
        lines.insert(first_definition - 1, HELPERS + "\n")
        text = "".join(lines)
        calls = _audit_calls(text)

    lines = text.splitlines(keepends=True)
    for end_line, end_column, data_expression in sorted(
        calls,
        reverse=True,
    ):
        line = lines[end_line - 1]
        close_index = line.rfind(")", 0, end_column)
        if close_index < 0:
            raise RuntimeError(f"{path}: closing parenthesis not found")
        indent = " " * (len(line) - len(line.lstrip()))
        insertion = (
            f"{indent}    external_guardrail_findings="
            f"{HELPER_NAME}({data_expression})[0],\n"
            f"{indent}    external_guardrail_hashes="
            f"{HELPER_NAME}({data_expression})[1],\n"
        )
        lines[end_line - 1] = (
            line[:close_index] + insertion + line[close_index:]
        )

    patched = "".join(lines)
    ast.parse(patched)
    path.write_text(patched, encoding="utf-8")
    return True


def main() -> int:
    changed = []
    for path in TARGETS:
        if path.exists() and _patch(path):
            changed.append(str(path.relative_to(ROOT)))
    if not changed:
        raise SystemExit("no live-company audit replay call required patching")
    print("patched Capacity audit replay: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
