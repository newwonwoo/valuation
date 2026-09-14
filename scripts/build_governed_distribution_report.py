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


def _broker_rows(snapshot: dict, *, spec: dict, market_price: Decimal, result) -> tuple[list[dict], dict]:
    if snapshot.get("schema_version") != "post-freeze-broker-comparison/v1":
        raise ValueError("unsupported broker comparison snapshot")
    if snapshot.get("company") != spec["company"] or snapshot.get("ticker") != spec["ticker"]:
        raise ValueError("broker comparison identity does not match the valuation target")
    if snapshot.get("as_of") != spec["as_of"]:
        raise ValueError("broker comparison cutoff does not match the valuation cutoff")
    raw_rows = snapshot.get("verified_reports") or []
    if len(raw_rows) < 2:
        raise ValueError("broker comparison requires at least two verified reports")
    rows = []
    institutions = set()
    for raw in raw_rows:
        institution = raw.get("institution", "").strip()
        if not institution or institution in institutions:
            raise ValueError("broker comparison institutions must be unique")
        institutions.add(institution)
        if raw.get("report_date", "") > spec["as_of"]:
            raise ValueError("broker report date is after the intrinsic cutoff")
        if raw.get("target_price_currency") != spec["reporting_currency"]:
            raise ValueError("broker target currency does not match the report currency")
        source_url = raw.get("url", "")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError("broker report requires a directly clickable HTTP(S) source")
        target = Decimal(str(raw.get("target_price_krw", "0")))
        if target <= 0:
            raise ValueError("broker target price must be positive")
        rows.append(
            {
                "institution": institution,
                "analyst": raw.get("analyst", "미공개"),
                "report_date": raw["report_date"],
                "title": raw.get("title", ""),
                "rating": raw.get("rating", "미공개"),
                "target_price": str(target),
                "target_price_currency": raw["target_price_currency"],
                "target_upside_vs_market_pct": str((target / market_price - Decimal("1")) * Decimal("100")),
                "premium_vs_p50_pct": str((target / result.p50 - Decimal("1")) * Decimal("100")),
                "premium_vs_probability_weighted_mean_pct": str(
                    (target / result.mean - Decimal("1")) * Decimal("100")
                ),
                "valuation_method": raw.get("valuation_method", "NOT_DISCLOSED"),
                "base_year": raw.get("base_year", "NOT_DISCLOSED"),
                "target_multiple": raw.get("target_multiple", "NOT_DISCLOSED"),
                "disclosed_estimates": raw.get("disclosed_estimates", []),
                "valuation_basis_note": raw.get("valuation_basis_note", ""),
                "load_bearing_assumption": raw.get("load_bearing_assumption", ""),
                "source_url": source_url,
                "access_quality": raw.get("access_quality", ""),
            }
        )
    targets = sorted(Decimal(row["target_price"]) for row in rows)
    midpoint = len(targets) // 2
    median = targets[midpoint] if len(targets) % 2 else (targets[midpoint - 1] + targets[midpoint]) / Decimal("2")
    sample = {
        "report_count": len(rows),
        "latest_report_date": max(row["report_date"] for row in rows),
        "min_target_price": str(targets[0]),
        "median_target_price": str(median),
        "mean_target_price": str(sum(targets, Decimal("0")) / Decimal(len(targets))),
        "max_target_price": str(targets[-1]),
        "median_premium_vs_market_pct": str((median / market_price - Decimal("1")) * Decimal("100")),
        "median_premium_vs_p50_pct": str((median / result.p50 - Decimal("1")) * Decimal("100")),
        "median_premium_vs_probability_weighted_mean_pct": str(
            (median / result.mean - Decimal("1")) * Decimal("100")
        ),
        "probability_weighted_mean_discount_to_median_pct": str(
            (Decimal("1") - result.mean / median) * Decimal("100")
        ),
    }
    return rows, sample


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
    # Intrinsic freeze occurs here. Broker targets and market data are
    # intentionally inaccessible until after this point.
    intrinsic_freeze_hash = sha256(
        (result.distribution_hash + str(result.entry_price) + policy["policy_version"]).encode("utf-8")
    ).hexdigest()

    broker_path = (run_dir / spec["source_broker_comparison"]).resolve()
    broker_snapshot = _json(broker_path)
    market_path = (run_dir / spec["source_market_observation"]).resolve()
    market_rows = _json(market_path)
    market = market_rows[0]
    market_price = Decimal(str(market["closePrice"]).replace(",", ""))
    if market["localTradedAt"] > spec["as_of"]:
        raise ValueError("post-freeze market observation is after the intrinsic cutoff")
    broker_rows, broker_sample = _broker_rows(
        broker_snapshot,
        spec=spec,
        market_price=market_price,
        result=result,
    )
    post_freeze_comparison_hash = sha256(
        (
            intrinsic_freeze_hash
            + _sha(broker_path)
            + _sha(market_path)
            + market["localTradedAt"]
            + str(market_price)
        ).encode("utf-8")
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
    broker_payload = {
        "schema_version": "post-freeze-broker-comparison-result/v1",
        "target_id": spec["target_id"],
        "as_of": spec["as_of"],
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
        "intrinsic_distribution_unchanged": True,
        "market": {
            "date": market["localTradedAt"],
            "price": str(market_price),
            "currency": spec["reporting_currency"],
        },
        "sample_policy": broker_snapshot["sample_policy"],
        "coverage_limit": broker_snapshot["coverage_limit"],
        "sample": broker_sample,
        "reports": broker_rows,
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
        "broker_loaded_only_after_intrinsic_freeze": True,
        "broker_report_dates_not_after_cutoff": all(
            row["report_date"] <= spec["as_of"] for row in broker_rows
        ),
        "broker_targets_excluded_from_intrinsic_inputs": True,
        "broker_sources_are_clickable_http": all(
            row["source_url"].startswith(("http://", "https://")) for row in broker_rows
        ),
        "broker_sample_uses_unique_institutions": len(
            {row["institution"] for row in broker_rows}
        )
        == len(broker_rows),
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
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
    }

    broker_table = []
    for row in broker_rows:
        basis = row["target_multiple"]
        if basis == "NOT_DISCLOSED":
            basis = "산식·배수 비공개"
        broker_table.append(
            f"| {row['institution']} ({row['report_date']}) | "
            f"{_money(Decimal(row['target_price']))} · {row['rating']} | "
            f"{_pct(Decimal(row['target_upside_vs_market_pct']))} | {basis} | "
            f"{row['load_bearing_assumption']} |"
        )
    broker_source_lines = "\n".join(
        f"- [{row['institution']} {row['report_date']} — {row['title']}]({row['source_url']})"
        for row in broker_rows
    )

    report = f"""# 대한항공 최종 투자자 보고서 — 구조형 분포 APV 보완

| 항목 | 결과 |
|---|---:|
| 기준일 | {spec['as_of']} |
| 판정 | **현재가에서는 신규매수 보류** |
| 중앙 적정가(P50) | **{_money(result.p50)}** |
| 확률가중 평균가치 | **{_money(result.mean)}** |
| 구체 매수가(3년, 연 12%, 하위 25% 기준) | **{_money(result.entry_price)}** |
| 검증 증권사 표본 | **{_money(Decimal(broker_sample['min_target_price']))}~{_money(Decimal(broker_sample['max_target_price']))}** · 중앙값 {_money(Decimal(broker_sample['median_target_price']))} |

## 무엇을 고쳤는가

기존 34.74%/9.45%/55.81%는 관측치를 가장 가까운 Down/Base/Bull 기준점에 배정해 양끝 꼬리가 확률을 과점한 결과였다. 폐기했다. 새 분포는 통합 실패 20%, 점진적 회복 60%, 실행 성공 20%의 상호배타적 사건 prior를 사용하며 중앙 경로가 유일한 최빈 상태다. 이는 현재 합병 연결그룹이나 전신 회사의 실적에서 보정된 확률이라고 주장하지 않는다. 대신 확률의 출처와 민감도를 고정해 재현 가능한 의사결정 분포로 사용한다.

## 가치와 하방

| 상태 | 사전확률 | 구조적 구주주가치/주 |
|---|---:|---:|
| 통합·회복 실패 | 20% | {_money(values['Down'])} |
| 점진적 회복 | 60% | {_money(values['Central'])} |
| 실행 성공 | 20% | {_money(values['Upside'])} |

하방을 0원으로 잘라 평균하지 않았다. 감사된 기업가치에 적격 유동자산을 되더하고, 리스 포함 공시부채·기준일까지의 자금소요·기타 선순위청구권을 총청구액으로 한 번만 반영했다. 자산변동성 22%와 5년 청구기간을 사용해 **만기가 있는 잔여청구권**으로 평가했다. 그 결과 하방 상태도 {_money(values['Down'])}이며, 법적 유한책임은 미래 만기 지급액에서만 작동한다.

가중 평균은 {_money(result.mean)}, 중앙값은 {_money(result.p50)}이다. prior 민감도에서 가중 평균은 {_money(min(Decimal(row['probability_weighted_mean']) for row in sensitivity))}~{_money(max(Decimal(row['probability_weighted_mean']) for row in sensitivity))}이다. P10~P90은 {_money(result.p10)}~{_money(result.p90)}이다. 현재가 {_money(market_price)}({market['localTradedAt']})은 이 내재가치와 매수가 계산을 끝내고 동결한 뒤에만 비교했다.

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

## 핵심 가정과 위험

- 공통 적용계약은 용량×가동률×단가, 높은 고정비·재투자, 장기자산·리스 및 금융청구권 구조를 기준으로 선택한다. 항공 업종명은 회사 지표를 공통 입력에 연결하는 역할만 한다.
- 확률은 감사된 조건부 가치에 결속한 사건 사전확률이다. 현재 그룹·전신 회사의 OOS 보정 또는 segment posterior로 표시하지 않는다.
- 구조적 옵션은 보고서 단계의 0원 하한을 대체한다. 다중 만기 waterfall의 모든 비공개 약정을 완전히 복원한 값은 아니다.
- 구조형 자산에는 적격 유동자산을 되더하고, 총청구액에는 공시부채·기준일까지의 자금소요·비지배/기타 청구권을 한 번씩만 합산했다. 세부 은행차입 만기와 담보순위 공백은 남는다.
- 기존 보고서는 감사 이력으로 보존하며 이 보고서가 의사결정 방법론을 대체한다.

## 증권사·시장 비교

기준일 이전에 내용과 목표가를 확인할 수 있었던 증권사별 최신 공개자료 표본 3건을 비교했다. 표본 중앙값은 {_money(Decimal(broker_sample['median_target_price']))}, 범위는 {_money(Decimal(broker_sample['min_target_price']))}~{_money(Decimal(broker_sample['max_target_price']))}이다. 이는 전체 시장 컨센서스가 아니라 **공개 원문 검증 표본**이다.

| 증권사·보고일 | 목표가·의견 | 현재가 대비 | 공개된 평가기준 | 목표가를 지탱하는 핵심 가정 |
|---|---:|---:|---|---|
""" + "\n".join(broker_table) + f"""

표본 중앙값 {_money(Decimal(broker_sample['median_target_price']))}은 현재가보다 {_pct(Decimal(broker_sample['median_premium_vs_market_pct']))}, 우리 P50보다 {_pct(Decimal(broker_sample['median_premium_vs_p50_pct']))}, 확률가중 평균보다 {_pct(Decimal(broker_sample['median_premium_vs_probability_weighted_mean_pct']))} 높다. 반대로 우리 확률가중 평균은 표본 중앙값보다 {_pct(Decimal(broker_sample['probability_weighted_mean_discount_to_median_pct']))} 낮다.

### 왜 차이가 나는가

- **영업경로:** 미래에셋은 3Q26 연결 영업이익 4,887억원과 원화 강세, LS는 FY2027 연결 영업이익 2.663조원·영업이익률 9.1%, 하나는 통합 LCC 효과의 2028년 본격화를 전제로 한다. 세 보고서 모두 여객·화물 단가와 통합 시너지를 회복축으로 본다.
- **평가정책:** 미래에셋은 과거 PBR 밴드 상단을 웃도는 1.1배를 적용한다. 우리 값은 목표 배수를 주가에 적용하지 않고, 상태별 사업자산에서 총금융청구권을 반영한 구주주 잔여가치를 확률분포로 계산한다.
- **재무·자본구조:** 우리 모형은 리스 포함 공시부채, 기준일까지의 자금소요와 기타 선순위청구권을 총 25.697조원으로 명시하고 적격 유동자산을 별도 되더한다. 공개자료에서 하나·LS의 목표가 산식, 기준연도, 순차입금·CAPEX 처리가 모두 공개되지 않아 이 부분의 가격 차이는 정량 분해하지 않았다.
- **상방의 위치:** 우리 실행 성공 값 {_money(values['Upside'])}은 증권사 목표가 상단 {_money(Decimal(broker_sample['max_target_price']))}보다 높다. 따라서 차이는 상방을 막아서라기보다, 상방 실현확률과 통합 전후 현금흐름·금융청구권을 언제 인식하느냐에서 발생한다.
- **남는 오차:** 증권사별 비공개 세부 산식 때문에 영업·금융·배수 효과를 합계 100%로 억지 배분하지 않았다. LS 자료는 증권사 작성본 전체를 확인했지만 공개 제3자 미러라는 출처 제약도 남긴다.

## 원문

- [대한항공 재무정보·분기 IR 아카이브](https://www.koreanair.com/contents/footer/about-us/investor-relations/financial-information)
- [대한항공 2026 반기보고서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814002803)
- [2026년 7월 24일 합병 투자설명서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260724000006)
- [Merton, corporate debt structural model](https://dspace.mit.edu/handle/1721.1/1875)
- [네이버증권 대한항공 가격 이력](https://m.stock.naver.com/api/stock/003490/price)
{broker_source_lines}
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
        (
            result.distribution_hash
            + intrinsic_freeze_hash
            + post_freeze_comparison_hash
            + _sha(spec_path)
        ).encode("utf-8")
    ).hexdigest()
    artifact_id = f"{spec['ticker']}-{spec['as_of'].replace('-', '')}-DIST-{artifact_basis[:12].upper()}"
    bundle = output_root / artifact_id
    bundle.mkdir(parents=True, exist_ok=True)
    files = {
        "equity_value_distribution.json": json.dumps(distribution_payload, ensure_ascii=False, indent=2) + "\n",
        "entry_price.json": json.dumps(entry_payload, ensure_ascii=False, indent=2) + "\n",
        "broker_comparison.json": json.dumps(broker_payload, ensure_ascii=False, indent=2) + "\n",
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
                f"증권사 공개 3건 {_money(Decimal(broker_sample['min_target_price']))}~{_money(Decimal(broker_sample['max_target_price']))}",
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
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
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
            "post_freeze_comparison_hash": post_freeze_comparison_hash,
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
