from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "ops" / "project_portfolio.yaml"
ACCEPTED_SHA = "f2e961bd7fe6e65125e06246070109ee0cc5d358"
VALIDATION = [
    "GHA valuation-tests run 701 PASS at exact main SHA",
    "GHA sanil-live-primary run 113 PASS at exact main SHA",
    "GHA skhynix-live-primary run 89 PASS at exact main SHA",
]


def milestone(
    milestone_id: str,
    title: str,
    implementation_refs: list[str],
    *,
    validation_evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": milestone_id,
        "title": title,
        "points": 0,
        "status": "VERIFIED",
        "accepted_sha": ACCEPTED_SHA,
        "validation_evidence": validation_evidence or VALIDATION,
        "implementation_refs": implementation_refs,
    }


def add_milestone(department: dict, row: dict) -> None:
    rows = department.setdefault("accepted_milestones", [])
    if not any(item.get("id") == row["id"] for item in rows):
        rows.append(row)


def add_handoff(portfolio: dict, row: dict) -> None:
    rows = portfolio.setdefault("accepted_handoffs", [])
    if not any(item.get("id") == row["id"] for item in rows):
        rows.insert(0, row)


def main() -> None:
    portfolio = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8"))
    if not isinstance(portfolio, dict):
        raise SystemExit("portfolio root must be a mapping")

    portfolio["updated"] = "2026-09-01"
    portfolio["validation_baseline"] = {
        "accepted_sha": ACCEPTED_SHA,
        "evidence": [
            {
                "kind": "github_actions",
                "workflow": "valuation-tests",
                "run_number": 701,
                "run_id": 33411388645,
                "conclusion": "success",
                "event": "push",
                "head_sha": ACCEPTED_SHA,
            },
            {
                "kind": "github_actions",
                "workflow": "sanil-live-primary",
                "run_number": 113,
                "run_id": 33411388647,
                "conclusion": "success",
                "event": "push",
                "head_sha": ACCEPTED_SHA,
            },
            {
                "kind": "github_actions",
                "workflow": "skhynix-live-primary",
                "run_number": 89,
                "run_id": 33411388682,
                "conclusion": "success",
                "event": "push",
                "head_sha": ACCEPTED_SHA,
            },
        ],
    }

    departments = {row["id"]: row for row in portfolio["departments"]}
    departments["pm-integrator"]["current_work"] = (
        "MAINTENANCE — natural-language routing, PRISM MCP, KR live-run/SOTP and runtime hardening are accepted; "
        "only repository-admin branch protection and real production-history accumulation remain"
    )
    departments["evidence-industry-agent"]["current_work"] = (
        "MAINTENANCE — source chronology and Sanil dependency coverage are accepted; design complete"
    )
    departments["runtime-safety-agent"]["current_work"] = (
        "MAINTENANCE — natural-language/MCP authority and native-Linux tunnel state authorization are accepted; "
        "main branch protection remains repository-admin configuration"
    )
    departments["valuation-engine-agent"]["current_work"] = (
        "MAINTENANCE — exact evaluator coverage and evidence-bound multi-segment KR SOTP are accepted"
    )
    departments["calibration-risk-agent"]["current_work"] = (
        "BLOCKED CAL-PRODUCTION-COHORT-003 — append-only production capture is accepted; "
        "future real outcomes must accumulate to declared cohort thresholds"
    )
    departments["qa-release-agent"]["current_work"] = (
        "MAINTENANCE — exact-main valuation-tests, Sanil and SK hynix acceptance workflows pass at the current runtime baseline"
    )

    add_milestone(
        departments["runtime-safety-agent"],
        milestone(
            "RUN-PRISM-INTENT-MCP-20260901",
            "Route natural-language stock analysis through the single PRISM MCP gateway and strict attested runtime",
            [
                "PR 145 natural-language intent gateway",
                "PR 146 strict PRISM_ANALYZE MCP gateway",
                "PR 147 canonical no-preselection live routing and managed tunnel launcher",
            ],
        ),
    )
    add_milestone(
        departments["runtime-safety-agent"],
        milestone(
            "RUN-TUNNEL-STATE-AUTHORIZATION-20260901",
            "Fail closed unless Secure MCP Tunnel state resides on a verified private native-Linux authorization surface",
            [
                "PR 148 explicit persistent private state root",
                "PR 150 reject macOS and unsupported hosts",
                "PR 153 native-Linux filesystem, stacked-mount and ACL-xattr hardening",
            ],
        ),
    )
    add_milestone(
        departments["valuation-engine-agent"],
        milestone(
            "VAL-KR-MULTISEG-SOTP-20260901",
            "Execute evidence-bound Korean multi-segment SOTP through disclosed segment bijection and segment-specific methods",
            ["PR 151 KR live-run entry detail, IFRS 8 segment declarations and multi-segment SOTP"],
        ),
    )
    add_milestone(
        departments["evidence-industry-agent"],
        milestone(
            "EVI-COLLECTION-KNOWLEDGE-TIME-20260901",
            "Reject future-observed intrinsic Evidence at collection time and restore dependency-complete Sanil verification",
            ["PR 152 collection chronology and Sanil verified-report dependency coverage"],
            validation_evidence=VALIDATION[:2],
        ),
    )
    add_milestone(
        departments["calibration-risk-agent"],
        milestone(
            "CAL-PRODUCTION-HISTORY-WRITER-20260901",
            "Provide a hash-chained append-only writer for pre-resolution forecasts and first-seen realized outcomes",
            ["PR 155 append-only production probability history CLI"],
        ),
    )

    for item in portfolio.get("work_items", []):
        if item.get("id") == "CAL-PRODUCTION-COHORT-003":
            item["current_step"] = (
                "Record every qualifying pre-resolution forecast with `prism-probability-history append-forecast`; "
                "append only later first-seen primary-source outcomes, validate/export the hash-chained ledger, "
                "and accumulate the declared cohort thresholds tracked in Issue #154. Synthetic, reconstructed, "
                "migrated or post-hoc history remains forbidden."
            )

    add_handoff(
        portfolio,
        {
            "id": "H-CAL-PRODUCTION-HISTORY-WRITER-20260901",
            "to": "pm-integrator + calibration-risk-agent + performance-platform-agent + qa-release-agent",
            "head": ACCEPTED_SHA,
            "validation_evidence": [
                "GHA-VALUATION-TESTS-701",
                "GHA-SANIL-LIVE-PRIMARY-113",
                "GHA-SKHYNIX-LIVE-PRIMARY-89",
            ],
            "closes": ["CAL-PRODUCTION-HISTORY-WRITER-20260901"],
            "residual_work": ["CAL-PRODUCTION-COHORT-003"],
        },
    )
    add_handoff(
        portfolio,
        {
            "id": "H-EVIDENCE-TUNNEL-HARDENING-20260901",
            "to": "pm-integrator + evidence-industry-agent + runtime-safety-agent + qa-release-agent",
            "head": ACCEPTED_SHA,
            "validation_evidence": [
                "GHA-VALUATION-TESTS-701",
                "GHA-SANIL-LIVE-PRIMARY-113",
            ],
            "closes": [
                "EVI-COLLECTION-KNOWLEDGE-TIME-20260901",
                "RUN-TUNNEL-STATE-AUTHORIZATION-20260901",
            ],
            "residual_work": [],
        },
    )
    add_handoff(
        portfolio,
        {
            "id": "H-PRISM-ENTRY-SOTP-20260901",
            "to": "pm-integrator + runtime-safety-agent + valuation-engine-agent + qa-release-agent",
            "head": ACCEPTED_SHA,
            "validation_evidence": [
                "GHA-VALUATION-TESTS-701",
                "GHA-SANIL-LIVE-PRIMARY-113",
                "GHA-SKHYNIX-LIVE-PRIMARY-89",
            ],
            "closes": [
                "RUN-PRISM-INTENT-MCP-20260901",
                "VAL-KR-MULTISEG-SOTP-20260901",
            ],
            "residual_work": ["CAL-PRODUCTION-COHORT-003"],
        },
    )

    PORTFOLIO.write_text(
        yaml.safe_dump(
            portfolio,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
