from pathlib import Path

from valuation_engine.live_readiness import load_live_primary_readiness


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    report = load_live_primary_readiness(
        readiness_path=ROOT / "config" / "live_primary_readiness.yaml",
        stage_registry_path=ROOT / "config" / "control_plane_stage_registry.yaml",
    )
    print(
        "LIVE_PRIMARY readiness OK: "
        f"stages={len(report.stages)} "
        f"ready={report.canonical_live_ready_count} "
        f"partial={len(report.partial_live_stages)} "
        f"gaps={len(report.unresolved_live_stages)}"
    )


if __name__ == "__main__":
    main()
