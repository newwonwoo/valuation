from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "ops" / "project_portfolio.yaml"
MAIN_SHA = "01562387067bd997b146426185f90d6eb27bcb59"
VALUATION_RUN_ID = 32961921763
SANIL_RUN_ID = 32961921861


def _department(portfolio: dict, department_id: str) -> dict:
    for department in portfolio.get("departments", ()):  # pragma: no branch
        if department.get("id") == department_id:
            return department
    raise ValueError(f"unknown department: {department_id}")


def _append_unique(rows: list[dict], row: dict, *, key: str = "id") -> None:
    identity = row[key]
    matches = [item for item in rows if item.get(key) == identity]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}: {identity}")
    if matches:
        matches[0].clear()
        matches[0].update(row)
    else:
        rows.append(row)


def main() -> int:
    portfolio = yaml.safe_load(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    if not isinstance(portfolio, dict):
        raise ValueError("project portfolio root must be a mapping")

    portfolio["updated"] = "2026-08-26"
    portfolio["validation_baseline"] = {
        "accepted_sha": MAIN_SHA,
        "evidence": [
            {
                "kind": "github_actions",
                "workflow": "valuation-tests",
                "run_number": 503,
                "run_id": VALUATION_RUN_ID,
                "conclusion": "success",
                "event": "push",
                "head_sha": MAIN_SHA,
            },
            {
                "kind": "github_actions",
                "workflow": "sanil-live-primary",
                "run_number": 52,
                "run_id": SANIL_RUN_ID,
                "conclusion": "success",
                "event": "push",
                "head_sha": MAIN_SHA,
            },
        ],
    }

    evidence = _department(portfolio, "evidence-industry-agent")
    evidence["current_work"] = (
        "MAINTENANCE — context-strength linkage and the Sanil source-backed run are accepted; "
        "design complete"
    )
    evidence_milestones = evidence.setdefault("accepted_milestones", [])
    _append_unique(
        evidence_milestones,
        {
            "id": "EVI-CONTEXT-STRENGTH-LINKAGE-001",
            "title": (
                "Make Environmental Change–Corporate Strength Linkage a mandatory "
                "pre-valuation runtime decision"
            ),
            "points": 0,
            "status": "VERIFIED",
            "accepted_sha": MAIN_SHA,
            "validation_evidence": [
                "PR 94 merged into the integrated main runtime",
                "GHA valuation-tests run 503 PASS at exact integrated main SHA",
                "GHA sanil-live-primary run 52 PASS at exact integrated main SHA",
            ],
            "implementation_refs": [
                "PR 94 Add mandatory context-strength linkage gate",
                "PR 95 exercised the linkage decision in an actual Sanil 33-stage run",
            ],
        },
    )

    qa = _department(portfolio, "qa-release-agent")
    qa["current_work"] = (
        "ACTIVE QA-LIVE-COMPANY-FIXTURES-003 — Sanil is accepted; complete OCI, Oracle, "
        "Bloom Energy and GE Vernova real-company fixtures"
    )
    qa_milestones = qa.setdefault("accepted_milestones", [])
    _append_unique(
        qa_milestones,
        {
            "id": "QA-SANIL-LIVE-PRIMARY-001",
            "title": (
                "Execute Sanil Electric through source, Capacity, Beta/WACC, DCF, Audit, "
                "Freeze and persisted reporting"
            ),
            "points": 0,
            "status": "VERIFIED",
            "accepted_sha": MAIN_SHA,
            "validation_evidence": [
                "GHA valuation-tests run 503 PASS at exact integrated main SHA",
                "GHA sanil-live-primary run 52 PASS at exact integrated main SHA",
                "Tracked report examples/report_forms/SANIL_062040_LIVE_PRIMARY_REPORT.md",
            ],
            "implementation_refs": [
                "PR 95 Run Sanil Electric through the full LIVE_PRIMARY stack",
                "33/33 canonical stages and VERIFIED_FROZEN execution attestation",
            ],
        },
    )

    work_items = portfolio.get("work_items", [])
    portfolio["work_items"] = [
        item
        for item in work_items
        if item.get("id") != "EVI-CONTEXT-STRENGTH-LINKAGE-001"
    ]
    for item in portfolio["work_items"]:
        if item.get("id") == "QA-LIVE-COMPANY-FIXTURES-003":
            item["current_step"] = (
                "Sanil Electric pilot is accepted with an attested 33-stage report. "
                "Run OCI, Oracle, Bloom Energy and GE Vernova through the same source-backed "
                "success-or-block contract."
            )
            item.pop("github_pr", None)

    handoffs = portfolio.setdefault("accepted_handoffs", [])
    _append_unique(
        handoffs,
        {
            "id": "H-EVIDENCE-CONTEXT-STRENGTH-20260826",
            "to": "pm-integrator + evidence-industry-agent",
            "head": MAIN_SHA,
            "validation_evidence": [
                "GHA-VALUATION-TESTS-503",
                "GHA-SANIL-LIVE-PRIMARY-52",
            ],
            "closes": ["EVI-CONTEXT-STRENGTH-LINKAGE-001"],
            "residual_work": [],
        },
    )
    _append_unique(
        handoffs,
        {
            "id": "H-QA-SANIL-LIVE-PRIMARY-20260826",
            "to": "pm-integrator + qa-release-agent",
            "head": MAIN_SHA,
            "validation_evidence": [
                "GHA-VALUATION-TESTS-503",
                "GHA-SANIL-LIVE-PRIMARY-52",
            ],
            "closes": ["QA-SANIL-LIVE-PRIMARY-001"],
            "residual_work": ["QA-LIVE-COMPANY-FIXTURES-003"],
        },
    )

    PORTFOLIO_PATH.write_text(
        yaml.safe_dump(
            portfolio,
            allow_unicode=True,
            sort_keys=False,
            width=110,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
