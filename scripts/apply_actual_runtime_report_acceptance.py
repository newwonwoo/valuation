from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = ROOT / "tests" / "test_full_live_primary_runtime.py"
MARKER = "# PRISM_ACTUAL_RUNTIME_REPORT_ACCEPTANCE"


def main() -> int:
    text = TEST_PATH.read_text(encoding="utf-8")
    if MARKER in text:
        return 0

    if "import os\n" not in text:
        lines = text.splitlines(keepends=True)
        insert_at = 0
        while insert_at < len(lines) and (
            lines[insert_at].startswith("from __future__")
            or not lines[insert_at].strip()
        ):
            insert_at += 1
        lines.insert(insert_at, "import os\n")
        text = "".join(lines)

    report_import = (
        "from valuation_engine.report_form import render_controlled_run_report\n"
    )
    if report_import not in text:
        marker = "from valuation_engine.records import (\n"
        if marker not in text:
            raise RuntimeError("records import marker not found")
        text = text.replace(marker, report_import + marker, 1)

    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    selected: tuple[ast.FunctionDef, ast.Assign, str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        source = ast.get_source_segment(text, node) or ""
        if "blocked_reasons == ()" not in source and "not result.blocked_reasons" not in source:
            continue
        if "freeze_token" not in source:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            call = child.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else None
            if name == "run_prism":
                selected = (node, child, target.id)
                break
        if selected is not None:
            break
    if selected is None:
        raise RuntimeError("successful full LIVE_PRIMARY run_prism assignment not found")

    _, assignment, variable = selected
    if assignment.end_lineno is None:
        raise RuntimeError("run_prism assignment lacks end_lineno")
    indent = " " * assignment.col_offset
    block = (
        f"{indent}{MARKER}\n"
        f"{indent}report_markdown = render_controlled_run_report({variable})\n"
        f"{indent}assert \"VERIFIED_FROZEN\" in report_markdown\n"
        f"{indent}assert \"## Stage Trace\" in report_markdown\n"
        f"{indent}assert \"Beta snapshot hash\" not in report_markdown or \"beta_snapshot_hash\" in report_markdown\n"
        f"{indent}export_path = os.environ.get(\"PRISM_REPORT_EXPORT_PATH\")\n"
        f"{indent}if export_path:\n"
        f"{indent}    target = Path(export_path)\n"
        f"{indent}    target.parent.mkdir(parents=True, exist_ok=True)\n"
        f"{indent}    target.write_text(report_markdown, encoding=\"utf-8\")\n"
    )
    lines.insert(assignment.end_lineno, block)
    TEST_PATH.write_text("".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
