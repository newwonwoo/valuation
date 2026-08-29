#!/usr/bin/env python3
"""Refuse a readiness claim that the repository cannot back.

``config/live_primary_readiness.yaml`` used to carry one hand-written status word
per stage, and nothing checked whether the word was true. This script imports
every symbol declared in ``config/stage_capability_declarations.yaml`` and
derives each stage's capability from what actually resolves, then fails if the
hand-written status claims more than the probe proves.

A declaration can only understate: naming a symbol that does not exist, or one
that lives in a company-bound module, is an error rather than a pass.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.live_readiness import (  # noqa: E402
    LiveReadinessStatus,
    load_live_primary_readiness,
)
from valuation_engine.orchestrator import load_stage_sequence  # noqa: E402
from valuation_engine.stage_capability import (  # noqa: E402
    DerivedCapability,
    build_stage_capability_report,
    load_stage_capability_declarations,
    probe_cold_start,
)


#: A declared status may not exceed what the derived capability supports.
#: LIVE_READY and RUNTIME_READY both assert the stage can execute once typed
#: inputs arrive, which is false when no implementation of its provider exists.
_OPTIMISTIC_STATUSES = {
    LiveReadinessStatus.LIVE_READY,
    LiveReadinessStatus.RUNTIME_READY,
    LiveReadinessStatus.PARTIAL_LIVE,
}
_UNBACKED = {DerivedCapability.PROVIDER_REQUIRED, DerivedCapability.UNDECLARED}


def main() -> int:
    canonical = load_stage_sequence(ROOT / "config" / "control_plane_stage_registry.yaml")
    declarations, company_bound = load_stage_capability_declarations(
        ROOT / "config" / "stage_capability_declarations.yaml"
    )
    base = build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=canonical,
    )
    cold = probe_cold_start(base.stages)
    if not cold.missing_provider_slots:
        # Every required slot has a company-neutral implementation, so the
        # stronger claim is testable: actually run the canonical runtime on a
        # company this repository has never seen and record what executed.
        from valuation_engine.cold_start_probe import execute_cold_start_probe

        cold = execute_cold_start_probe()
    report = build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=canonical,
        cold_start=cold,
    )

    readiness = load_live_primary_readiness(
        readiness_path=ROOT / "config" / "live_primary_readiness.yaml",
        stage_registry_path=ROOT / "config" / "control_plane_stage_registry.yaml",
    )
    declared = {item.stage: item for item in readiness.stages}

    failures: list[str] = []
    for capability in report.stages:
        row = declared[capability.stage]
        if capability.derived in _UNBACKED and row.status in _OPTIMISTIC_STATUSES:
            failures.append(
                f"{capability.stage}: declared {row.status.value} but the probe derives "
                f"{capability.derived.value} — {capability.note}"
            )
        if (
            row.status is LiveReadinessStatus.PROVIDER_REQUIRED
            and capability.derived not in _UNBACKED
        ):
            failures.append(
                f"{capability.stage}: declared PROVIDER_REQUIRED but "
                f"{capability.implementation.ref} resolves; the declaration is stale"
            )

    counts = report.counts()
    print(
        "stage capability: "
        f"stages={len(report.stages)} "
        f"cold_proven={report.cold_proven_count} "
        f"implemented={counts[DerivedCapability.IMPLEMENTED]} "
        f"contract_only={counts[DerivedCapability.CONTRACT_ONLY]} "
        f"provider_required={counts[DerivedCapability.PROVIDER_REQUIRED]} "
        f"undeclared={counts[DerivedCapability.UNDECLARED]}"
    )
    if cold.config_blocked_reason:
        print(f"cold start: BLOCKED — {cold.config_blocked_reason}")
    elif cold.probed and cold.blocking_stage is None:
        print(
            f"cold start: COMPLETED — all {len(cold.reached)}/{len(report.stages)} stages "
            "executed to an attested freeze and final report for an unseen company"
        )
    elif cold.probed:
        print(
            f"cold start: EXECUTED — reached {len(cold.reached)}/{len(report.stages)} stages; "
            f"stopped at {cold.blocking_stage}: {cold.blocking_reason}"
        )
    else:
        print(
            "cold start: NOT PROBED — every required provider slot is filled; "
            "an executed cold run is now required to claim COLD_PROVEN"
        )

    if failures:
        print("\nreadiness claims not backed by the repository:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
