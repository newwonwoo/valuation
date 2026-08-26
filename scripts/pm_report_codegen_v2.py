from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/valuation_engine/generic_reporting.py",
    "from pathlib import Path\nimport shutil\n",
    "from pathlib import Path\nimport html\nimport shutil\n",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    "from .control_plane import DoctrineCoverageEntry, StageStatus, authorize_post_freeze\n",
    """from .control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    authorize_post_freeze,
)
from .execution_attestation import ExecutionAttestation
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """    coverage = data.get("doctrine_coverage", ())
    if not isinstance(valuation, GenericValuationResult):
""",
    """    coverage = data.get("doctrine_coverage", ())
    attestation = data.get("execution_attestation")
    if attestation is not None and not isinstance(attestation, ExecutionAttestation):
        raise ValueError("execution_attestation must be typed when present")
    if not isinstance(valuation, GenericValuationResult):
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """    if partial:
        lines.append(
            "- Scope: PARTIAL_INTRINSIC — 아래 숫자는 평가 완료 segment subtotal이며 전체 기업가치가 아닙니다."
        )
""",
    """    if isinstance(attestation, ExecutionAttestation):
        lines.extend(
            (
                "",
                "## Verified Execution",
                f"- Run ID: {attestation.run_id}",
                f"- Mode: {attestation.execution_mode}",
                f"- Canonical pre-save stages: {len(attestation.observed_stage_prefix)}/{len(attestation.expected_stage_prefix)}",
                f"- Execution attestation: {attestation.attestation_hash}",
            )
        )
    if partial:
        lines.append(
            "- Scope: PARTIAL_INTRINSIC — 아래 숫자는 평가 완료 segment subtotal이며 전체 기업가치가 아닙니다."
        )
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """        f"- Assumption set: {data.get('assumption_set_hash', '')}",
        f"- Valuation: {data.get('valuation_hash', '')}",
        f"- Audit: {data.get('audit_hash', '')}",
        f"- Freeze token: {getattr(data.get('intrinsic_freeze_token'), 'token_hash', '')}",
    ))
    return "\\n".join(lines) + "\\n"
""",
    '''        f"- Assumption set: {data.get('assumption_set_hash', '')}",
        f"- Scenario set: {data.get('scenario_set_hash', '')}",
        f"- Beta: {data.get('beta_snapshot_hash', '')}",
        f"- WACC: {data.get('wacc_snapshot_hash', '')}",
        f"- Capacity assessment: {data.get('capacity_commitment_assessment_hash', '')}",
        f"- Capacity audit: {data.get('capacity_audit_hash', '')}",
        f"- Valuation: {data.get('valuation_hash', '')}",
        f"- Audit: {data.get('audit_hash', '')}",
        f"- Freeze token: {getattr(data.get('intrinsic_freeze_token'), 'token_hash', '')}",
        f"- Execution attestation: {getattr(attestation, 'attestation_hash', '')}",
    ))
    return "\\n".join(lines) + "\\n"


def _markdown_body(markdown: str) -> str:
    rendered: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                rendered.append("<ul>")
                in_list = True
            rendered.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if in_list:
            rendered.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("# "):
            rendered.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            rendered.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            rendered.append(f"<h3>{html.escape(line[4:])}</h3>")
        else:
            rendered.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        rendered.append("</ul>")
    return "\\n".join(rendered)


def render_generic_report_html(data: dict[str, Any]) -> str:
    markdown = render_generic_report(data)
    body = _markdown_body(markdown)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PRISM Verified Research Report</title>
<style>
:root {{ color-scheme: light; }}
body {{ margin: 0; background: #f4f6f8; color: #17202a; font-family: Arial, 'Noto Sans KR', sans-serif; line-height: 1.65; }}
main {{ max-width: 980px; margin: 32px auto; padding: 48px 56px; background: white; border: 1px solid #dfe5eb; box-shadow: 0 8px 32px rgba(15, 23, 42, .08); }}
h1 {{ font-size: 30px; margin: 0 0 28px; border-bottom: 3px solid #17202a; padding-bottom: 14px; }}
h2 {{ font-size: 21px; margin-top: 34px; padding-bottom: 7px; border-bottom: 1px solid #dfe5eb; }}
h3 {{ font-size: 17px; margin-top: 24px; }}
ul {{ padding-left: 22px; }}
li {{ margin: 6px 0; }}
p {{ margin: 10px 0; }}
footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #dfe5eb; font-size: 12px; color: #64748b; }}
@media print {{ body {{ background: white; }} main {{ margin: 0; max-width: none; box-shadow: none; border: none; }} }}
</style>
</head>
<body>
<main>
{body}
<footer>Generated only from the immutable payload persisted by the PRISM Control Plane.</footer>
</main>
</body>
</html>
"""
''',
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """            "saved_report_markdown",
            "module_impact_summary",
            "final_report",
""",
    """            "saved_report_markdown",
            "saved_report_html",
            "module_impact_summary",
            "final_report",
            "final_report_html",
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """            if token is None or getattr(token, "run_id", None) != context.run_id:
                raise ValueError("same-run IntrinsicFreezeToken is required")
            authorize_post_freeze(token, run_id=context.run_id)

            report = render_generic_report(context.data)
""",
    """            if token is None or getattr(token, "run_id", None) != context.run_id:
                raise ValueError("same-run IntrinsicFreezeToken is required")
            authorize_post_freeze(token, run_id=context.run_id)
            attestation = context.data.get("execution_attestation")
            if context.execution_mode is ExecutionMode.LIVE_PRIMARY and not isinstance(
                attestation, ExecutionAttestation
            ):
                raise ValueError(
                    "LIVE_PRIMARY persistence requires a typed execution attestation"
                )
            if isinstance(attestation, ExecutionAttestation):
                if attestation.run_id != context.run_id:
                    raise ValueError("execution attestation run_id mismatch")
                if context.data.get("execution_attestation_hash") != attestation.attestation_hash:
                    raise ValueError("execution attestation hash mismatch")

            report = render_generic_report(context.data)
            report_html = render_generic_report_html(context.data)
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """                "freeze_token.json": _jsonable(token),
                "final_report.md": report,
            }
""",
    """                "freeze_token.json": _jsonable(token),
                "execution_attestation.json": _jsonable(attestation),
                "final_report.md": report,
                "final_report.html": report_html,
            }
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """                "audit_hash": context.data.get("audit_hash"),
                "decision_impact_hash": context.data.get("decision_impact_hash"),
""",
    """                "audit_hash": context.data.get("audit_hash"),
                "execution_attestation_hash": context.data.get(
                    "execution_attestation_hash"
                ),
                "decision_impact_hash": context.data.get("decision_impact_hash"),
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """            "saved_report_markdown": report,
            "module_impact_summary": impact_summary,
""",
    """            "saved_report_markdown": report,
            "saved_report_html": report_html,
            "module_impact_summary": impact_summary,
""",
)
replace_once(
    "src/valuation_engine/generic_reporting.py",
    """        report = context.data.get("saved_report_markdown")
        if not isinstance(report, str) or not report:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "saved report artifact is missing; SAVE_STATE must complete first",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "final report emitted from the same immutable payload saved in the run state",
            {"final_report": report},
        )
""",
    """        report = context.data.get("saved_report_markdown")
        report_html = context.data.get("saved_report_html")
        if not isinstance(report, str) or not report:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "saved report artifact is missing; SAVE_STATE must complete first",
                blocking=True,
            )
        if not isinstance(report_html, str) or not report_html:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "saved HTML report artifact is missing; SAVE_STATE must complete first",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Markdown and HTML reports emitted from the same immutable saved payload",
            {
                "final_report": report,
                "final_report_html": report_html,
            },
        )
""",
)

print("execution report codegen complete")
