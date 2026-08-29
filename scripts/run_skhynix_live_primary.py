from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from valuation_engine.report_artifact import versioned_asset_filename
from valuation_engine.skhynix_continuous_live_primary import run_skhynix_live_primary
from valuation_engine.strict_live_runtime import require_canonical_live_result


LATEST_MANIFEST_FILENAME = "SKHYNIX_000660_LATEST_REPORT.json"
_SCENARIO_LABELS = {
    "Down": "하방",
    "Core": "기준",
    "Bull": "상방",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _render_calibrated_probability_summary(
    report: str,
    probability_snapshot,
    probability_distribution_status: object,
) -> str:
    """Render only the already-frozen canonical probability snapshot into the report artifact."""
    if str(probability_distribution_status) != "CALIBRATED":
        return report

    estimates = tuple(probability_snapshot.estimates)
    by_id = {item.scenario_id: item for item in estimates}
    if set(by_id) != set(_SCENARIO_LABELS):
        raise RuntimeError("calibrated probability snapshot must cover Down/Core/Bull")
    total = sum((Decimal(str(item.probability)) for item in estimates), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("1e-12"):
        raise RuntimeError("calibrated probability snapshot must sum to one")

    probability_summary = " · ".join(
        f"{_SCENARIO_LABELS[scenario_id]} "
        f"{Decimal(str(by_id[scenario_id].probability)) * 100:.1f}%"
        for scenario_id in ("Down", "Core", "Bull")
    )
    replacement = (
        f"| **시나리오 가능성** | {probability_summary} "
        "(보정 완료·수치 가중 적용) |"
    )
    probability_rows = tuple(
        line for line in report.splitlines() if line.startswith("| **시나리오 가능성** |")
    )
    if len(probability_rows) != 1:
        raise RuntimeError("canonical report must contain exactly one scenario probability row")
    existing = probability_rows[0]
    if existing == replacement:
        return report
    if "미산출" not in existing:
        raise RuntimeError("refusing to overwrite an unexpected scenario probability rendering")
    return report.replace(existing, replacement, 1)


def _artifact_identity(result, report: str, visual_payloads: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    valuation = result.data["generic_valuation_result"]
    core = next(item.value_per_share for item in valuation.scenarios if item.scenario_id == "Core")
    core_token = f"CORE{core.quantize(Decimal('1')):.0f}"
    market = result.data.get("market_comparison")
    as_of = getattr(getattr(market, "observation", None), "as_of", "2026-08-29")
    date_token = str(as_of)[:10].replace("-", "")
    seed = "|".join(
        (
            "prism-skhynix-report/v2-continuous-probability",
            result.run_id,
            str(result.data["valuation_hash"]),
            str(result.data["execution_attestation_hash"]),
            str(result.data.get("probability_calibration_snapshot_hash") or "NO_PROBABILITY_HASH"),
            core_token,
            _sha256_text(report),
            ",".join(f"{name}:{_sha256_text(svg)}" for name, svg in visual_payloads),
        )
    )
    short_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12].upper()
    artifact_id = f"SKHYNIX-000660-{date_token}-{core_token}-{short_hash}"
    return artifact_id, f"SKHYNIX_000660_{date_token}_{core_token}_{short_hash}"


def _blocked_diagnostic(authority) -> None:
    print(
        json.dumps(
            {
                "blocked_reasons": list(authority.result.blocked_reasons),
                "stage_traces": [
                    {"stage": item.stage, "status": item.status.value, "rationale": item.rationale}
                    for item in authority.result.stage_traces
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run_and_render(output: Path | None = None) -> dict[str, object]:
    with TemporaryDirectory(prefix="skhynix-prism-") as state_root:
        authority = run_skhynix_live_primary(state_root)
        if authority.result.blocked_reasons:
            _blocked_diagnostic(authority)
            raise RuntimeError("SK hynix canonical run blocked")
        result = require_canonical_live_result(authority)
        valuation = result.data["generic_valuation_result"]
        probability_snapshot = result.data["continuous_probability_calibration_snapshot"]
        report = _render_calibrated_probability_summary(
            str(result.data["final_report"]),
            probability_snapshot,
            result.data.get("probability_distribution_status"),
        )
        run_dir = Path(str(result.data["saved_run_dir"]))
        visual_names = tuple(str(name) for name in result.data.get("saved_report_visuals", ()))
        if len(visual_names) != 2:
            raise RuntimeError("canonical SK hynix report requires exactly two deterministic SVG cards")
        visual_payloads = tuple((name, (run_dir / name).read_text(encoding="utf-8")) for name in visual_names)
        artifact_id, filename_base = _artifact_identity(result, report, visual_payloads)
        versioned_visuals = tuple(
            (name, versioned_asset_filename(name, artifact_id), svg)
            for name, svg in visual_payloads
        )
        stamped_report = report
        for original, versioned, _ in versioned_visuals:
            stamped_report = stamped_report.replace(original, versioned)
        stamped_report = stamped_report.rstrip() + f"\n\n---\n보고서 ID `{artifact_id}`\n"
        report_sha = _sha256_text(stamped_report)
        versioned_report_name = f"{filename_base}.md"
        versioned_manifest_name = f"{filename_base}.json"
        summary = {
            "artifact_id": artifact_id,
            "run_id": result.run_id,
            "canonical_entrypoint_id": result.data.get("canonical_entrypoint_id"),
            "blocked_reasons": list(result.blocked_reasons),
            "freeze_token_present": result.freeze_token is not None,
            "execution_attestation_hash": result.data.get("execution_attestation_hash"),
            "valuation_hash": result.data.get("valuation_hash"),
            "probability_calibration_snapshot_hash": result.data.get("probability_calibration_snapshot_hash"),
            "probability_calibration_dataset_hash": result.data.get("probability_calibration_dataset_hash"),
            "probability_distribution_status": result.data.get("probability_distribution_status"),
            "scenario_probabilities": {
                item.scenario_id: {
                    "probability": str(item.probability),
                    "lower": str(item.lower_probability),
                    "upper": str(item.upper_probability),
                }
                for item in probability_snapshot.estimates
            },
            "expected_value_per_share": (
                str(valuation.expected_value_per_share)
                if valuation.expected_value_per_share is not None
                else None
            ),
            "scenario_values": {item.scenario_id: str(item.value_per_share) for item in valuation.scenarios},
            "report": {"filename": versioned_report_name, "sha256": report_sha},
            "visuals": [
                {"filename": versioned, "sha256": _sha256_text(svg)}
                for _, versioned, svg in versioned_visuals
            ],
            "final_report_present": bool(stamped_report),
        }
        manifest = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            versioned_report = output.parent / versioned_report_name
            versioned_manifest = output.parent / versioned_manifest_name
            latest_manifest = output.parent / LATEST_MANIFEST_FILENAME
            if versioned_report.exists() and versioned_report.read_text(encoding="utf-8") != stamped_report:
                raise FileExistsError(f"refusing to overwrite immutable report: {versioned_report}")
            if versioned_manifest.exists() and versioned_manifest.read_text(encoding="utf-8") != manifest:
                raise FileExistsError(f"refusing to overwrite immutable manifest: {versioned_manifest}")
            versioned_report.write_text(stamped_report, encoding="utf-8")
            versioned_manifest.write_text(manifest, encoding="utf-8")
            latest_manifest.write_text(manifest, encoding="utf-8")
            output.write_text(stamped_report, encoding="utf-8")
            output.with_suffix(".json").write_text(manifest, encoding="utf-8")
            for original, versioned, svg in versioned_visuals:
                source = run_dir / original
                target = output.parent / versioned
                if target.exists() and target.read_text(encoding="utf-8") != svg:
                    raise FileExistsError(f"refusing to overwrite immutable visual: {target}")
                if source.exists():
                    shutil.copy2(source, target)
                else:
                    target.write_text(svg, encoding="utf-8")

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_and_render(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
