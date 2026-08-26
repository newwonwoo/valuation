from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "ops" / "project_portfolio.yaml"


def main() -> int:
    payload = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("project portfolio root must be a mapping")

    item = next(
        (
            row
            for row in payload.get("work_items", ())
            if row.get("id") == "EVI-CONTEXT-STRENGTH-LINKAGE-005"
        ),
        None,
    )
    if not isinstance(item, dict):
        raise RuntimeError("EVI-CONTEXT-STRENGTH-LINKAGE-005 is missing")
    if item.get("github_pr") != 94:
        raise RuntimeError("context-strength linkage work item lost PR #94 binding")
    item["status"] = "MERGED_PENDING_ACCEPTANCE"
    item["current_step"] = (
        "PR #94 is merged; retain zero completion credit until an exact-main "
        "valuation-tests run and PM/Integrator acceptance close the milestone."
    )

    department = next(
        (
            row
            for row in payload.get("departments", ())
            if row.get("id") == "evidence-industry-agent"
        ),
        None,
    )
    if not isinstance(department, dict):
        raise RuntimeError("evidence-industry-agent department is missing")
    department["current_work"] = (
        "MERGED_PENDING_ACCEPTANCE EVI-CONTEXT-STRENGTH-LINKAGE-005 — "
        "PR #94 merged; exact-main acceptance evidence pending"
    )
    payload["updated"] = "2026-08-26"

    PORTFOLIO.write_text(
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
