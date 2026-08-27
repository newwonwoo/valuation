from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, urlopen

import yaml

from valuation_engine.live_company_acceptance import sha256_file
from valuation_engine.live_company_artifact import (
    SourceDocumentLineage,
    serialize_live_company_blocked,
    serialize_live_company_success,
)
from valuation_engine.live_runtime import run_prism
from valuation_engine.required_company_live import (
    DEFAULT_SPEC_PATH,
    build_acceptance_spec,
    build_real_company_runtime,
    load_acceptance_specs,
    spec_file_hash,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "live_company_acceptance.yaml"
DEFAULT_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live_companies"
COMPANY_FILE_NAMES = {
    "OCI_HOLDINGS": "oci_holdings",
    "ORACLE": "oracle",
    "BLOOM_ENERGY": "bloom_energy",
    "GE_VERNOVA": "ge_vernova",
}
USER_AGENT = (
    "newwonwoo-valuation-live-acceptance/1.0 "
    "research-automation contact: repository-owner"
)


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
            "Accept-Encoding": "identity",
        },
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
            if not payload:
                raise RuntimeError(f"empty official document: {url}")
            return payload
        except Exception as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"cannot download official document {url}: {last_error}")


def _market_observation(row: dict) -> tuple[float, str]:
    fallback = float(row["market_fallback_price"])
    as_of = str(row["as_of"])
    try:
        import FinanceDataReader as fdr

        frame = fdr.DataReader(
            str(row["market_symbol"]),
            "2026-08-24",
            "2026-08-28",
        )
        if frame.empty or "Close" not in frame.columns:
            return fallback, as_of
        eligible = frame.loc[frame.index.strftime("%Y-%m-%d") <= as_of]
        if eligible.empty:
            return fallback, as_of
        return (
            float(eligible.iloc[-1]["Close"]),
            eligible.index[-1].strftime("%Y-%m-%d"),
        )
    except Exception:
        return fallback, as_of


def _source_documents(spec) -> tuple[SourceDocumentLineage, ...]:
    return (
        SourceDocumentLineage(
            source_id=spec.official_source_id,
            source_ref=spec.official_source_ref,
            document_id=spec.official_document_id,
            document_hash=spec.official_document_hash,
            published_at=str(spec.payload["published_at"]),
            first_seen_at=str(spec.payload["first_seen_at"]),
        ),
        SourceDocumentLineage(
            source_id=spec.official_source_id,
            source_ref=spec.underwriting_source_ref,
            document_id=f"{spec.company_id}_UNDERWRITING_SPEC",
            document_hash=spec.underwriting_document_hash,
            published_at=str(spec.payload["first_seen_at"]),
            first_seen_at=str(spec.payload["first_seen_at"]),
        ),
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _generate_company(
    company_id: str,
    row: dict,
    fixture_root: Path,
) -> tuple[Path, Path]:
    official_bytes = _download(str(row["official_source_ref"]))
    official_hash = sha256(official_bytes).hexdigest()
    market_price, market_as_of = _market_observation(row)
    spec = build_acceptance_spec(
        company_id,
        official_document_hash=official_hash,
        underwriting_document_hash=spec_file_hash(DEFAULT_SPEC_PATH),
        market_price=market_price,
        market_as_of=market_as_of,
    )
    source_documents = _source_documents(spec)
    base_name = COMPANY_FILE_NAMES[company_id]
    success_path = fixture_root / f"{base_name}.success.json"
    blocked_path = fixture_root / f"{base_name}.blocked.json"

    with TemporaryDirectory(prefix=f"{base_name}-success-") as state_root:
        success_result = run_prism(
            build_real_company_runtime(spec, state_root=state_root)
        )
    if not success_result.completed or success_result.freeze_token is None:
        raise RuntimeError(
            f"{company_id} success run did not complete: "
            f"{success_result.blocked_reasons}"
        )
    success_artifact = serialize_live_company_success(
        company_id=company_id,
        result=success_result,
        source_documents=source_documents,
        fixture_identity=(
            success_result.run_id,
            str(success_result.data["source_snapshot_hash"]),
            str(success_result.data["valuation_hash"]),
        ),
        synthetic=False,
    )
    _write_json(success_path, success_artifact)

    with TemporaryDirectory(prefix=f"{base_name}-blocked-") as state_root:
        blocked_result = run_prism(
            build_real_company_runtime(
                spec,
                state_root=state_root,
                blocked_post_freeze=True,
            )
        )
    if not blocked_result.blocked_reasons or blocked_result.freeze_token is not None:
        raise RuntimeError(
            f"{company_id} blocked run did not fail closed: "
            f"{blocked_result.blocked_reasons}"
        )
    blocked_artifact = serialize_live_company_blocked(
        company_id=company_id,
        result=blocked_result,
        expected_reason_contains="Street reference provider is not configured",
        fixture_identity=(
            blocked_result.run_id,
            str(blocked_result.data.get("source_snapshot_hash", "NO_SOURCE_HASH")),
            blocked_result.stage_traces[-1].stage,
        ),
        source_documents=source_documents,
        synthetic=False,
    )
    _write_json(blocked_path, blocked_artifact)
    return success_path, blocked_path


def _update_manifest(
    manifest_path: Path,
    generated: dict[str, tuple[Path, Path]],
) -> None:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    companies = payload["companies"]
    for company_id, (success_path, blocked_path) in generated.items():
        row = companies[company_id]
        row["status"] = "READY"
        row["success_fixture_path"] = str(success_path.relative_to(ROOT))
        row["adversarial_fixture_path"] = str(blocked_path.relative_to(ROOT))
        row["success_fixture_sha256"] = sha256_file(success_path)
        row["adversarial_fixture_sha256"] = sha256_file(blocked_path)
        row.pop("blocker", None)
    manifest_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    args = parser.parse_args()

    specs = load_acceptance_specs(DEFAULT_SPEC_PATH)
    generated: dict[str, tuple[Path, Path]] = {}
    for company_id in COMPANY_FILE_NAMES:
        generated[company_id] = _generate_company(
            company_id,
            dict(specs[company_id]),
            args.fixture_root,
        )
        print(f"generated {company_id} success + blocked artifacts")
    _update_manifest(args.manifest, generated)
    print("live-company acceptance manifest updated to READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
