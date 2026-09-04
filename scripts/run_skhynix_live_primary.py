from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.report_artifact import build_report_artifact_bundle, versioned_asset_filename
from valuation_engine.skhynix_brokerage_html import render_skhynix_brokerage_html
from valuation_engine.skhynix_continuous_live_primary import (
    render_calibrated_probability_summary,
    run_skhynix_live_primary,
)
from valuation_engine.skhynix_public_report import (
    render_skhynix_public_report,
    render_skhynix_public_visual,
    skhynix_public_source_links,
)
from valuation_engine.strict_live_runtime import require_canonical_live_result


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTML_OUTPUT = ROOT / "examples" / "report_forms" / "SKHYNIX_000660_LIVE_PRIMARY_REPORT.html"
DEFAULT_MARKDOWN_OUTPUT = ROOT / "examples" / "report_forms" / "SKHYNIX_000660_LIVE_PRIMARY_REPORT.md"
LATEST_MANIFEST_FILENAME = "SKHYNIX_000660_LATEST_REPORT.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _blocked_diagnostic(authority) -> None:
    print(json.dumps({"차단사유": list(authority.result.blocked_reasons)}, ensure_ascii=False, indent=2))


def render_report(state_root: Path) -> tuple[object, tuple[tuple[str, str], ...], dict[str, object]]:
    authority = run_skhynix_live_primary(state_root)
    if authority.result.blocked_reasons:
        _blocked_diagnostic(authority)
        raise RuntimeError("SK하이닉스 표준 가치평가 실행이 차단되었습니다")
    result = require_canonical_live_result(authority)
    valuation = result.data["generic_valuation_result"]
    probability_snapshot = result.data["continuous_probability_calibration_snapshot"]
    markdown_report = render_calibrated_probability_summary(
        str(result.data["final_report"]),
        probability_snapshot,
        result.data.get("probability_distribution_status"),
    )
    source_links = skhynix_public_source_links(result.data)
    markdown_report = render_skhynix_public_report(
        markdown_report,
        data=result.data,
        source_links=source_links,
    )

    run_dir = Path(str(result.data["saved_run_dir"]))
    visual_names = tuple(str(name) for name in result.data.get("saved_report_visuals", ()))
    if len(visual_names) != 2:
        raise RuntimeError("SK하이닉스 최종보고서는 표준 요약 이미지 2장이 필요합니다")
    visuals = tuple(
        (
            name,
            render_skhynix_public_visual(
                (run_dir / name).read_text(encoding="utf-8"),
                card_number=index,
            ),
        )
        for index, name in enumerate(visual_names, start=1)
    )
    market = result.data["market_comparison"]
    as_of = str(market.observation.as_of)[:10]
    html_report = render_skhynix_brokerage_html(
        markdown_report,
        summary_filename=visuals[0][0],
        assumptions_filename=visuals[1][0],
        as_of=as_of,
        markdown_filename=DEFAULT_MARKDOWN_OUTPUT.name,
        source_links=source_links,
    )
    core_value = next(
        item.value_per_share for item in valuation.scenarios if item.scenario_id == "Core"
    )
    artifact = build_report_artifact_bundle(
        company_key="SKHYNIX_000660",
        as_of=as_of,
        run_id=result.run_id,
        valuation_hash=str(result.data["valuation_hash"]),
        reference_value_per_share=Decimal(str(core_value)),
        html=html_report,
        markdown=markdown_report,
        markdown_link_alias=DEFAULT_MARKDOWN_OUTPUT.name,
        asset_filenames=tuple(name for name, _ in visuals),
    )
    summary = {
        "artifact_id": artifact.artifact_id,
        "html_filename": artifact.html_filename,
        "html_sha256": artifact.html_sha256,
        "markdown_filename": artifact.markdown_filename,
        "markdown_sha256": artifact.markdown_sha256,
        "run_id": result.run_id,
        "valuation_hash": result.data.get("valuation_hash"),
        "probability_calibration_snapshot_hash": result.data.get("probability_calibration_snapshot_hash"),
        "expected_value_per_share": (
            str(valuation.expected_value_per_share)
            if valuation.expected_value_per_share is not None
            else None
        ),
        "visuals": [
            {
                "filename": versioned_asset_filename(name, artifact.artifact_id),
                "sha256": _sha256_text(svg),
            }
            for name, svg in visuals
        ],
    }
    return artifact, visuals, summary


def _verify_file(path: Path, expected: str, label: str) -> None:
    if not path.exists() or path.read_text(encoding="utf-8") != expected:
        raise SystemExit(f"SK하이닉스 {label}이 누락되었거나 최신 상태가 아닙니다: {path}")


def publish_or_check(
    *,
    html_output: Path,
    markdown_output: Path,
    check: bool,
    state_root: Path | None = None,
) -> dict[str, object]:
    if state_root is not None:
        state_root.mkdir(parents=True, exist_ok=True)
        artifact, visuals, summary = render_report(state_root)
    else:
        with TemporaryDirectory(prefix="skhynix-prism-") as temporary:
            artifact, visuals, summary = render_report(Path(temporary))

    html_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    versioned_html = html_output.parent / artifact.html_filename
    versioned_markdown = markdown_output.parent / artifact.markdown_filename
    latest_manifest = html_output.parent / LATEST_MANIFEST_FILENAME
    manifest = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if check:
        _verify_file(html_output, artifact.html, "HTML 최종보고서")
        _verify_file(markdown_output, artifact.markdown, "마크다운 검증본")
        _verify_file(versioned_html, artifact.html, "버전 HTML 보고서")
        _verify_file(versioned_markdown, artifact.markdown, "버전 마크다운 보고서")
        _verify_file(latest_manifest, manifest, "최신 보고서 매니페스트")
        for name, svg in visuals:
            _verify_file(html_output.parent / name, svg, "요약 이미지")
            _verify_file(
                html_output.parent / versioned_asset_filename(name, artifact.artifact_id),
                svg,
                "버전 요약 이미지",
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary

    for target, content, label in (
        (versioned_html, artifact.html, "HTML 보고서"),
        (versioned_markdown, artifact.markdown, "마크다운 보고서"),
    ):
        if target.exists() and target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"기존 확정 {label}를 덮어쓸 수 없습니다: {target}")
    html_output.write_text(artifact.html, encoding="utf-8")
    markdown_output.write_text(artifact.markdown, encoding="utf-8")
    versioned_html.write_text(artifact.html, encoding="utf-8")
    versioned_markdown.write_text(artifact.markdown, encoding="utf-8")
    latest_manifest.write_text(manifest, encoding="utf-8")
    for name, svg in visuals:
        (html_output.parent / name).write_text(svg, encoding="utf-8")
        versioned = html_output.parent / versioned_asset_filename(name, artifact.artifact_id)
        if versioned.exists() and versioned.read_text(encoding="utf-8") != svg:
            raise FileExistsError(f"기존 확정 이미지를 덮어쓸 수 없습니다: {versioned}")
        versioned.write_text(svg, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_HTML_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()
    publish_or_check(
        html_output=args.output,
        markdown_output=args.markdown_output,
        check=args.check,
        state_root=args.state_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
