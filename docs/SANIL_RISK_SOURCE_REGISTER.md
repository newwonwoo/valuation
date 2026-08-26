# 산일전기 Beta·WACC 외부 위험자료 원장

- 기준일: 2026-08-25
- 목적: 산일전기 `LIVE_PRIMARY`의 L1→L4 Beta 계층과 WACC 입력 출처를 명시한다.
- 분류: 외부 시장자료(회사 공시·가이던스가 아님)
- 공통 Beta 관측: StockAnalysis의 `Beta (5Y)` 및 같은 페이지의 `Debt / Equity`를 동결하였다.
- 한계: 공급자의 세부 benchmark·빈도·회귀 표준오차는 공개되지 않는다. 따라서 `beta_standard_error`는 비워 두며, 결과는 공식 KRX 회귀 Beta가 아니라 중간 신뢰도의 외부 위험 스냅샷이다.
- 목표 자본구조: peer 평균을 산일에 강제하지 않고, 산일 공시상 순현금·저부채 구조를 반영한 장기 정책값을 별도 사용한다.

## L1 — Broad Sector

| 회사 | 코드 | Beta (5Y) | Debt / Equity | 출처 |
|---|---:|---:|---:|---|
| LS ELECTRIC | 010120 | 1.83 | 0.69 | https://stockanalysis.com/quote/krx/010120/statistics/ |
| 효성중공업 | 298040 | 2.16 | 0.25 | https://stockanalysis.com/quote/krx/298040/statistics/ |

## L2 — Electrical Equipment Industry

| 회사 | 코드 | Beta (5Y) | Debt / Equity | 출처 |
|---|---:|---:|---:|---|
| HD현대일렉트릭 | 267260 | 1.17 | 0.08 | https://stockanalysis.com/quote/krx/267260/statistics/ |
| 일진전기 | 103590 | 1.81 | 0.22 | https://stockanalysis.com/quote/krx/103590/statistics/ |

## L3 — Risk-driver Subindustry

| 회사 | 코드 | Beta (5Y) | Debt / Equity | 출처 |
|---|---:|---:|---:|---|
| 대한전선 | 001440 | 2.18 | 0.67 | https://stockanalysis.com/quote/krx/001440/statistics/ |
| 제룡전기 | 033100 | 1.24 | 0.08 | https://stockanalysis.com/quote/kosdaq/033100/statistics/ |

## L4 — Economic Twins

| 회사 | 코드 | Beta (5Y) | Debt / Equity | 출처 |
|---|---:|---:|---:|---|
| 광명전기 | 017040 | 1.25 | 0.21 | https://stockanalysis.com/quote/krx/017040/statistics/ |
| 제일일렉트릭 | 199820 | 1.58 | 0.22 | https://stockanalysis.com/quote/kosdaq/199820/statistics/ |

## WACC 거시 입력

| 항목 | 동결값 | 출처/처리 |
|---|---:|---|
| 원화 무위험금리 | 4.334% | 2026-08-25 한국 10년 국채금리 스냅샷 |
| Mature-market ERP | 4.230% | Damodaran 2026 국가위험 자료 |
| 한국 Country Risk Premium | 0.640% | Damodaran 2026 국가위험 자료 |
| Country-risk lambda | 0.25 | 미국 매출 비중과 한국 생산·법인·비용 노출을 함께 고려한 PRISM 판단값 |
| 세전 한계차입비용 | 4.690% | 원화 시장 차입 benchmark 동결값 |
| 장기 목표 자본구조 | Equity 98% / Debt 2% | 산일전기 공시상 순현금·저부채 구조를 반영한 장기 정책값 |

## 사용 규칙

1. 이 자료는 실제 회사 실적이나 경영진 계획으로 표시하지 않는다.
2. Beta·WACC는 같은 run과 같은 목표 자본구조로 결속한다.
3. 외부 Beta 공급자의 방법론이 불완전하므로 공식 KRX 시계열 회귀 provider가 가용해지면 해당 자료로 교체한다.
4. 성장성·수주가시성·부지매입은 현금흐름 경로에 반영하며, 같은 사실을 WACC 인하로 중복 보상하지 않는다.
