# 대한항공 연속분포 APV·재무곤경 가치평가 설계

Status: implementation-ready design; runtime not yet promoted  
Target: 대한항공 003490 / PR #184  
Base revision: `e9ed9f66967620e6e9ab28cfe49515ef37db5583`  
Supersedes as decision methodology: `korean_air_combined_margin_v1` nearest-anchor weighting and report-level limited-liability flooring

## 1. 결정

대한항공의 최종 투자판단은 더 이상 다음 식에서 만들지 않는다.

```text
Down / Base / Bull 점가치
× 가장 가까운 시나리오로 분류한 확률
× 시나리오별 max(주주 잔여가치, 0)
```

새 정본은 다음 순서다.

```mermaid
flowchart TD
    A["동일 범위 분기자료"] --> B["동적 운항·원가 분포"]
    B --> C["경로별 사업 SOTP"]
    C --> D["APV 금융효과"]
    D --> E["차환·증자·구조조정"]
    E --> F["구주주 가치분포"]
    F --> G["P50 적정가·수익률 기반 매수가"]
```

기존 3개 시나리오 DCF는 회귀 비교와 투자자 설명을 위해 남기되 새 실행의 확률 또는 목표가를 만들지 않는다. 기존 `continuous_financial_path_probability/v1`도 다른 실행의 호환성을 위해 삭제하지 않는다. 대한항공만 새 method/version으로 명시적으로 전환한다.

## 2. 현재 결과의 폐기 사유

현재 불변 산출물의 시나리오별 부채 차감 후 주당가치는 Down -26,760.88원, Base 3,671.77원, Bull 36,672.64원이다. 확률은 각각 34.74%, 9.45%, 55.81%다.

현재 엔진은 모든 모의 경로를 다음과 같이 가장 가까운 시나리오 기준점에 배정한다.

```text
scenario(path) = argmin distance(path, scenario_anchor)
```

중간 기준점인 Base는 두 경계 사이의 유한한 영역만 갖지만 양끝의 Down과 Bull은 바깥 꼬리를 계속 보유한다. 예측분산이 기준점 간격보다 훨씬 커지면 Base 확률은 사업의 중심 가능성과 무관하게 작아진다. 따라서 9.45%는 보정된 '기준 시나리오 발생확률'이 아니다.

또한 보고서에서 시나리오별 음수 주주가치를 0원으로 바꾼 뒤 평균하면서 다음 차이가 발생했다.

| 계산 | 주당가치 |
|---|---:|
| 부호를 보존한 확률가중 잔여가치 | 11,516원 |
| 시나리오별 0원 하한 적용 후 | 20,813원 |
| 하한 적용으로 추가된 가치 | 9,297원 |

0원은 최종 주주 지급액의 법적 하한일 수 있으나, 부채 만기·변동성·시간·차환·구조조정·회수순서를 계산하지 않은 현재시점 DCF 점가치를 0으로 자를 근거는 아니다. 구조적 옵션가치의 핵심 입력이 자산가치, 부채청구액, 변동성 및 만기라는 점은 Merton 모형과 일치한다.

따라서 기존 보고서의 20,813원 목표가와 15,600원 매수가는 새 모델의 비교 기준이나 prior로 사용하지 않는다. 과거 산출물은 감사 이력으로 보존하되 새 report manifest의 `supersedes`에만 기록한다.

## 3. 범위와 비범위

### 이번 변경 범위

1. 대한항공 자기 이력의 빈도·경제범위를 맞춘 동적 확률경로
2. 여객·화물·항공우주·호텔·기타의 경로별 SOTP
3. 고정 WACC 대신 금융효과를 분리하는 APV 주 평가경로
4. 현금·차입금·리스 만기와 차환·증자·구조조정의 연도별 분기
5. 경로별 구주주 가치분포와 P50/P20~P80/P10
6. 요구수익률과 달성확률에서 역산하는 구체 매수가
7. 기존 DCF, Street, 현재가 격리, 불변 산출물 및 보고서 계약과의 연결

### 이번 변경 비범위

- 기존 OCI·고려아연·셀트리온 확률 산출물의 소급 재작성
- 현재가 또는 증권사 목표가를 확률·할인율·매수가 입력으로 사용
- 항공사 비교기업의 영업실적을 대한항공 사건확률 표본으로 사용
- Merton 단일만기 모형을 대한항공의 주 평가모형으로 사용
- 기존 성공 산출물을 삭제하거나 같은 이름으로 덮어쓰기

## 4. 가치평가 방법

### 4.1 사업 SOTP

| 가치 블록 | 확률경로의 핵심 입력 | 평가 출력 |
|---|---|---|
| 여객 | ASK, RPK, 탑승률, 여객 yield, 유가, 환율, CASK ex-fuel, 기단·CAPEX·리스 | 무차입 FCFF 가치 |
| 화물 | ATK, RTK, 화물 yield, belly/freighter 공급, 유가·환율 | 무차입 FCFF 가치 |
| 항공우주·MRO | 확정수주, 전환시점, 매출총이익률, 운전자본·CAPEX | backlog DCF 가치 |
| 호텔·부동산 | 정상화 NOI, 자산가치, 자산별 부채 | NAV 또는 자산 DCF |
| 기타 서비스 | 확인된 매출기반, 정상화 마진, 재투자 | 정상화 서비스 DCF |
| 통합효과 | 확인된 통합비용, 시너지 종류·실현시점·실패조건 | 별도 증분가치 |

여객 영업이익률 하나가 화물 yield, 항공우주 성장, 호텔 NAV, 통합 시너지와 동시에 같은 방향으로 움직이지 못하게 한다. 각 증분가치는 별도의 `economic_path_id`를 갖는다.

### 4.2 APV

경로 `k`, 사업부 `s`의 무차입 사업가치는 다음으로 계산한다.

```text
V_unlevered[k,s] = PV(FCFF[k,s], asset_required_return[s])
```

연결 사업가치는 다음과 같다.

```text
Operating_APV[k]
  = sum(V_unlevered[k,s])
  + PV(usable_tax_shields[k])
  - PV(explicit_financing_costs[k])
  + non_operating_assets[k]
```

- 세금효과는 실제 과세소득이 발생하는 경로에서만 인정한다.
- 무차입 할인율은 사업의 시간가치와 체계적 위험만 가격화하고, 물리적 경로분포에 이미 들어간 영업·곤경 빈도를 다시 임의 가산하지 않는다.
- 세금효과는 그 실현위험에 맞는 별도 할인율을 사용하고, 부채스프레드에 포함된 distress와 명시적 distress branch를 중복 반영하지 않는다.
- 증자비용, 차환수수료, 자산급매 손실은 실제 해당 금융행동이 발생한 경로에만 넣는다.
- 재무곤경 비용을 APV에서 미리 차감한 뒤 distress branch에서 다시 차감하지 않는다.
- 기존 고정 WACC DCF는 cross-check로 유지한다. APV와 차이가 크면 평균하지 않고 세금·레버리지·말기가치·리스 처리 차이를 대사한다.

### 4.3 리스 처리

대한항공 실행은 리스를 금융청구권으로 취급한다. 다음 네 요소가 반드시 함께 움직여야 한다.

1. 영업성과는 lease-adjusted EBIT/EBITDAR로 대사한다.
2. 기존 리스 원금·이자·만기를 financing schedule에 넣는다.
3. 신규 기단 리스와 갱신에 필요한 경제적 재투자를 forecast에 넣는다.
4. EV-to-equity에서 같은 리스의 현금유출 또는 부채를 두 번 차감하지 않는다.

구현 전 `LeaseReconciliation`이 아래 항등식을 검증한다.

```text
opening lease liability
+ new lease additions
+ imputed interest
- principal payment
= closing lease liability
```

회계상 재분류만으로 구주주가치가 달라져서는 안 된다.

## 5. 동적 확률경로

### 5.1 입력자료 계약

`TargetDriverPanel`은 다음을 강제한다.

- 분기 단위, 최소 32개 비교가능 관측치
- 최소 12개 rolling-origin holdout
- 연결/별도, 대한항공 단독/통합 pro-forma 경제범위의 명시적 구분
- 최신 조건값도 분기 또는 계절조정된 동등 빈도
- 각 관측치의 `period_end`, `published_at`, `first_seen_at`, 원문 URL, 내용 hash
- 합병, 회계정책 변경, 사업범위 변경의 structural-break 선언

32개 분기와 12개 holdout은 학술적으로 유일한 숫자가 아니라 작은 표본의 과도한 승격을 막기 위한 versioned 운영 하한이다. 관측치 수가 이 기준을 넘더라도 예측성능·구간보정·범위 일관성을 통과하지 못하면 승인하지 않는다.

현재 2019~2024년 6개 연간 관측치와 2026년 반기 조건값을 결합한 인증서는 새 분포형 가치평가의 입력자격을 자동 상실한다. 기존 인증서를 삭제하지 않고 `legacy_scenario_classification_only`로 보존한다.

### 5.2 확률변수

확률을 직접 적용할 최소 load-bearing driver block은 다음이다.

1. 여객: capacity growth, load factor, passenger yield
2. 화물: cargo traffic, cargo yield
3. 원가: jet fuel, KRW/USD, non-fuel unit cost
4. 재투자: owned-aircraft CAPEX, lease additions
5. 재무: marginal refinancing spread, available refinancing capacity
6. 통합: 비용 및 시너지 실현 사건

비교기업은 Beta·PER·가치 sanity check에만 사용하고 위 분포의 실현표본에는 넣지 않는다. 공통 거시변수와 산업 공적통계는 외생 설명변수로만 허용한다.

### 5.3 모형

기본 모형은 계절항과 shrinkage를 가진 동적 상태모형이다.

```text
z[t] = seasonal[q] + A z[t-1] + B x[t] + epsilon[t]
epsilon[t] ~ multivariate Student-t(df, covariance)
```

- `z`: 대한항공의 변환된 load-bearing driver
- `x`: 유가·환율·공통 수요 등 원문이 있는 외생변수
- `A`: 시간적 지속성; 표본이 작을수록 대각·0 방향으로 shrinkage
- `covariance`: 같은 기간 driver의 상관구조
- Student-t: 정상분포보다 두꺼운 꼬리

각 미래기간을 독립적으로 뽑지 않고 이전 기간의 상태에서 재귀적으로 전개한다. parameter uncertainty도 별도 outer draw로 포함한다.

정상/충격 2상태 regime은 rolling CRPS가 단일상태 모형보다 개선되고 상태식별이 안정적일 때만 승격한다. COVID 관측치를 제거하지 않되, 성능이 검증되지 않은 복잡한 regime 모형을 강제하지 않는다.

### 5.4 보정과 승인

확률분포는 다음을 모두 통과해야 `valuation_distribution_authorized=true`가 된다.

- 시간순 rolling-origin 검증
- 각 핵심 driver의 CRPS와 log score
- best seasonal-naive / historical-block-bootstrap 대비 양(+)의 CRPS skill
- 80%·90% 예측구간의 관측 포함률이 사전 고정된 이항 허용구간 내 존재
- PIT/reliability에서 명백한 중심편향 또는 꼬리누락 없음
- 잔차의 시간적 지속성과 동시상관이 모의경로에 재현됨
- seed 및 draw-count 변경 시 P50과 매수가의 안정성

표본 수 통과만으로 `CALIBRATED`를 부여하지 않는다. 성능이 benchmark를 이기지 못하면 개발자 산출물에 정확한 실패지표를 기록하고 최종 투자보고서 생성을 차단한다. '확률이 보정되지 않았다'는 일반 문장만 들어간 반쪽 보고서는 생성하지 않는다.

## 6. 재무곤경·구주주 회수 모형

### 6.1 연도별 가용유동성

```text
available_liquidity[t,k]
  = opening_cash[t,k]
  + operating_cash_flow[t,k]
  + committed_facilities[t,k]
  - interest[t,k]
  - debt_maturity[t,k]
  - lease_payment[t,k]
  - mandatory_capex[t,k]
  - minimum_operating_cash[t]
```

부족 시 실행순서는 공시·계약으로 가능한 행동만 사용한다.

```text
차환 → 비핵심 자산매각 → 신주·전환성 자본조달 → 구조조정
```

각 행동에는 한도, 가격/할인, 거래비용, 실행시점, 기존 주주 희석률과 근거 Evidence가 필요하다. 근거 없는 무제한 차환이나 무손실 자산매각은 금지한다.

### 6.2 경로별 구주주가치

유한책임의 `max(·, 0)`는 현재시점 DCF 점가치가 아니라 명시적 미래시점 `H`의 주주 지급액에만 적용한다. 기존 주주가 증자에 새 돈을 낸다고 자동 가정하지 않으며, 미참여 기준 희석과 양도 가능한 권리의 가치만 별도 계약으로 반영한다.

```text
if survives through H:
    terminal_old_equity_payoff[H,k]
      = max(enterprise_value[H,k] + cash[H,k] - senior_claims[H,k], 0)
        * old_shareholder_ownership_after_dilution[H,k]

if distress occurs at tau <= H:
    old_shareholder_recovery[tau,k]
      = max(distress_asset_proceeds[tau,k]
            - claim_waterfall[tau,k]
            - distress_cost[tau,k], 0)
        * old_shareholder_retention[tau,k]

old_shareholder_present_value[k]
  = PV(dividends_and_transferable_rights_to_old_holders[k])
    + PV(terminal_old_equity_payoff[k] or old_shareholder_recovery[k])
```

0원은 이 명시적 waterfall의 결과일 때만 허용한다. 하방 DCF 점가치를 보고서에서 사후적으로 0으로 바꾸지 않는다. 회수율은 0으로 고정하지 않고 담보·우선순위·자산매각가치·증자 또는 출자전환 조건에서 산출한다.

Merton 구조모형은 자산변동성과 부채청구권으로 얻은 주식 옵션가치가 위 결과와 크게 어긋나는지 보는 cross-check로만 사용한다. 대한항공의 다중 만기 차입금과 리스는 단일만기 모형으로 대체하지 않는다.

## 7. 목표가·시나리오·매수가 정책

### 7.1 보고할 가치

`EquityValueDistribution`은 최소 다음을 포함한다.

| 출력 | 용도 |
|---|---|
| P50 | 중앙 적정가의 headline |
| Mean | 확률가중 평균의 보조지표 |
| P20~P80 | 주 평가범위 |
| P10/P90 | 스트레스/상방 꼬리 |
| `P(distress)` | 구조조정·지급불능 위험 |
| `P(dilution)` | 자본조달로 인한 희석 위험 |
| `E(old_share_retention | distress)` | 곤경 시 구주주 잔존율 |

Mean은 경제적으로 유효한 보조값이지만, 오른쪽 꼬리가 큰 고레버리지 주식에서 headline으로 쓰지 않는다. P50 역시 절대적 진실이 아니라 의사결정용 중앙값임을 표시한다.

### 7.2 시나리오 표시

시나리오는 확률을 생성하지 않고 분포를 설명한다.

- Down: 하위 20% 경로의 경제적 공통점, 대표값 P10
- Central: P20~P80 경로, 대표값 P50
- Bull: 상위 20% 경로의 경제적 공통점, 대표값 P90

20%/60%/20%는 '가치구간 비중'이며 사건확률로 표시하지 않는다. 별도 사건확률이 필요하면 상호배타적이고 전체를 포괄하는 명시적 조건을 먼저 정의한 뒤 모의경로가 조건을 충족한 빈도로 계산한다.

`Base`라는 이름을 유지할 경우 그 경로는 반드시 posterior median driver path에서 생성한다. 분석가가 별도로 지정한 Base anchor를 확률의 기준으로 사용하지 않는다.

### 7.3 구체 매수가

매수가는 임의 안전마진율이 아니라 투자기간과 요구수익률에서 역산한다.

경로 `k`, 투자기간 `H=3`, 기본 요구수익률 `h=12%`에 대해:

```text
discounted_payoff[k]
  = (exit_share_value[k] + cumulative_dividends[k]) / (1 + h)^H

entry_price
  = Q25(discounted_payoff)
```

이는 보정된 모형 안에서 제시 매수가 이하의 투자자가 연 12% 이상을 달성할 확률을 약 75%로 정하는 정책이다. 3년·12%·Q25는 보편적 금융법칙이 아니라 이번 실행 전에 고정할 versioned 투자정책이며, 보고서는 10%·12%·15% 요구수익률 민감도를 함께 표시한다.

- 현재 주가는 매수가 산식의 입력이 아니다.
- 매수가와 현재가의 비교는 Intrinsic Freeze 이후에만 수행한다.
- `P(distress)`, P10 손실 및 희석확률을 함께 표시한다.
- Q25가 0 이하이거나 분포 승인에 실패하면 매수가를 만들지 않고 전체 최종보고서 생성을 차단한다. 조건부 숫자를 최종 숫자로 승격하지 않는다.

## 8. 신규 데이터 계약

```python
@dataclass(frozen=True)
class TargetDriverPanel:
    target_id: str
    frequency: str
    perimeter: str
    observations: tuple[TargetDriverObservation, ...]
    structural_breaks: tuple[StructuralBreak, ...]
    source_hash: str

@dataclass(frozen=True)
class DynamicDriverPosterior:
    driver_ids: tuple[str, ...]
    seasonal_terms: tuple[tuple[str, Decimal], ...]
    transition_matrix: tuple[tuple[Decimal, ...], ...]
    innovation_covariance: tuple[tuple[Decimal, ...], ...]
    student_t_df: int
    parameter_draws_hash: str
    calibration_diagnostics: CalibrationDiagnostics

@dataclass(frozen=True)
class FinancingPathSpec:
    opening_cash: Decimal
    minimum_operating_cash: Decimal
    debt_and_lease_schedule: tuple[ClaimSchedule, ...]
    committed_facilities: tuple[Facility, ...]
    capital_actions: tuple[CapitalActionPolicy, ...]
    recovery_waterfall: RecoveryWaterfallPolicy

@dataclass(frozen=True)
class EquityValueDistribution:
    quantiles: tuple[tuple[Decimal, Decimal], ...]
    mean: Decimal
    distress_probability: Decimal
    dilution_probability: Decimal
    expected_old_share_retention_in_distress: Decimal
    draw_count: int
    seed_set: tuple[int, ...]
    input_hash: str
    distribution_hash: str

@dataclass(frozen=True)
class EntryPricePolicy:
    horizon_years: int
    required_annual_return: Decimal
    success_quantile: Decimal
    sensitivity_returns: tuple[Decimal, ...]
```

위 객체는 현재가·증권사 목표가·기존 20,813원 또는 15,600원 필드를 갖지 않는다.

## 9. 구현 경계와 파일 계획

기존 v1 확률경로를 직접 고쳐 다른 종목의 회귀를 깨뜨리지 않는다.

| 구분 | 파일 | 변경 |
|---|---|---|
| 정본 계약 | `SKILL.md`, `.agents/skills/valuation-analysis/SKILL.md`, 핵심 방법론 문서 | 분포형 가치·distress·entry gate 추가, 두 SKILL byte-identical 유지 |
| Unit Contract | `config/unit_contract_registry.yaml` | Scenario Engine 출력에 driver paths, DETERMINISTIC_VALUATION에 distributional APV, Audit에 distress/entry 검증 추가 |
| Method registry | `config/valuation_method_capability_registry.yaml` | `airline_transport/traffic_yield_apv_distributional` exact route 추가 |
| 동적 분포 | `src/valuation_engine/dynamic_driver_distribution.py` | 동일빈도 검증, 재귀상태 추정·모의, OOS proper-score 진단 |
| 리스·재무곤경 | `src/valuation_engine/airline_financing_paths.py` | 리스 대사, 유동성, 차환·증자·waterfall |
| 경로별 가치 | `src/valuation_engine/distributional_apv.py` | segment APV/SOTP와 구주주가치분포 |
| 매수가 | `src/valuation_engine/entry_price.py` | 요구수익률·quantile 기반 pure function |
| Registry 연결 | `src/valuation_engine/evaluator_registry.py`, `generic_valuation_plan.py`, `valuation_execution.py` | 새 method/version exact binding; legacy fallback 금지 |
| Audit | `src/valuation_engine/generic_audit.py`, `audit_adapter.py` | anchor 미사용, 0-floor 금지, lease/waterfall/분포 안정성 검사 |
| Reporting | `investor_report.py`, `generic_reporting.py`, `visual_reporting.py` | P50·범위·위험·매수가 표시, 기존 문구 삭제 |
| 대한항공 입력 | `runs/korean-air-003490/**`, `research/korean-air-20260913/**` | 분기 panel, debt/lease schedule, method binding, 재생성 입력 |
| 최종 산출물 | 새 불변 report directory | 기존 manifest를 `supersedes`, 동일 실행의 보고서·SVG·감사 묶음 |

`evaluator_registry.py`는 보호영역이므로 구현 시작 전에 PR #184의 active claim에 해당 파일을 추가한다. 현재 작업자의 미추적 risk 조사파일은 이번 설계·구현의 write set에서 제외한다.

## 10. 수정 조항과 실행 DAG

### 원자 조항

| ID | 요구결과 | 시작 Unit | 관찰 가능한 합격조건 |
|---|---|---|---|
| C1 | nearest-anchor 확률 제거 | SCENARIO_ENGINE | 대한항공 가치분포가 시나리오 이름·anchor 이동에 불변 |
| C2 | 0원 하한의 사후 평균 제거 | DETERMINISTIC_VALUATION | distress waterfall 없이는 음수 점가치를 0으로 바꾸지 못함 |
| C3 | 분기 동일범위 자기이력 보정 | SCENARIO_ENGINE | 연간+반기 혼합 입력 거부, OOS 진단 통과 필요 |
| C4 | APV·리스·차환·희석 결합 | DETERMINISTIC_VALUATION | lease roll-forward와 claim waterfall 대사 |
| C5 | 구체 매수가를 수익률에서 역산 | INTRINSIC_FREEZE | 시장가격을 바꿔도 매수가 불변, quantile 수식 golden test |
| C6 | 투자자 보고서와 산출물 재생성 | FINAL_REPORT | 새 숫자·범위·위험이 동일 immutable run과 일치 |

### 순차 작업

```mermaid
flowchart TD
    T1["T1 계약·스키마"] --> T3["T3 동적 분포"]
    T2["T2 분기자료·만기표"] --> T3
    T3 --> T4["T4 APV·재무곤경"]
    T4 --> T5["T5 매수가·감사"]
    T5 --> T6["T6 대한항공 재실행"]
    T6 --> T7["T7 보고서·SVG·불변묶음"]
    T7 --> T8["T8 전체회귀·CI"]
```

T1과 T2만 write set이 겹치지 않아 함께 진행할 수 있다. 모델 이후 보고서라는 장벽은 유지한다. 실행 중 조항 하나가 실패하면 그 작업과 하위 작업만 무효화하고, base revision과 plan hash가 같은 독립 완료 작업만 재사용한다.

### 작업별 read/write set과 검증기

| Task | 조항 | 주요 read set | write set | 완료 검증기 |
|---|---|---|---|---|
| T1 계약·스키마 | C1~C5 | 정본 방법론, Unit Contract, method registry | 두 `SKILL.md`, 정본 문서, `unit_contract_registry.yaml`, `valuation_method_capability_registry.yaml`, PR #184 claim | skill byte identity, registry validators, revision-plan validator |
| T2 분기자료·만기표 | C3,C4 | DART/IR 원문, 기존 run declarations | 새 driver panel·debt/lease schedule·provenance declaration | frequency/perimeter/source-link/lease-schedule validators |
| T3 동적 분포 | C1,C3 | T1 schema, T2 panel | `dynamic_driver_distribution.py`, 전용 tests | OOS score, coverage, recursive persistence, correlation tests |
| T4 APV·재무곤경 | C2,C4 | T1 schema, T2 financing inputs, T3 draws | `airline_financing_paths.py`, `distributional_apv.py`, registry binding, tests | APV, lease, liquidity, dilution, waterfall tests |
| T5 매수가·감사 | C2,C5 | T4 distribution | `entry_price.py`, audit adapters, tests | Q25 golden test, market isolation, no report-floor test |
| T6 대한항공 재실행 | C1~C6 | T2~T5 outputs, 기존 evidence/bridges | 새 run declarations와 immutable runtime outputs | 전체 33단계, Audit, Freeze, distribution-hash binding |
| T7 보고서·SVG | C6 | T6 frozen outputs, post-freeze Street/market | 새 versioned report bundle과 latest manifest | report verifier, source links, 두 SVG, supersedes link |
| T8 전체회귀·CI | C1~C6 | 최종 head | 코드 수정 없음 | full pytest, registries, portfolio integrity, PM sync, verified-report, LIVE_PRIMARY |

표의 `write set`은 경로군을 요약한 것이다. 실제 revision plan에서는 파일 단위로 펼치고, 같은 파일을 쓰는 작업은 한 owner에게 주거나 명시적으로 순서를 둔다.

## 11. 필수 회귀·적대 테스트

### 확률·시계열

- `test_korean_air_rejects_annual_history_with_halfyear_conditioning`
- `test_path_simulation_is_recursive_not_independent_by_year`
- `test_cross_driver_covariance_is_reproduced`
- `test_scenario_labels_and_anchors_do_not_change_equity_distribution`
- `test_distribution_requires_positive_oos_skill_and_coverage`
- `test_peer_company_outcomes_cannot_enter_target_driver_panel`

### APV·리스·재무곤경

- `test_lease_rollforward_balances`
- `test_lease_reclassification_alone_does_not_change_equity_value`
- `test_tax_shield_exists_only_with_taxable_income`
- `test_refinancing_limit_can_trigger_dilution_then_distress`
- `test_distress_recovery_can_be_zero_or_positive_from_waterfall`
- `test_report_layer_cannot_apply_scenario_zero_floor`
- `test_financing_cost_is_not_counted_in_apv_and_distress_twice`

### 목표가·매수가·격리

- `test_headline_value_is_p50_and_mean_is_secondary`
- `test_entry_price_equals_discounted_payoff_q25`
- `test_entry_price_is_invariant_to_current_market_price`
- `test_entry_price_withheld_when_distribution_not_authorized`
- `test_street_and_market_load_only_after_distribution_freeze`

### 완주

- 기존 OCI·셀트리온·고려아연 golden fixture 불변
- 대한항공 기존 3개 시나리오 DCF는 legacy cross-check로 재현
- 새 대한항공 실행의 probability/value/report/두 SVG가 동일 distribution hash 참조
- 모든 활성 Evidence가 원문 HTTP(S) 링크를 보고서에 제공
- registry, portfolio integrity, PM project status sync, verified-report, LIVE_PRIMARY와 전체 pytest 통과

## 12. 승격 조건

다음이 모두 PASS일 때만 새 보고서를 최종 투자보고서로 승격한다.

1. 분기 driver panel의 범위·빈도·원문이 대사됨
2. OOS 확률분포 검증이 versioned gate를 통과함
3. 경로별 사업가치와 consolidated SOTP가 대사됨
4. 차입금·리스·현금·차환·희석·waterfall이 대사됨
5. APV와 기존 WACC DCF의 차이가 설명됨
6. 0원 하한의 report-time 적용이 없음
7. P50·Mean·P20~P80·P(distress)·P(dilution)과 매수가가 동일 distribution hash에서 생성됨
8. Audit 이후에만 Street와 현재가가 로드됨
9. 새 불변 manifest가 기존 보고서를 `supersedes`로 연결함
10. 필수 CI 전체가 최종 head에서 통과함

하나라도 실패하면 새 숫자를 배포하지 않는다. 이 경우 실패는 구체적인 데이터·산식·검증항목으로 개발 산출물에 남기며, 기존 20,813원·15,600원을 대신 표시하지 않는다.

## 13. 근거

- 연속위험은 소수의 임의 시나리오로 자를 때 경계가 주관적이고, 시나리오 확률은 전체 가능영역을 포괄할 때만 의미가 있다: [Aswath Damodaran, Probabilistic Approaches](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/probabilistic.pdf)
- distress를 단순 고할인율로만 처리하면 계속기업 가정이 남으므로 생존가치와 distress sale value를 분리해야 한다: [Aswath Damodaran, Distress in DCF Valuation](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/distresspaper.htm)
- APV는 사업가치와 금융효과를 분리한다: [Stewart Myers, Interactions of Corporate Financing and Investment Decisions](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1974.tb00021.x)
- 주식의 옵션 성격은 자산가치뿐 아니라 부채청구액, 변동성 및 만기를 요구한다: [Robert Merton, On the Pricing of Corporate Debt](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1974.tb03058.x)
- 리스를 부채로 재분류할 때 영업이익, 재투자, 할인율과 EV-to-equity 처리가 함께 일관돼야 한다: [Aswath Damodaran, Operating Leases](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/oplease.htm)
- 상태전환은 경기·충격 국면에 따라 자기회귀 모수가 달라지는 경우의 후보모형이다: [James Hamilton, A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle](https://www.ssc.wisc.edu/~bhansen/718/Hamilton1989.pdf)
- 확률예측은 적중률이 아니라 proper scoring rule로 전체 예측분포를 검증해야 한다: [Gneiting and Raftery, Strictly Proper Scoring Rules, Prediction, and Estimation](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)
