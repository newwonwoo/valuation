from decimal import Decimal

import pytest

from valuation_engine.report_artifact import (
    build_report_artifact_bundle,
    extract_report_artifact_id,
    versioned_asset_filename,
)


def bundle(value: str = "242038"):
    return build_report_artifact_bundle(
        company_key="SANIL_062040",
        as_of="2026-08-27",
        run_id="SANIL-062040-20260827-POLICY",
        valuation_hash="a" * 64,
        reference_value_per_share=Decimal(value),
        html="<html><head><title>보고서</title></head><body>본문</body></html>",
        markdown="# 보고서\n\n본문\n\n![요약](summary.svg)",
        asset_filenames=("summary.svg",),
    )


def test_report_bundle_has_immutable_value_and_hash_identity():
    result = bundle()

    assert "TP242038" in result.artifact_id
    filename_id = result.artifact_id.replace("-", "_")
    assert filename_id in result.html_filename
    assert filename_id in result.markdown_filename
    assert extract_report_artifact_id(result.html) == result.artifact_id
    assert f"보고서 ID {result.artifact_id}" in result.html
    assert f"`{result.artifact_id}`" in result.markdown
    versioned_visual = versioned_asset_filename("summary.svg", result.artifact_id)
    assert versioned_visual in result.html or versioned_visual in result.markdown
    assert result.versioned_assets == (("summary.svg", versioned_visual),)


def test_changed_value_gets_a_different_download_filename():
    assert bundle("242038").html_filename != bundle("168223").html_filename


def test_report_bundle_requires_stamped_html_boundaries():
    with pytest.raises(ValueError, match="head and body boundaries"):
        build_report_artifact_bundle(
            company_key="SANIL",
            as_of="2026-08-27",
            run_id="R1",
            valuation_hash="a" * 64,
            reference_value_per_share=Decimal("1"),
            html="not-html",
            markdown="# report",
        )
