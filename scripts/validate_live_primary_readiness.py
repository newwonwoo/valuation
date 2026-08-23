from pathlib import Path

from valuation_engine.live_readiness import load_live_primary_readiness


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    report = load_live_primary_readiness(
        readiness_path=ROOT / "config" / "live_primary_readiness.yaml",
        stage_registry_path=ROOT / "config" / "control_plane_stage_registry.yaml",
        method_capability_path=ROOT / "config" / "valuation_method_capability_registry.yaml",
        archetype_registry_path=ROOT / "config" / "archetype_module_registry.yaml",
        repo_root=ROOT,
    )
    coverage = report.deterministic_method_coverage
    if coverage is None:
        raise SystemExit("valuation method coverage was not validated")
    print(
        "LIVE_PRIMARY readiness OK: "
        f"stages={len(report.stages)} "
        f"ready={report.canonical_live_ready_count} "
        f"partial={len(report.partial_live_stages)} "
        f"gaps={len(report.unresolved_live_stages)} "
        f"valuation_methods={coverage.total} "
        f"method_ready={len(coverage.runtime_ready)} "
        f"method_partial={len(coverage.partial_runtime)} "
        f"method_missing={len(coverage.not_implemented)}"
    )


if __name__ == "__main__":
    main()
