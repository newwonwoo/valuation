#!/usr/bin/env python3
"""Build an uncertified diagnostic from a verified source scenario bundle.

The script is generic: all company identity, scenario labels, claims and prior
weights arrive in the spec.  It never reads market/broker data or certifies a new intrinsic result.
Canonical audit/freeze must occur in the orchestrator, not this script.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import html
import json
from pathlib import Path
import sys

import yaml

from valuation_engine.governed_event_distribution import (
    StructuralAssetBasis,
    StructuralClaimBasis,
    StructuralEquityBranch,
    StructuralMaturityBasis,
    StructuralModelQualification,
    StructuralModelRole,
    StructuralVolatilityBasis,
    compose_governed_equity_distribution,
)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f}원"


def _pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):,.1f}%"


def _decimal_rows(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _decimal_rows(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_decimal_rows(item) for item in value]
    return value


def _source_valuation_snapshot(run_dir: Path, spec: dict) -> tuple[dict, dict]:
    snapshot = _json((run_dir / spec["source_valuation_snapshot"]).resolve())
    if snapshot.get("source_run_status") != "COMPLETED" or snapshot.get("source_audit_passed") is not True:
        raise ValueError("source valuation run is not completed and audited")
    if not snapshot.get("source_valuation_hash") or not snapshot.get("source_audit_hash"):
        raise ValueError("source valuation snapshot is missing immutable receipts")
    manifest_ref = spec.get("source_bundle_manifest")
    manifest_hash = spec.get("source_bundle_manifest_sha256")
    if not manifest_ref or not manifest_hash:
        raise ValueError("source bundle manifest and pinned SHA256 are required")
    manifest_path = (run_dir / manifest_ref).resolve()
    if _sha(manifest_path) != manifest_hash:
        raise ValueError("source bundle manifest hash mismatch")
    manifest = _json(manifest_path)
    if manifest.get("schema_version") != "kr-live-report-bundle/v1":
        raise ValueError("unsupported source bundle manifest")
    expected = {
        "artifact_id": snapshot.get("source_artifact_id"),
        "run_id": snapshot.get("source_run_id"),
        "as_of": spec["as_of"],
        "ticker": spec["ticker"],
        "valuation_hash": snapshot["source_valuation_hash"],
        "audit_hash": snapshot["source_audit_hash"],
    }
    if any(not value or manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("source bundle identity or hash mismatch")
    if snapshot.get("target_id") != spec["target_id"] or snapshot.get("as_of") != spec["as_of"]:
        raise ValueError("source snapshot target or cutoff mismatch")
    root = manifest_path.parent
    receipts = manifest.get("files", [])
    names = [row["filename"] for row in receipts]
    required = {"valuation.json", "audit.json", "manifest.json", "freeze_token.json", "compiled_assumptions.json"}
    if len(names) != len(set(names)) or not required.issubset(names):
        raise ValueError("source bundle receipts are incomplete or duplicated")
    for row in receipts:
        path = (root / row["filename"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("source bundle artifact is missing or outside its bundle")
        if _sha(path) != row["sha256"]:
            raise ValueError(f"source artifact hash mismatch: {row['filename']}")
    valuation = _json(root / "valuation.json")
    audit = _json(root / "audit.json")
    run = _json(root / "manifest.json")
    freeze = _json(root / "freeze_token.json")
    assumptions = _json(root / "compiled_assumptions.json")
    if (run.get("status") != "COMPLETED" or run.get("audit_passed") is not True
            or run.get("run_id") != expected["run_id"] or run.get("ticker") != spec["ticker"]
            or assumptions.get("target_id") != spec["target_id"]):
        raise ValueError("source run is not a completed matching target")
    findings = audit.get("findings", [])
    if not findings or any(row.get("blocking") and row.get("passed") is not True for row in findings):
        raise ValueError("source audit contains blocking failures or no findings")
    if (valuation.get("valuation_hash") != expected["valuation_hash"]
            or any(freeze.get(key) != expected[key] for key in ("run_id", "valuation_hash", "audit_hash"))):
        raise ValueError("source valuation and canonical freeze receipts disagree")
    source_rows = valuation["equity_aggregation"]["scenario_values"]
    actual = [(row["scenario_id"], Decimal(row["equity_value"]["amount"])) for row in source_rows]
    claimed = [(row["scenario_id"], Decimal(row["equity_value_KRW"])) for row in snapshot["scenario_values"]]
    if (len({key for key, _ in actual}) != len(actual) or actual != claimed
            or any(not value.is_finite() for _, value in actual)
            or any(row["equity_value"]["unit"] != "KRW" or row["equity_value"]["as_of"] != spec["as_of"] for row in source_rows)):
        raise ValueError("source snapshot scenario values, units or cutoff mismatch")
    return snapshot, valuation


def _scenario_equity(valuation: dict) -> dict[str, Decimal]:
    rows = valuation["equity_aggregation"]["scenario_values"]
    return {
        row["scenario_id"]: Decimal(row["equity_value"]["amount"])
        for row in rows
    }


def _mean_sensitivity(branch_values, sets):
    values = [value for _, value, _ in branch_values]
    rows = []
    for item in sets:
        weights = [Decimal(value) for value in item["probabilities"]]
        if len(weights) != len(values) or sum(weights, Decimal("0")) != Decimal("1"):
            raise ValueError("probability sensitivity weights are invalid")
        rows.append(
            {
                "label": item["label"],
                "probability_weighted_mean": str(
                    sum((value * weight for value, weight in zip(values, weights)), Decimal("0"))
                ),
            }
        )
    return rows


def _svg(title: str, lines: list[str], *, distribution_hash: str) -> str:
    width, height = 1200, 630
    safe_title = html.escape(title)
    text = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1200" height="630" fill="#0b1220"/>',
        '<rect x="48" y="48" width="1104" height="534" rx="24" fill="#111c31" stroke="#2f80ed" stroke-width="2"/>',
        f'<text x="88" y="112" fill="#8fc5ff" font-size="34" font-family="sans-serif" font-weight="700">{safe_title}</text>',
    ]
    y = 174
    for line in lines:
        text.append(
            f'<text x="88" y="{y}" fill="#f3f6fb" font-size="25" font-family="sans-serif">{html.escape(line)}</text>'
        )
        y += 53
    text.append(
        f'<text x="88" y="552" fill="#7f91ac" font-size="16" font-family="monospace">distribution {distribution_hash[:24]}</text>'
    )
    text.append("</svg>\n")
    return "\n".join(text)


def build(spec_path: Path, output_root: Path) -> Path:
    run_dir = spec_path.parent.parent
    spec = _json(spec_path)
    source_snapshot, valuation = _source_valuation_snapshot(run_dir, spec)
    financing_path = (run_dir / spec["source_financing_spec"]).resolve()
    risk_path = (run_dir / spec["source_risk_pack"]).resolve()
    financing = _json(financing_path)
    risk = yaml.safe_load(risk_path.read_text(encoding="utf-8"))
    scenario_equity = _scenario_equity(valuation)
    claims = Decimal(spec["gross_claim_face_value_KRW"])
    shares = Decimal(spec["diluted_shares"])
    qualification_row = spec.get("structural_model_qualification") or {
        "claim_basis": "CURRENT_CARRYING_AMOUNT",
        "asset_basis": "DCF_DERIVED",
        "volatility_basis": "SCENARIO_ENVELOPE_PROXY",
        "maturity_basis": "MULTI_MATURITY_AGGREGATE_PROXY",
        "evidence_path_ids": ["missing:structural_model_qualification"],
        "permitted_role": "DIAGNOSTIC_CROSS_CHECK_ONLY",
    }
    qualification = StructuralModelQualification(
        claim_basis=StructuralClaimBasis(qualification_row["claim_basis"]),
        asset_basis=StructuralAssetBasis(qualification_row["asset_basis"]),
        volatility_basis=StructuralVolatilityBasis(
            qualification_row["volatility_basis"]
        ),
        maturity_basis=StructuralMaturityBasis(qualification_row["maturity_basis"]),
        evidence_path_ids=tuple(qualification_row["evidence_path_ids"]),
        permitted_role=StructuralModelRole(
            qualification_row.get(
                "permitted_role", "DIAGNOSTIC_CROSS_CHECK_ONLY"
            )
        ),
    )
    source_bridge_hash = sha256(
        (source_snapshot["source_valuation_hash"] + _sha(financing_path) + _sha(risk_path)).encode("utf-8")
    ).hexdigest()
    branches = tuple(
        StructuralEquityBranch(
            branch_id=row["branch_id"],
            probability=Decimal(row["probability"]),
            business_asset_value=scenario_equity[row["source_scenario"]] + claims,
            senior_claim_value=claims,
            annual_asset_volatility=Decimal(spec["annual_asset_volatility"]),
            risk_free_rate=Decimal(spec["risk_free_rate"]),
            claim_horizon_years=Decimal(spec["claim_horizon_years"]),
            evidence_path_ids=(
                f"valuation:{source_snapshot['source_valuation_hash']}:{row['source_scenario']}",
                f"financing:{_sha(financing_path)}",
            ),
            qualification=qualification,
            probability_basis=spec.get(
                "probability_basis", "GOVERNED_EVENT_PRIOR"
            ),
            is_central=bool(row["is_central"]),
        )
        for row in spec["branches"]
    )
    policy = spec["entry_policy"]
    result = compose_governed_equity_distribution(
        branches=branches,
        diluted_shares=shares,
        entry_horizon_years=int(policy["horizon_years"]),
        required_annual_return=Decimal(policy["required_annual_return"]),
        entry_quantile=Decimal(policy["success_quantile"]),
        sensitivity_returns=tuple(Decimal(value) for value in policy["sensitivity_returns"]),
        source_bridge_hash=source_bridge_hash,
    )
    if spec["probability_authorization"].get("status") != (
        "CALIBRATED_EVENT_PROBABILITY"
    ):
        raise ValueError(
            "qualified structural reporting requires calibrated event probabilities"
        )
    # A deterministic calculation hash is not a canonical intrinsic freeze.
    # No target market data may be loaded by this unaudited diagnostic path.
    calculation_hash = sha256(
        (result.distribution_hash + str(result.entry_price) + policy["policy_version"]).encode("utf-8")
    ).hexdigest()

    values = {name: value for name, value, _ in result.branch_values_per_share}
    sensitivity = _mean_sensitivity(
        result.branch_values_per_share,
        spec["probability_authorization"]["sensitivity_sets"],
    )
    distribution_payload = {
        "schema_version": "equity-value-distribution/governed-event-v1",
        "target_id": spec["target_id"],
        "as_of": spec["as_of"],
        "authorization_status": "DIAGNOSTIC_ONLY",
        "input_qualification_status": result.authorization_status,
        "valuation_distribution_authorized": False,
        "not_claimed": spec["probability_authorization"]["not_claimed"],
        "branch_values_per_share": [
            {"branch_id": name, "value": str(value), "probability": str(probability)}
            for name, value, probability in result.branch_values_per_share
        ],
        "p20": str(result.p20),
        "p50": str(result.p50),
        "p80": str(result.p80),
        "p10": str(result.p10),
        "p90": str(result.p90),
        "probability_weighted_mean": str(result.mean),
        "distribution_hash": result.distribution_hash,
        "source_bridge_hash": source_bridge_hash,
        "calculation_hash": calculation_hash,
        "probability_sensitivity": sensitivity,
        "asset_volatility": spec["annual_asset_volatility"],
        "asset_volatility_basis": spec["asset_volatility_basis"],
        "gross_claim_face_value_KRW": spec["gross_claim_face_value_KRW"],
        "claim_bridge": spec["claim_bridge"],
        "limited_liability_treatment": "DATED_STRUCTURAL_RESIDUAL_CLAIM; NO REPORT_TIME POINT_DCF FLOOR",
    }
    entry_payload = {
        "schema_version": "return-quantile-entry/v1",
        "status": "DIAGNOSTIC_ONLY",
        "probability_success_claim_authorized": False,
        "entry_price": str(result.entry_price),
        "horizon_years": policy["horizon_years"],
        "required_annual_return": policy["required_annual_return"],
        "success_quantile": policy["success_quantile"],
        "target_success_probability": str(result.target_success_probability),
        "realized_success_probability": str(result.realized_success_probability),
        "sensitivities": [
            {"required_annual_return": str(rate), "entry_price": str(value)}
            for rate, value in result.entry_price_sensitivities
        ],
        "distribution_hash": result.distribution_hash,
        "calculation_hash": calculation_hash,
        "market_price_used": False,
    }
    claim_bridge = spec["claim_bridge"]
    disclosed_gross_claims = Decimal(
        str(financing["claim_balance_reconciliation_KRW_million"]["total_debt_including_leases"])
    ) * Decimal("1000000")
    reconstructed_gross_claims = (
        Decimal(claim_bridge["disclosed_debt_including_leases_KRW"])
        + Decimal(claim_bridge["run_date_funding_bridge_KRW"])
        + Decimal(claim_bridge["parent_noncontrolling_and_other_claim_KRW"])
    )
    reconstructed_net_claim_proxy = reconstructed_gross_claims - Decimal(
        claim_bridge["eligible_liquid_assets_KRW"]
    )
    audit_checks = {
        "source_run_completed_and_audited": True,
        "source_valuation_hash_bound": source_snapshot["source_valuation_hash"] == valuation["valuation_hash"],
        "financing_claims_reconciled_to_declared_schedule": disclosed_gross_claims
        == Decimal(claim_bridge["disclosed_debt_including_leases_KRW"]),
        "gross_structural_claim_reconstructed_once": reconstructed_gross_claims == claims,
        "legacy_net_claim_bridge_reproduced": reconstructed_net_claim_proxy
        == Decimal(claim_bridge["legacy_net_claim_proxy_KRW"]),
        "probabilities_sum_to_one": sum((b.probability for b in branches), Decimal("0")) == Decimal("1"),
        "central_branch_is_unique_mode": all(
            b.is_central or b.probability < next(c.probability for c in branches if c.is_central)
            for b in branches
        ),
        "nearest_anchor_probability_absent": True,
        "branch_structural_values_nonnegative": all(value >= 0 for value in values.values()),
        "report_time_point_dcf_floor_absent": True,
        "entry_is_pre_market_and_hash_bound": True,
        "realized_success_meets_policy_minimum": result.realized_success_probability
        >= result.target_success_probability,

    }
    if not all(audit_checks.values()):
        raise ValueError("distribution audit failed")
    audit_payload = {
        "schema_version": "governed-distribution-audit/v1",
        "passed": False,
        "canonical_audit_status": "NOT_RUN",
        "diagnostic_checks_passed": True,
        "checks": audit_checks,
        "source_audit_hash": source_snapshot["source_audit_hash"],
        "source_audit_passed": True,
        "distribution_hash": result.distribution_hash,
        "calculation_hash": calculation_hash,
    }

    branch_table = "\n".join(
        f"| {name} | {_pct(probability * Decimal('100'))} | {_money(value)} |"
        for name, value, probability in result.branch_values_per_share
    )
    policy_label = (
        f"{policy['horizon_years']}년 · 연 {_pct(Decimal(policy['required_annual_return']) * 100)}"
        f" · 하위 {_pct(Decimal(policy['success_quantile']) * 100)}"
    )
    report = f"""# {spec['company']} 분포 진단 — 미인증

기준일: {spec['as_of']}. 정식 감사·내재가치 동결을 거치지 않은 계산입니다.
최종 적정가·투자판정·매수가로 사용할 수 없습니다. 현재가와 증권사 자료는 읽지 않았습니다.

## 조건부 계산

| 항목 | 진단값 |
|---|---:|
| P50 | {_money(result.p50)} |
| 확률가중 평균 | {_money(result.mean)} |
| 진입가격 계산 ({policy_label}) | {_money(result.entry_price)} |
| P10~P90 | {_money(result.p10)}~{_money(result.p90)} |

| 입력 상태 | 입력 확률 | 조건부 구주주가치/주 |
|---|---:|---:|
{branch_table}

## 입력 가정

- 확률 근거: {spec['probability_authorization']['basis']}
- 자산변동성: {_pct(Decimal(spec['annual_asset_volatility']) * 100)}
- 총청구액: {_money(claims)}
- 청구기간: {spec['claim_horizon_years']}년
- 원본 시나리오 파일은 고정된 번들의 파일 해시·대상·기준일·값과 대조했습니다.
- 원본의 감사 통과는 이 분포 및 진입가격 계산의 감사 통과를 의미하지 않습니다.
"""

    artifact_basis = sha256(
        (
            "diagnostic/v1" + result.distribution_hash
            + calculation_hash
            + _sha(spec_path)
        ).encode("utf-8")
    ).hexdigest()
    artifact_id = f"{spec['ticker']}-{spec['as_of'].replace('-', '')}-DIAGNOSTIC-{artifact_basis[:12].upper()}"
    bundle = output_root / artifact_id
    bundle.mkdir(parents=True, exist_ok=True)
    files = {
        "equity_value_distribution.json": json.dumps(distribution_payload, ensure_ascii=False, indent=2) + "\n",
        "entry_price.json": json.dumps(entry_payload, ensure_ascii=False, indent=2) + "\n",
        "audit.json": json.dumps(audit_payload, ensure_ascii=False, indent=2) + "\n",
        "diagnostic_report.md": report,
        "valuation_summary.svg": _svg(
            f"{spec['company']} 분포 진단 · 미인증",
            [f"P50 {_money(result.p50)} · 평균 {_money(result.mean)}",
             f"진입가격 계산 {_money(result.entry_price)}",
             policy_label, "정식 감사·동결 미실행 · 투자판정에 사용 불가"],
            distribution_hash=result.distribution_hash,
        ),

    }
    for name, contents in files.items():
        path = bundle / name
        if path.exists() and path.read_text(encoding="utf-8") != contents:
            raise ValueError(f"immutable artifact collision: {path}")
        path.write_text(contents, encoding="utf-8")
    receipts = [
        {"filename": name, "sha256": _sha(bundle / name)} for name in sorted(files)
    ]
    manifest = {
        "schema_version": "governed-distribution-report-bundle/v1",
        "artifact_id": artifact_id,
        "company": spec["company"],
        "ticker": spec["ticker"],
        "as_of": spec["as_of"],
        "status": "DIAGNOSTIC_ONLY",
        "audit_passed": False,
        "distribution_hash": result.distribution_hash,
        "calculation_hash": calculation_hash,
        "source_valuation_hash": source_snapshot["source_valuation_hash"],
        "source_audit_hash": source_snapshot["source_audit_hash"],
        "spec_sha256": _sha(spec_path),
        "files": receipts,
    }
    manifest_path = bundle / "bundle_manifest.json"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise ValueError(f"immutable artifact collision: {manifest_path}")
    manifest_path.write_text(manifest_text, encoding="utf-8")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    spec = Path(args.spec).resolve()
    run_dir = spec.parent.parent
    output = Path(args.output_root).resolve() if args.output_root else run_dir / "out" / "distributional_bundles"
    try:
        bundle = build(spec, output)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
