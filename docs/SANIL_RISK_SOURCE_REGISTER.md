# 산일전기 Beta·WACC 위험자료 원장

- 가치평가 기준일: 2026-08-25
- 회귀 관측 종료일: 2026-08-25
- 주가 원자료: 공개 한국 주가 시계열(네이버 금융 경로)
- 수집기: https://github.com/FinanceData/FinanceDataReader
- 공통 benchmark: FDR_KOSPI_KS11
- 주 추정치: 주간 수익률 OLS(상수항 포함)
- 교차검증: 일간 OLS를 진단값으로 함께 보존
- 모든 peer에 동일 기간·benchmark·빈도를 적용하고 회귀 표준오차를 저장한다.
- Debt / Equity 원장은 Beta 시계열과 분리해 `capital_source_ref`로 추적한다.

## L1→L4 회귀 결과

| Level | Peer | Code | Weekly Beta | Std. Error | Obs. | Daily Beta | R² | Series hash |
|---|---|---:|---:|---:|---:|---:|---:|---|
| L1_BROAD_SECTOR | LS_ELECTRIC_010120 | 010120 | 1.1378 | 0.1861 | 108 | 1.1252 | 0.2606 | `115c31456954df4c9513fdd5e5a2c85e4b88b87987babf32b8a1b6157ca082a8` |
| L1_BROAD_SECTOR | HYOSUNG_HEAVY_INDUSTRIES_298040 | 298040 | 1.0891 | 0.1734 | 108 | 1.0712 | 0.2713 | `111cfa34ab2852774450bab8a117c8d0534d93b90139e8520bf7f0b502dab4dd` |
| L2_INDUSTRY | HD_HYUNDAI_ELECTRIC_267260 | 267260 | 0.8712 | 0.1656 | 108 | 1.0013 | 0.2070 | `587c869f440e11de4fae4cf556c3a67366689eca4923c143c38be9f1b8000ae1` |
| L2_INDUSTRY | ILJIN_ELECTRIC_103590 | 103590 | 1.1439 | 0.2027 | 108 | 1.0483 | 0.2310 | `24637a32eecd7c4fdf1d901f5689ca9d4dc833f2f96ee787a555ee1862b44052` |
| L3_RISK_DRIVER_SUBINDUSTRY | TAIHAN_CABLE_001440 | 001440 | 1.1120 | 0.1782 | 108 | 1.0655 | 0.2686 | `74db671e206bd55c46cdc6c3d02a27dd4d249c3d2ad7dceb5521bc14ac312185` |
| L3_RISK_DRIVER_SUBINDUSTRY | CHERYONG_ELECTRIC_033100 | 033100 | 0.8854 | 0.2107 | 108 | 0.9857 | 0.1428 | `d717b307712b482b564cc1b37793993a2320907971dfad6164ada57f83d378a9` |
| L4_ECONOMIC_TWINS | KWANGMYUNG_ELECTRIC_017040 | 017040 | 0.3665 | 0.1605 | 108 | 0.3577 | 0.0469 | `e55c9189dabbb3bcf4bb779c7833f577f542076f085cb53ce5780a66022d656e` |
| L4_ECONOMIC_TWINS | CHEIL_ELECTRIC_199820 | 199820 | 0.7138 | 0.2149 | 108 | 0.9693 | 0.0943 | `422a8119bd93f83424001ab91a87abdb2137745c3f31de84f113dc99222d7481` |

## WACC 거시 입력

| 항목 | 동결값 | 처리 |
|---|---:|---|
| 원화 무위험금리 | 4.334% | 원화 장기국채 입력 |
| Mature-market ERP | 4.230% | Country Risk와 분리 |
| 한국 Country Risk Premium | 0.640% | 노출계수와 별도 적용 |
| Country-risk lambda | 0.25 | 미국 매출과 한국 생산·법인 노출을 분리한 판단값 |
| 세전 한계차입비용 | 4.690% | 현재 원화 차입 benchmark |
| 장기 목표 자본구조 | Equity 98.0% / Debt 2.0% | 산일 공시상 순현금·저부채 구조 |

## 사용 규칙

1. 회귀 Beta와 자본구조 입력의 출처를 분리한다.
2. Beta는 L1→L4 partial pooling 후 동일 목표구조로 relever한다.
3. 성장성·수주·부지매입은 FCF에 반영하고 WACC에서 재보상하지 않는다.
4. 일간·주간 Beta 괴리는 추정 불확실성으로 남기며 임의 평균하지 않는다.
