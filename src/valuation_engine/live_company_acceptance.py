from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json
import yaml


_REQUIRED_COMPANIES = ("OCI_HOLDINGS", "ORACLE", "BLOOM_ENERGY", "GE_VERNOVA")
_ALLOWED_STATUS = {"READY", "BLOCKED_SOURCE_FIXTURE"}
_REQUIRED_CONTRACT_FLAGS = (
    "full_live_primary_33_stage",
    "primary_evidence_lineage",
    "target_market_isolation_pre_freeze",
    "audit_and_intrinsic_freeze",
    "post_freeze_market_compare",
    "synthetic_placeholder_forbidden",
)
_READY_FIXTURE_FIELDS = (
    "company_id",
    "source_snapshot_hash",
    "industry_snapshot_hash",
    "ledger_snapshot_hash",
    "expected_terminal_stage",
)


@dataclass(frozen=True)
class LiveCompanyAcceptanceSummary:
    ready: tuple[str, ...]
    blocked: tuple[str, ...]


def validate_live_company_acceptance(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> LiveCompanyAcceptanceSummary:
    manifest_path = Path(path)
    root = Path(repo_root)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("version", 0)) != 1:
        raise ValueError("live company acceptance manifest requires version 1")
    contract = payload.get("required_contract")
    if not isinstance(contract, dict):
        raise ValueError("live company acceptance manifest requires required_contract")
    for flag in _REQUIRED_CONTRACT_FLAGS:
        if contract.get(flag) is not True:
            raise ValueError(f"live company acceptance contract flag must be true: {flag}")

    companies = payload.get("companies")
    if not isinstance(companies, dict):
        raise ValueError("live company acceptance manifest requires companies mapping")
    if tuple(sorted(companies)) != tuple(sorted(_REQUIRED_COMPANIES)):
        missing = sorted(set(_REQUIRED_COMPANIES) - set(companies))
        extra = sorted(set(companies) - set(_REQUIRED_COMPANIES))
        raise ValueError(
            f"live company acceptance company set mismatch: missing={missing}, extra={extra}"
        )

    ready: list[str] = []
    blocked: list[str] = []
    for company_id in _REQUIRED_COMPANIES:
        row = companies[company_id]
        if not isinstance(row, dict):
            raise ValueError(f"acceptance row {company_id} must be a mapping")
        status = str(row.get("status") or "")
        display_name = str(row.get("display_name") or "")
        fixture_path = str(row.get("fixture_path") or "")
        if not display_name or not fixture_path:
            raise ValueError(f"acceptance row {company_id} requires display_name and fixture_path")
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"acceptance row {company_id} has invalid status {status!r}")

        fixture = root / fixture_path
        if status == "BLOCKED_SOURCE_FIXTURE":
            blocker = str(row.get("blocker") or "")
            if not blocker:
                raise ValueError(f"blocked acceptance row {company_id} requires blocker")
            if fixture.exists():
                raise ValueError(
                    f"blocked acceptance row {company_id} has a fixture; validate it and promote explicitly"
                )
            blocked.append(company_id)
            continue

        if not fixture.is_file():
            raise ValueError(f"READY acceptance row {company_id} fixture is missing: {fixture_path}")
        fixture_payload = json.loads(fixture.read_text(encoding="utf-8"))
        if not isinstance(fixture_payload, dict):
            raise ValueError(f"READY fixture {company_id} must be a JSON mapping")
        missing_fields = [
            field for field in _READY_FIXTURE_FIELDS if not fixture_payload.get(field)
        ]
        if missing_fields:
            raise ValueError(
                f"READY fixture {company_id} missing fields: {', '.join(missing_fields)}"
            )
        if fixture_payload["company_id"] != company_id:
            raise ValueError(f"READY fixture {company_id} company_id mismatch")
        if fixture_payload["expected_terminal_stage"] != "FINAL_REPORT":
            raise ValueError(
                f"READY fixture {company_id} must prove completion through FINAL_REPORT"
            )
        if fixture_payload.get("synthetic") is not False:
            raise ValueError(f"READY fixture {company_id} must explicitly declare synthetic=false")
        ready.append(company_id)

    return LiveCompanyAcceptanceSummary(tuple(ready), tuple(blocked))
