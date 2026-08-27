from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "config" / "sanil_live_snapshot.yaml"
DEFAULT_REGISTER = ROOT / "docs" / "SANIL_RISK_SOURCE_REGISTER.md"
BENCHMARK_CODE = "KS11"
BENCHMARK_ID = "FDR_KOSPI_KS11"
START_DATE = "2024-07-29"
END_DATE = "2026-08-25"
PRICE_SOURCE_REF = "https://finance.naver.com/"
PROVIDER_REF = "https://github.com/FinanceData/FinanceDataReader"

PEER_CODES = {
    "LS_ELECTRIC_010120": "010120",
    "HYOSUNG_HEAVY_INDUSTRIES_298040": "298040",
    "HD_HYUNDAI_ELECTRIC_267260": "267260",
    "ILJIN_ELECTRIC_103590": "103590",
    "TAIHAN_CABLE_001440": "001440",
    "CHERYONG_ELECTRIC_033100": "033100",
    "KWANGMYUNG_ELECTRIC_017040": "017040",
    "CHEIL_ELECTRIC_199820": "199820",
}


@dataclass(frozen=True)
class OLSResult:
    beta: float
    standard_error: float
    alpha: float
    r_squared: float
    observations: int
    start_date: str
    end_date: str
    series_hash: str


def _close_series(frame: pd.DataFrame, label: str) -> pd.Series:
    if frame.empty:
        raise RuntimeError(f"price provider returned no rows for {label}")
    close_column = next(
        (column for column in ("Close", "Adj Close", "종가") if column in frame.columns),
        None,
    )
    if close_column is None:
        raise RuntimeError(
            f"price provider returned no close column for {label}: {list(frame.columns)}"
        )
    series = frame[close_column].astype(float).replace(0.0, np.nan).dropna()
    series.index = pd.to_datetime(series.index).tz_localize(None)
    start = pd.Timestamp(START_DATE)
    cutoff = pd.Timestamp(END_DATE)
    series = series.loc[(series.index >= start) & (series.index <= cutoff)]
    if len(series) < 60:
        raise RuntimeError(f"price history is too short for {label}: {len(series)}")
    if series.index.max() > cutoff:
        raise RuntimeError(f"price series for {label} exceeds the frozen cutoff")
    series.name = label
    return series


def _returns(series: pd.Series, frequency: str) -> pd.Series:
    if frequency == "weekly":
        sampled = series.resample("W-FRI").last().dropna()
    elif frequency == "daily":
        sampled = series
    else:
        raise ValueError(f"unsupported return frequency: {frequency}")
    return (
        sampled.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def _ols(stock_returns: pd.Series, market_returns: pd.Series) -> OLSResult:
    joined = pd.concat((stock_returns, market_returns), axis=1, join="inner").dropna()
    if len(joined) < 40:
        raise RuntimeError(f"insufficient aligned return observations: {len(joined)}")
    y = joined.iloc[:, 0].to_numpy(dtype=float)
    x = joined.iloc[:, 1].to_numpy(dtype=float)
    design = np.column_stack((np.ones(len(x)), x))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha, beta = coefficients
    fitted = design @ coefficients
    residuals = y - fitted
    dof = len(x) - 2
    residual_variance = float((residuals @ residuals) / dof)
    centered_x = x - x.mean()
    sxx = float(centered_x @ centered_x)
    if sxx <= 0:
        raise RuntimeError("benchmark return variance is zero")
    standard_error = float(np.sqrt(residual_variance / sxx))
    total = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - float((residuals @ residuals) / total) if total > 0 else 0.0
    canonical = [
        (
            index.strftime("%Y-%m-%d"),
            round(float(row.iloc[0]), 12),
            round(float(row.iloc[1]), 12),
        )
        for index, row in joined.iterrows()
    ]
    return OLSResult(
        beta=float(beta),
        standard_error=standard_error,
        alpha=float(alpha),
        r_squared=r_squared,
        observations=len(joined),
        start_date=joined.index.min().strftime("%Y-%m-%d"),
        end_date=joined.index.max().strftime("%Y-%m-%d"),
        series_hash=sha256(
            json.dumps(canonical, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def _fetch_close(code: str) -> pd.Series:
    return _close_series(fdr.DataReader(code, START_DATE, END_DATE), code)


def _fetch_beta(code: str, market_close: pd.Series) -> tuple[OLSResult, OLSResult]:
    close = _fetch_close(code)
    return (
        _ols(_returns(close, "weekly"), _returns(market_close, "weekly")),
        _ols(_returns(close, "daily"), _returns(market_close, "daily")),
    )


def _refresh_snapshot(snapshot_path: Path) -> tuple[dict, list[dict]]:
    payload = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Sanil snapshot root must be a mapping")
    if str(payload.get("cutoff")) != END_DATE:
        raise RuntimeError(
            f"Beta cutoff {END_DATE} must equal Sanil snapshot cutoff {payload.get('cutoff')}"
        )
    risk = payload["risk"]
    market_close = _fetch_close(BENCHMARK_CODE)
    results: list[dict] = []
    for level_name, rows in risk["peers"].items():
        for row in rows:
            peer_id = str(row["peer_id"])
            try:
                code = PEER_CODES[peer_id]
            except KeyError as exc:
                raise RuntimeError(f"missing Korean listing code for {peer_id}") from exc
            weekly, daily = _fetch_beta(code, market_close)
            capital_source = str(
                row.get("capital_source_ref") or row.get("source_ref") or ""
            )
            row.update(
                {
                    "levered_beta": round(weekly.beta, 8),
                    "beta_standard_error": round(weekly.standard_error, 8),
                    "source_ref": PRICE_SOURCE_REF,
                    "provider_ref": PROVIDER_REF,
                    "capital_source_ref": capital_source,
                    "estimation_method": (
                        "common KOSPI weekly OLS with intercept; daily OLS diagnostic"
                    ),
                    "observations": weekly.observations,
                    "start_date": weekly.start_date,
                    "end_date": weekly.end_date,
                    "price_series_hash": weekly.series_hash,
                    "daily_beta_diagnostic": round(daily.beta, 8),
                    "daily_beta_standard_error": round(daily.standard_error, 8),
                    "r_squared": round(weekly.r_squared, 8),
                }
            )
            results.append(
                {
                    "level": level_name,
                    "peer_id": peer_id,
                    "code": code,
                    "weekly": weekly,
                    "daily": daily,
                    "capital_source_ref": capital_source,
                }
            )
    latest = max(item["weekly"].end_date for item in results)
    if latest > END_DATE:
        raise RuntimeError(f"computed Beta end date exceeds cutoff: {latest}")
    risk.update(
        {
            "benchmark_id": BENCHMARK_ID,
            "return_frequency": "weekly",
            "estimation_window_months": 25,
            "as_of": END_DATE,
            "beta_observation_end": latest,
            "beta_source_ref": PRICE_SOURCE_REF,
            "beta_provider_ref": PROVIDER_REF,
            "beta_methodology": (
                "Common KOSPI benchmark; weekly OLS with intercept; daily OLS retained "
                "as a frequency/non-synchronous-trading diagnostic"
            ),
        }
    )
    return payload, results


def _render_register(payload: dict, results: Iterable[dict]) -> str:
    risk = payload["risk"]
    lines = [
        "# 산일전기 Beta·WACC 위험자료 원장",
        "",
        f"- 가치평가 기준일: {risk['as_of']}",
        f"- 회귀 관측 종료일: {risk['beta_observation_end']}",
        "- 주가 원자료: 공개 한국 주가 시계열(네이버 금융 경로)",
        f"- 수집기: {PROVIDER_REF}",
        f"- 공통 benchmark: {risk['benchmark_id']}",
        "- 주 추정치: 주간 수익률 OLS(상수항 포함)",
        "- 교차검증: 일간 OLS를 진단값으로 함께 보존",
        "- 모든 peer에 동일 기간·benchmark·빈도를 적용하고 회귀 표준오차를 저장한다.",
        "- Debt / Equity 원장은 Beta 시계열과 분리해 `capital_source_ref`로 추적한다.",
        "",
        "## L1→L4 회귀 결과",
        "",
        "| Level | Peer | Code | Weekly Beta | Std. Error | Obs. | Daily Beta | R² | Series hash |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in results:
        weekly: OLSResult = item["weekly"]
        daily: OLSResult = item["daily"]
        lines.append(
            f"| {item['level']} | {item['peer_id']} | {item['code']} | "
            f"{weekly.beta:.4f} | {weekly.standard_error:.4f} | {weekly.observations} | "
            f"{daily.beta:.4f} | {weekly.r_squared:.4f} | `{weekly.series_hash}` |"
        )
    lines.extend(
        (
            "",
            "## WACC 거시 입력",
            "",
            "| 항목 | 동결값 | 처리 |",
            "|---|---:|---|",
            f"| 원화 무위험금리 | {float(risk['risk_free_rate']):.3%} | 원화 장기국채 입력 |",
            f"| Mature-market ERP | {float(risk['mature_market_erp']):.3%} | Country Risk와 분리 |",
            f"| 한국 Country Risk Premium | {float(risk['country_risk_premium']):.3%} | 노출계수와 별도 적용 |",
            f"| Country-risk lambda | {float(risk['country_risk_lambda']):.2f} | 미국 매출과 한국 생산·법인 노출을 분리한 판단값 |",
            f"| 세전 한계차입비용 | {float(risk['pre_tax_cost_of_debt']):.3%} | 현재 원화 차입 benchmark |",
            f"| 장기 목표 자본구조 | Equity {float(risk['target_equity_weight']):.1%} / Debt {float(risk['target_debt_weight']):.1%} | 산일 공시상 순현금·저부채 구조 |",
            "",
            "## 사용 규칙",
            "",
            "1. 회귀 Beta와 자본구조 입력의 출처를 분리한다.",
            "2. Beta는 L1→L4 partial pooling 후 동일 목표구조로 relever한다.",
            "3. 성장성·수주·부지매입은 FCF에 반영하고 WACC에서 재보상하지 않는다.",
            "4. 일간·주간 Beta 괴리는 추정 불확실성으로 남기며 임의 평균하지 않는다.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    args = parser.parse_args()

    payload, results = _refresh_snapshot(args.snapshot)
    args.snapshot.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    args.register.write_text(_render_register(payload, results), encoding="utf-8")
    print(
        f"updated {len(results)} same-source peer regressions through "
        f"{payload['risk']['beta_observation_end']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
