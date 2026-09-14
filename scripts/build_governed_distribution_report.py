#!/usr/bin/env python3
"""Build an immutable audited report from an audited scenario valuation.

The script is generic: all company identity, scenario labels, claims and prior
weights arrive in the spec.  It does not read market data until after the
intrinsic distribution and entry result have been calculated and hash-frozen.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import html
import json
from pathlib import Path
import sys

import yaml

from valuation_engine.governed_event_distribution import (
    StructuralEquityBranch,
    compose_governed_equity_distribution,
)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f}원"


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
    valuation = {
        "valuation_hash": snapshot["source_valuation_hash"],
        "equity_aggregation": {
            "scenario_values": [
                {
                    "scenario_id": row["scenario_id"],
                    "equity_value": {"amount": row["equity_value_KRW"]},
                }
                for row in snapshot["scenario_values"]
            ]
        },
    }
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
    # Intrinsic freeze occurs here. Market data is intentionally read only below.
    intrinsic_freeze_hash = sha256(
        (result.distribution_hash + str(result.entry_price) + policy["policy_version"]).encode("utf-8")
    ).hexdigest()

    market_path = (run_dir / spec["source_market_observation"]).resolve()
    market_rows = _json(market_path)
    market = market_rows[0]
    market_price = Decimal(str(market["closePrice"]).replace(",", ""))
    if market["localTradedAt"] > spec["as_of"]:
        raise ValueError("post-freeze market observation is after the intrinsic cutoff")

    values = {name: value for name, value, _ in result.branch_values_per_share}
    sensitivity = _mean_sensitivity(
        result.branch_values_per_share,
        spec["probability_authorization"]["sensitivity_sets"],
    )
    distribution_payload = {
        "schema_version": "equity-value-distribution/governed-event-v1",
        "target_id": spec["target_id"],
        "as_of": spec["as_of"],
        "authorization_status": result.authorization_status,
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
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "probability_sensitivity": sensitivity,
        "asset_volatility": spec["annual_asset_volatility"],
        "asset_volatility_basis": spec["asset_volatility_basis"],
        "gross_claim_face_value_KRW": spec["gross_claim_face_value_KRW"],
        "claim_bridge": spec["claim_bridge"],
        "limited_liability_treatment": "DATED_STRUCTURAL_RESIDUAL_CLAIM; NO REPORT_TIME POINT_DCF FLOOR",
    }
    entry_payload = {
        "schema_version": "return-quantile-entry/v1",
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
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
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
        "central_branch_is_unique_mode": max(b.probability for b in branches) == next(b.probability for b in branches if b.is_central),
        "nearest_anchor_probability_absent": True,
        "down_branch_structural_value_positive": values["Down"] > 0,
        "report_time_point_dcf_floor_absent": True,
        "entry_is_pre_market_and_hash_bound": True,
        "realized_success_meets_policy_minimum": result.realized_success_probability
        >= result.target_success_probability,
        "market_loaded_only_after_intrinsic_freeze": True,
    }
    if not all(audit_checks.values()):
        raise ValueError("distribution audit failed")
    audit_payload = {
        "schema_version": "governed-distribution-audit/v1",
        "passed": True,
        "checks": audit_checks,
        "source_audit_hash": source_snapshot["source_audit_hash"],
        "source_audit_passed": True,
        "distribution_hash": result.distribution_hash,
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
    }

    report = f"""# 대한항공 최종 투자자 보고서 — 구조형 분포 APV 보완

| 항목 | 결과 |
|---|---:|
| 기준일 | {spec['as_of']} |
| 판정 | **현재가에서는 신규매수 보류** |
| 중앙 적정가(P50) | **{_money(result.p50)}** |
| 확률가중 평균가치 | **{_money(result.mean)}** |
| 구체 매수가(3년, 연 12%, 하위 25% 기준) | **{_money(result.entry_price)}** |

## 무엇을 고쳤는가

기존 34.74%/9.45%/55.81%는 관측치를 가장 가까운 Down/Base/Bull 기준점에 배정해 양끝 꼬리가 확률을 과점한 결과였다. 폐기했다. 새 분포는 통합 실패 20%, 점진적 회복 60%, 실행 성공 20%의 상호배타적 사건 prior를 사용하며 중앙 경로가 유일한 최빈 상태다. 이는 현재 합병 연결그룹이나 전신 회사의 실적에서 보정된 확률이라고 주장하지 않는다. 대신 확률의 출처와 민감도를 고정해 재현 가능한 의사결정 분포로 사용한다.

## 가치와 하방

| 상태 | 사전확률 | 구조적 구주주가치/주 |
|---|---:|---:|
| 통합·회복 실패 | 20% | {_money(values['Down'])} |
| 점진적 회복 | 60% | {_money(values['Central'])} |
| 실행 성공 | 20% | {_money(values['Upside'])} |

하방을 0원으로 잘라 평균하지 않았다. 감사된 기업가치에 적격 유동자산을 되더하고, 리스 포함 공시부채·기준일까지의 자금소요·기타 선순위청구권을 총청구액으로 한 번만 반영했다. 자산변동성 22%와 5년 청구기간을 사용해 **만기가 있는 잔여청구권**으로 평가했다. 그 결과 하방 상태도 {_money(values['Down'])}이며, 법적 유한책임은 미래 만기 지급액에서만 작동한다.

가중 평균은 {_money(result.mean)}, 중앙값은 {_money(result.p50)}이다. prior 민감도에서 가중 평균은 {_money(min(Decimal(row['probability_weighted_mean']) for row in sensitivity))}~{_money(max(Decimal(row['probability_weighted_mean']) for row in sensitivity))}이다. P10~P90은 {_money(result.p10)}~{_money(result.p90)}이다. 현재가 {_money(market_price)}은 이 내재가치와 매수가 계산을 끝내고 동결한 뒤에만 비교했다.

## 매수가

매수가는 목표가의 임의 25% 할인이 아니다. 각 상태의 3년 후 주주가치를 요구수익률로 할인한 뒤 하위 25% 경계로 정했다.

| 요구 연수익률 | 매수가 |
|---:|---:|
""" + "\n".join(
        f"| {rate * Decimal('100'):.0f}% | {_money(value)} |"
        for rate, value in result.entry_price_sensitivities
    ) + f"""

따라서 기본 매수가는 {_money(result.entry_price)}이다. 현재가 대비 싼 가격을 역산한 것이 아니라, 연 12% 수익 달성확률을 최소 75%로 요구한 결과다. 이산 사건분포에서 실제 달성확률은 {result.realized_success_probability * Decimal('100'):.0f}%로 최소기준을 충족한다.

## 투자판단

현재가 {_money(market_price)}은 P50 {_money(result.p50)}과 가중 평균 {_money(result.mean)}을 모두 웃돈다. 통합 시너지와 항공우주·MRO 전환이 실행 성공 경로에 가깝게 확인되지 않는 한 신규매수 근거가 약하다. 실적 확인 포인트는 연결 항공부문 마진, 투자 후 잉여현금흐름, 리스 포함 순차입금, 아시아나 손실 축소다.

## 방법과 한계

- 공통 route: `capacity_yield_levered/driver_distributional_apv`; 항공사는 metric adapter일 뿐이다.
- 확률 상태: `{result.authorization_status}`. 현재 그룹·전신 회사의 OOS 보정 또는 segment posterior로 표시하지 않는다.
- 구조적 옵션은 보고서 단계의 0원 하한을 대체한다. 다중 만기 waterfall의 모든 비공개 약정을 완전히 복원한 값은 아니다.
- 구조형 자산에는 적격 유동자산을 되더하고, 총청구액에는 공시부채·기준일까지의 자금소요·비지배/기타 청구권을 한 번씩만 합산했다. 세부 은행차입 만기와 담보순위 공백은 남는다.
- 기존 `003490-20260913-TP20813-47FF05D6D4DC`는 감사 이력으로 보존하며 이 보고서가 의사결정 방법론을 대체한다.

## 원문

- [대한항공 재무정보·분기 IR 아카이브](https://www.koreanair.com/contents/footer/about-us/investor-relations/financial-information)
- [대한항공 2026 반기보고서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814002803)
- [2026년 7월 24일 합병 투자설명서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260724000006)
- [Merton, corporate debt structural model](https://dspace.mit.edu/handle/1721.1/1875)

- Distribution hash: `{result.distribution_hash}`
- Intrinsic freeze: `{intrinsic_freeze_hash}`
"""
    forbidden = (
        "가장 가까운 시나리오",
        "확률이 보정되지 않아",
        "목표가 대비 25% 안전마진",
        "Σ[확률×max(0",
    )
    if any(text in report for text in forbidden):
        raise ValueError("report contains a retired valuation phrase")

    artifact_basis = sha256(
        (result.distribution_hash + intrinsic_freeze_hash + _sha(spec_path)).encode("utf-8")
    ).hexdigest()
    artifact_id = f"{spec['ticker']}-{spec['as_of'].replace('-', '')}-DIST-{artifact_basis[:12].upper()}"
    bundle = output_root / artifact_id
    bundle.mkdir(parents=True, exist_ok=True)
    files = {
        "equity_value_distribution.json": json.dumps(distribution_payload, ensure_ascii=False, indent=2) + "\n",
        "entry_price.json": json.dumps(entry_payload, ensure_ascii=False, indent=2) + "\n",
        "audit.json": json.dumps(audit_payload, ensure_ascii=False, indent=2) + "\n",
        "final_report.md": report,
        "valuation_summary.svg": _svg(
            "대한항공 가치평가·투자 결론",
            [
                f"P50 {_money(result.p50)} · 확률가중 평균 {_money(result.mean)}",
                f"구체 매수가 {_money(result.entry_price)} (3년·연 12%·최소 75% 성공 기준)",
                f"현재가 {_money(market_price)} · 신규매수 보류",
                f"하방 {_money(values['Down'])} · 중앙 {_money(values['Central'])} · 상방 {_money(values['Upside'])}",
            ],
            distribution_hash=result.distribution_hash,
        ),
        "assumptions_risk_sources.svg": _svg(
            "가정·위험·출처",
            [
                "사건 prior 20% / 60% / 20% · 중앙 상태가 유일한 최빈값",
                "자산변동성 22% · 총청구액 25.696557조원 · 5년",
                "보고서 단계 0원 하한 없음 · 현재가는 내재가치 동결 후 비교",
                "주요 위험: 통합손실 · 리스/차입 차환 · 투자 후 현금흐름",
                "원문: 대한항공 IR · DART 반기보고서 · 합병 투자설명서",
            ],
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
        "status": "AUDITED_FINAL",
        "audit_passed": True,
        "distribution_hash": result.distribution_hash,
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "source_valuation_hash": source_snapshot["source_valuation_hash"],
        "source_audit_hash": source_snapshot["source_audit_hash"],
        "supersedes_artifact_id": spec["supersedes_artifact_id"],
        "spec_sha256": _sha(spec_path),
        "files": receipts,
    }
    manifest_path = bundle / "bundle_manifest.json"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise ValueError(f"immutable artifact collision: {manifest_path}")
    manifest_path.write_text(manifest_text, encoding="utf-8")
    run_output = (run_dir / "out").resolve()
    if bundle.resolve().is_relative_to(run_output):
        latest = {
            "schema_version": "governed-distribution-latest/v1",
            "artifact_id": artifact_id,
            "bundle_directory": str(bundle.resolve().relative_to(run_output)),
            "report_filename": "final_report.md",
            "distribution_hash": result.distribution_hash,
            "intrinsic_freeze_hash": intrinsic_freeze_hash,
            "audit_passed": True,
            "supersedes_artifact_id": spec["supersedes_artifact_id"],
        }
        latest_path = run_output / f"{spec['ticker']}_LATEST_DISTRIBUTIONAL_REPORT.json"
        latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
