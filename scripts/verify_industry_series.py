#!/usr/bin/env python3
"""Operator tool: check a KOSIS-style series before marking it verified.

`config/kr_industry_series_registry.yaml` ships with zero verified rows: an
industry series collects into Evidence only after a human has confirmed it
against the source catalog (table identity, unit, cadence). This tool is that
step. It fetches the candidate series, parses the observations, enriches them
with explicit publication/first-seen/revision timestamps, persists a frozen
snapshot, and prints a ready-to-paste registry row with ``verified: false`` so
the operator reviews real numbers before flipping the flag.

Usage:

    export KOSIS_API_KEY=...
    PYTHONPATH=src python scripts/verify_industry_series.py \
        --url 'https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList&apiKey=REPLACE&orgId=...&tblId=...&format=json&jsonVD=Y' \
        --metric benchmark_price --unit dimensionless \
        --definition-id DEF_PPI_STEEL --series-id KR_KOSIS_PPI_STEEL_V1 \
        --published-at 2026-08-28T09:00:00+09:00

The URL is fetched verbatim (with its credential); the printed template carries
the redacted ``{api_key}`` form, never the live key. The snapshot defaults to
``config/industry_series_snapshots/<series-id>.json`` and contains no
credential. Nothing is written to the registry — flipping ``verified: true``
stays a deliberate human edit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.industry_series_collector import (  # noqa: E402
    INDUSTRY_OBSERVABLE_METRICS,
    IndustrySeriesError,
    SERIES_SNAPSHOT_SCHEMA,
    _period_end,
    credential_free_verification_url,
)
from valuation_engine.live_indexers import (  # noqa: E402
    HttpTransport,
    parse_json_response,
    parse_kosis_series_values,
)


_CREDENTIAL_QUERY_KEYS = {
    "auth",
    "authorization",
    "api_key",
    "apikey",
    "awsaccesskeyid",
    "crtfc_key",
    "access_token",
    "refresh_token",
    "sig",
    "signature",
    "token",
    "password",
    "secret",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-goog-credential",
    "x-goog-signature",
}


def _credential_template(url: str, api_key: str | None) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold() in _CREDENTIAL_QUERY_KEYS or (api_key and api_key in value):
            value = "{api_key}"
        query.append((key, value))
    rendered = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
    )
    return rendered.replace("%7Bapi_key%7D", "{api_key}")


def _aware_timestamp(raw: str, *, label: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IndustrySeriesError(f"{label} must be an ISO timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndustrySeriesError(f"{label} must include a timezone")
    return value


def _response_hash(rows: list[dict]) -> str:
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def build_timestamped_snapshot(
    *,
    series_id: str,
    source_id: str,
    verification_url: str,
    rows: list[dict],
    published_at: str,
    captured_at: str,
    existing: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the credential-free snapshot consumed by the live collector.

    First capture is conservative: every row becomes knowable no earlier than
    this workflow's ``captured_at``. An unchanged row preserves its original
    timestamps on later refreshes; a changed value receives a new first-seen and
    revision timestamp, so a historical run before that refresh cannot see it.
    """
    published = _aware_timestamp(published_at, label="published_at")
    captured = _aware_timestamp(captured_at, label="captured_at")
    if captured < published:
        raise IndustrySeriesError("captured_at cannot precede published_at")
    observations = parse_kosis_series_values(rows)
    if not observations:
        raise IndustrySeriesError("snapshot has no parseable observations")

    existing_rows: dict[str, Mapping[str, object]] = {}
    if existing is not None:
        if (
            existing.get("schema_version") != SERIES_SNAPSHOT_SCHEMA
            or existing.get("series_id") != series_id
            or existing.get("source_id") != source_id
            or existing.get("verification_url") != verification_url
        ):
            raise IndustrySeriesError("existing snapshot identity does not match request")
        prior = existing.get("observations")
        if not isinstance(prior, list) or not all(
            isinstance(row, Mapping) for row in prior
        ):
            raise IndustrySeriesError("existing snapshot observations are invalid")
        existing_rows = {str(row.get("PRD_DE") or ""): row for row in prior}

    enriched = []
    for period, value in observations:
        prior = existing_rows.get(period)
        if prior is not None and str(prior.get("DT")) == value:
            enriched.append(dict(prior))
            continue
        revision = captured if prior is not None else published
        enriched.append(
            {
                "PRD_DE": period,
                "DT": value,
                "PUBLISHED_AT": published.isoformat(),
                "FIRST_SEEN_AT": captured.isoformat(),
                "REVISION_AT": revision.isoformat(),
            }
        )

    return {
        "schema_version": SERIES_SNAPSHOT_SCHEMA,
        "series_id": series_id,
        "source_id": source_id,
        "verification_url": verification_url,
        "captured_at": captured.isoformat(),
        "source_response_sha256": _response_hash(rows),
        "observations": enriched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full statisticsParameterData URL")
    parser.add_argument("--metric", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--source-id", default="KR_KOSIS_API")
    parser.add_argument("--definition-id", required=True)
    parser.add_argument("--geography", default="KR")
    parser.add_argument("--layer", default="authorized_market_data")
    parser.add_argument("--api-key-env", default="KOSIS_API_KEY")
    parser.add_argument(
        "--published-at",
        required=True,
        help="Timezone-aware source/catalog publication timestamp for this release",
    )
    parser.add_argument(
        "--captured-at",
        help="Timezone-aware first-seen timestamp (default: current UTC time)",
    )
    parser.add_argument(
        "--verification-url",
        help=(
            "Credential-free public catalog URL "
            "(default: API URL without credential parameters)"
        ),
    )
    parser.add_argument(
        "--snapshot-out",
        type=Path,
        help=(
            "Frozen JSON output "
            "(default: config/industry_series_snapshots/<series-id>.json)"
        ),
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.metric not in INDUSTRY_OBSERVABLE_METRICS:
        raise SystemExit(
            f"metric {args.metric!r} is not an industry-observable metric; the "
            "definition gate refuses company-realized quantities. Allowed: "
            + ", ".join(sorted(INDUSTRY_OBSERVABLE_METRICS))
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.series_id):
        raise SystemExit(
            "series-id must contain only letters, digits, dot, dash or underscore"
        )

    api_key = os.environ.get(args.api_key_env, "")
    transport = HttpTransport(timeout_seconds=args.timeout)
    rows = parse_json_response(transport.get_text(args.url).text)
    observations = parse_kosis_series_values(rows)
    if not observations:
        raise SystemExit(
            "no parseable (period, value) observations — check tblId/itmId and "
            "that the table returns PRD_DE/DT fields"
        )

    template_url = _credential_template(args.url, api_key)
    verification_url = credential_free_verification_url(
        args.verification_url or template_url
    )
    captured_at = args.captured_at or datetime.now(timezone.utc).isoformat()
    snapshot_path = args.snapshot_out or (
        ROOT / "config" / "industry_series_snapshots" / f"{args.series_id}.json"
    )
    snapshot_path = snapshot_path.resolve()
    existing = None
    if snapshot_path.exists():
        try:
            loaded = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"existing snapshot is invalid JSON: {exc}") from exc
        if not isinstance(loaded, Mapping):
            raise SystemExit("existing snapshot must be a JSON object")
        existing = loaded
    try:
        snapshot = build_timestamped_snapshot(
            series_id=args.series_id,
            source_id=args.source_id,
            verification_url=verification_url,
            rows=rows,
            published_at=args.published_at,
            captured_at=captured_at,
            existing=existing,
        )
    except IndustrySeriesError as exc:
        raise SystemExit(str(exc)) from exc
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        registry_snapshot_path = snapshot_path.relative_to(ROOT / "config").as_posix()
    except ValueError:
        registry_snapshot_path = str(snapshot_path)

    print(f"# parsed {len(observations)} observations for {args.series_id}")
    for period, value in observations[-8:]:
        try:
            effective = _period_end(period)
        except IndustrySeriesError:
            effective = "?"
        print(f"#   {period} -> {value}  (effective {effective})")
    print("#")
    print(f"# frozen snapshot written: {snapshot_path}")
    print("# Review the numbers against the KOSIS catalog, then paste this row")
    print("# into config/kr_industry_series_registry.yaml and set verified: true.")
    print("  - series_id: " + args.series_id)
    print("    source_id: " + args.source_id)
    print("    metric: " + args.metric)
    print("    layer: " + args.layer)
    print("    unit: " + args.unit)
    print("    geography: " + args.geography)
    print("    definition_id: " + args.definition_id)
    print("    definition: >-")
    print("      TODO operator: one sentence naming the exact KOSIS table, item")
    print("      and index base, and stating this is an industry observable, not")
    print("      a company-realized figure.")
    print("    url_template: " + template_url)
    print("    api_key_env: " + args.api_key_env)
    print("    snapshot_path: " + registry_snapshot_path)
    print("    verification_url: " + verification_url)
    print("    published_at_field: PUBLISHED_AT")
    print("    first_seen_at_field: FIRST_SEEN_AT")
    print("    revision_at_field: REVISION_AT")
    print("    verified: false  # flip to true only after catalog review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
