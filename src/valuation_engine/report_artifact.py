from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re
from pathlib import PurePosixPath


_ARTIFACT_META = re.compile(
    r'<meta name="prism-report-artifact-id" content="([^"]+)">'
)


@dataclass(frozen=True)
class ReportArtifactBundle:
    artifact_id: str
    html_filename: str
    markdown_filename: str
    html: str
    markdown: str
    html_sha256: str
    markdown_sha256: str
    versioned_assets: tuple[tuple[str, str], ...] = ()

def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    if not token:
        raise ValueError("report artifact token cannot be empty")
    return token


def versioned_asset_filename(filename: str, artifact_id: str) -> str:
    path = PurePosixPath(filename)
    if path.name != filename or not path.suffix:
        raise ValueError("report asset filename must be a plain filename with suffix")
    artifact_hash = _safe_token(artifact_id).split("-")[-1]
    return f"{path.stem}_{artifact_hash}{path.suffix}"


def build_report_artifact_bundle(
    *,
    company_key: str,
    as_of: str,
    run_id: str,
    valuation_hash: str,
    reference_value_per_share: Decimal,
    html: str,
    markdown: str,
    markdown_link_alias: str | None = None,
    asset_filenames: tuple[str, ...] = (),
) -> ReportArtifactBundle:
    if not html.strip() or not markdown.strip():
        raise ValueError("report artifact requires HTML and Markdown content")
    if "</head>" not in html or "</body>" not in html:
        raise ValueError("report artifact HTML requires head and body boundaries")
    if not run_id or not valuation_hash:
        raise ValueError("report artifact requires run and valuation identities")

    key = _safe_token(company_key)
    date_token = _safe_token(as_of.replace("-", ""))
    value_token = f"TP{reference_value_per_share.quantize(Decimal('1')):.0f}"
    seed = "|".join(
        (
            "prism-report-artifact/v2",
            key,
            date_token,
            run_id,
            valuation_hash,
            value_token,
            hashlib.sha256(html.encode("utf-8")).hexdigest(),
            hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            ",".join(asset_filenames),
        )
    )
    short_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12].upper()
    artifact_id = f"{key}-{date_token}-{value_token}-{short_hash}"
    base_filename = f"{key}_{date_token}_{value_token}_{short_hash}"

    versioned_markdown_filename = f"{base_filename}.md"
    linked_html = (
        html.replace(markdown_link_alias, versioned_markdown_filename)
        if markdown_link_alias
        else html
    )
    versioned_assets = tuple(
        (filename, versioned_asset_filename(filename, artifact_id))
        for filename in asset_filenames
    )
    linked_markdown = markdown
    for original, versioned in versioned_assets:
        linked_html = linked_html.replace(original, versioned)
        linked_markdown = linked_markdown.replace(original, versioned)
    stamped_html = linked_html.replace(
        "</head>",
        f'<meta name="prism-report-artifact-id" content="{artifact_id}">\n</head>',
        1,
    ).replace(
        "</body>",
        (
            '<div class="report-artifact-id" style="font-size:10px;color:#667085;'
            'margin:16px 36px 24px">'
            f"보고서 ID {artifact_id} · 기준 목표가 "
            f"{reference_value_per_share:,.0f}원</div>\n</body>"
        ),
        1,
    )
    stamped_markdown = (
        linked_markdown.rstrip()
        + f"\n\n---\n보고서 ID `{artifact_id}` · 기준 목표가 "
        + f"{reference_value_per_share:,.0f}원\n"
    )
    return ReportArtifactBundle(
        artifact_id=artifact_id,
        html_filename=f"{base_filename}.html",
        markdown_filename=versioned_markdown_filename,
        html=stamped_html,
        markdown=stamped_markdown,
        html_sha256=hashlib.sha256(stamped_html.encode("utf-8")).hexdigest(),
        markdown_sha256=hashlib.sha256(
            stamped_markdown.encode("utf-8")
        ).hexdigest(),
        versioned_assets=versioned_assets,
    )


def extract_report_artifact_id(html: str) -> str:
    match = _ARTIFACT_META.search(html)
    if match is None:
        raise ValueError("report artifact identity is missing")
    return match.group(1)
