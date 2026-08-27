# Live Company Validation and Probability Calibration

Status: implementation-ready design  
Companies: OCI Holdings, Oracle, Bloom Energy, GE Vernova

## 1. 목적

네 회사는 목표가를 비교하기 위한 표본이 아니다. 동일한 Research OS가 서로 다른 수익 구조에서 fail-closed로 작동하는지 검증하는 contract fixture다.

공통 완료 조건:

1. 회사·연결범위·segment가 primary source로 확정된다.
2. RoutingDecision이 Evidence key를 가진다.
3. required critical evidence가 모두 존재한다.
4. Evidence conflict가 삭제되지 않는다.
5. Compiler가 scenario별 AssumptionSet을 생성한다.
6. exact evaluator가 Registry에서 선택된다.
7. Red Team이 market/valuation 정보 없이 반증한다.
8. Audit PASS 전 target price를 읽지 않는다.
9. Live state에는 legacy regression Evidence가 남지 않는다.
10. 회사별 actuals와 consolidated/segment 합계가 primary source에 대사된다.

하나라도 실패하면 fair value 대신 `VALUATION BLOCKED`와 부족한 자료를 출력한다.

## 2. OCI Holdings

### Routing

```text
holding_company / sotp / 1
├─ polysilicon: commodity_materials / throughput_exit_multiple / 1
├─ wafer: commodity_materials / throughput_exit_multiple / 1
└─ other assets: asset-specific evaluator or explicit non-operating asset value
```

### Primary evidence plan

- DART 사업/반기/분기보고서: 주식수, debt/cash, segment 재무, 관계기업, NCI
- OCI 공식 IR: capacity, expansion timing, contract status, segment economics
- 정책 원문: 적용범위·시행일·예외만 기록
- 고객/공급사 공식 자료: qualification/LTA 독립 검증

### Critical questions

- 70kMT가 현재 capacity인가 목표 capacity인가?
- 정책 floor와 실제 ASP 사이에 observable contract/realization evidence가 있는가?
- wafer ownership, subsidiary debt, 보조금이 EBITDA/W와 SOTP에서 어디에 반영되는가?
- `other_business_pv` 구성자산이 폴리/wafer/option과 겹치지 않는가?

### Live completion gate

모든 active assumption에서 `LEGACY_REGRESSION` 참조가 제거되고 asset-level SOTP가 생성돼야 한다.

## 3. Oracle

### Routing

Primary: `software_platform / arr_fcff_dcf / 1`  
Possible segment split: cloud infrastructure, cloud applications, license/support. Holding discount는 사용하지 않는다.

### Primary evidence plan

- SEC 10-K/10-Q: revenue composition, RPO, capex, D&A, debt/cash, shares, stock compensation
- Oracle earnings release/IR: cloud metrics, guidance, capacity/CAPEX plan
- 고객 공식자료는 대형 계약의 independent confirmation에만 사용

### Required assumptions

- revenue/ARR or disclosed cloud revenue base
- RPO conversion schedule without treating RPO as separate asset value
- gross/operating/FCF margin path
- data-center CAPEX and funding path
- dilution/share count and net debt
- discount rate or normalized multiple with evidence-backed rationale

### Kill conditions

- RPO growth without revenue/cash conversion
- CAPEX growth persistently outruns incremental gross profit
- cloud growth relies on concentration not disclosed in base assumptions
- stock compensation/dilution offsets FCF growth

### Validation test

RPO를 증가시키되 conversion assumption을 고정하면 valuation이 자동 상승해서는 안 된다. Revenue/FCF Bridge가 바뀔 때만 가치가 변한다.

### PASS gate

- 최근 3개 연도와 최신 분기의 revenue, cash flow, shares가 filing에 대사된다.
- 입력은 USD와 shares actual unit을 유지한다.
- RPO와 data-center CAPEX/funding path가 서로 다른 economic path를 가진다.
- CAPEX, D&A, debt 변화가 cash flow와 EV→Equity에서 한 번씩만 반영된다.
- `terminal_growth < WACC`와 current-price isolation을 통과한다.

## 4. Bloom Energy

### Routing

Primary: `order_equipment / backlog_conversion_exit_multiple / 1`  
Submodule: `energy_infrastructure / asset_npv_sotp / 1` for separately owned/financed projects or service cash flows.

### Primary evidence plan

- SEC 10-K/10-Q: product/installation/service revenue recognition, backlog definitions, debt/cash, inventory, receivables, warranties
- Bloom IR: manufacturing capacity, booking/backlog, margin and deployment timing
- 고객·utility 공식자료: binding order, acceptance, interconnection and power delivery
- 정책 원문: tax credit eligibility and effective dates

### Contract-quality classification

MOU, framework ceiling, IDIQ ceiling, firm equipment order, accepted system, service contract를 각각 분리한다. Firm backlog만 deterministic conversion input 후보가 된다.

### Kill conditions

- backlog 증가와 동시에 deposit/contract liability 또는 delivery가 약화
- installation/acceptance 지연으로 revenue recognition 이월
- warranty/service cost가 gross-margin 개선을 상쇄
- policy benefit이 실제 project economics에 적용되지 않음
- working capital과 debt가 shipment 성장보다 악화

### Validation test

Framework ceiling 증가만으로 quantity assumption이 변하면 FAIL이다. Firm order·delivery schedule Bridge가 있어야 한다.

### PASS gate

- product, installation, service, electricity의 revenue/cost를 filing에 대사한다.
- framework/MOU는 Core Value와 firm backlog quantity에 들어가지 않는다.
- 관련자 JV 매출, 미실현이익, 지분법 손익을 별도 path로 추적한다.
- product sale과 service/PPA cash flow 중복검사를 통과한다.
- warranty, installation delay, customer concentration, financing failure stress가 존재한다.
- convertible debt와 stock compensation dilution을 EV→Equity에 반영한다.

## 5. GE Vernova

### Routing

GE Vernova는 법적 지주사가 아니라 다사업 운영회사다. `HOLDING_COMPANY`나 holding discount를 적용하지 않는다.

```text
OPERATING_MULTI_SEGMENT / company_sum_parts / 1
├─ Power: backlog conversion + installed-base service cash flow
├─ Wind: backlog conversion + loss-contract/warranty guard
├─ Electrification: backlog conversion + capacity/margin path
└─ Corporate: tax, CAPEX, NWC, pension, consolidated net cash/debt
```

### Primary evidence plan

- SEC 10-K/10-Q: segment revenue/profit, backlog, contract assets/liabilities, working capital, cash/debt, restructuring
- GE Vernova IR: segment margin framework, backlog conversion, service mix, capacity investment
- 고객/utility/regulator primary sources: grid and generation awards

### Required segment distinctions

- equipment backlog와 long-term service revenue를 분리한다.
- Wind backlog 증가는 손실계약/원가율 검증 없이 호재로 처리하지 않는다.
- Electrification bottleneck은 order, capacity, lead time, margin을 각각 Bridge한다.
- corporate costs, pension, restructuring, NCI를 parent level에서 한 번만 반영한다.

### Kill conditions

- backlog conversion 지연
- Wind loss-contract provision 확대
- service margin이 equipment weakness를 보완하지 못함
- working-capital release가 반복 가능한 FCF로 오인됨

### Validation test

동일 backlog가 segment EV와 별도 strategic-option value에 함께 들어가면 FAIL이다.

### PASS gate

- Power/Wind/Electrification 합계가 consolidated actuals에 대사된다.
- equipment와 service RPO, conversion period를 분리한다.
- 고객선수금의 일시적 NWC 효과를 terminal FCF로 영구화하지 않는다.
- Wind loss-contract와 warranty stress가 필수다.
- 동일 data-center/grid demand가 Power와 Electrification path에 중복되지 않는다.
- ownership/NCI/holding discount를 자동 적용하지 않고 current-price isolation을 통과한다.

## 6. Cross-company anti-overfit matrix

| Contract | OCI | Oracle | Bloom | GE Vernova |
|---|---:|---:|---:|---:|
| Actual-unit conversion | kMT/GW | currency/period | units/MW | equipment/service mix |
| Aggregator | holding SOTP | single company | hybrid SOTP | operating segments |
| Backlog quality | LTA | RPO timing | contract class | segment backlog |
| Policy isolation | MIP→ASP 금지 | subsidy/CAPEX | credit eligibility | policy/order distinction |
| CAPEX guard | expansion/funding | data center/FCF | manufacturing/project | capacity/restructuring |
| EV→Equity | segment ownership | consolidated | consolidated/project | segment + parent |

네 회사 모두 같은 company-specific formula를 사용하면 실패다. 공통인 것은 Compiler/Evaluator/Audit 계약이며 공식은 evaluator별로 달라야 한다.

## 7. Probability event ledger

Probability calibration은 valuation 결과가 아니라 **사전에 정의된 사건 예측**을 평가한다.

```python
@dataclass(frozen=True)
class ProbabilityForecast:
    forecast_id: str
    hypothesis_id: str
    company_id: str
    forecast_class: str
    horizon: str
    event_definition: str
    outcome_space: tuple[str, ...]
    issued_at: datetime
    evaluation_deadline: date
    probability: Decimal
    displayed_band: str
    evidence_snapshot_hash: str
    model_version: str
    resolution_rule: str
    resolution_source_policy: str
    supersedes_id: str | None

@dataclass(frozen=True)
class ForecastOutcome:
    forecast_id: str
    observed_at: datetime
    outcome: str  # occurred | not_occurred | censored | ambiguous
    outcome_evidence_ids: tuple[str, ...]
    resolver_id: str
    rationale: str

@dataclass(frozen=True)
class CalibrationSnapshot:
    cohort_key: str
    horizon: str
    cutoff: datetime
    raw_sample_count: int
    effective_sample_count: int
    mapping_version: str
    metrics: dict[str, Decimal]
    status: CalibrationStatus
```

발행 후 기존 record의 probability, deadline, resolution rule을 수정하지 않는다. 새 Evidence가 생기면 사건 정의·기한·해소 계약을 그대로 유지한 superseding forecast version으로 확률만 갱신한다. 같은 사건의 여러 revision은 독립 표본으로 세지 않는다.

`LIVE_PRIMARY`의 선언된 binary event는 `PROBABILITY_DISTRIBUTION_ANALYSIS`에서 평가 결과와 분리된 raw forecast draft로 고정되고, 감사 통과 실행의 `SAVE_STATE`에서 append-only 생산 원장에 저장된다. outcome은 명시적 `first_seen_at`, 활성 primary Evidence ID, 직접 검증 가능한 HTTP(S) 원문 링크가 모두 있어야 저장된다. 분석가 주장·시장 비교·합성 또는 사후 구성된 outcome은 생산 이력으로 들어갈 수 없다.

## 8. Calibration status

```text
UNCALIBRATED : 평가 가능한 표본 부족
CALIBRATING  : 표본 축적 중, production mapping 사용 금지
CALIBRATED   : forecast class·horizon별 promotion gate 통과
DEGRADED     : 최근 out-of-sample window에서 gate 이탈
```

기존 enum은 구현 시 위 네 상태로 migration한다.

표시 규칙:

- `UNCALIBRATED`/`CALIBRATING`: 5% 단위 또는 Low/Medium/High band로 표시
- `CALIBRATED`: 1%보다 세밀하게 표시하지 않음
- 표본이 적은 company/scanner별 수치는 전체 calibration과 혼합하지 않음
- 0%/100%는 사건이 이미 해결됐거나 논리적으로 불가능할 때만 허용
- Evidence confidence와 event probability를 별도 필드와 라벨로 표시

## 9. Calibration metrics

필수:

- Brier score
- Brier Skill Score versus predeclared base rate
- log loss
- reliability table와 fixed-bin ECE
- calibration intercept/slope when sample permits
- outcome coverage and censoring rate
- industry, event type, source grade, horizon별 slice

Binary hypothesis는 Brier가 주지표다. Bear/Base/Bull은 사전에 결정된 mutually-exclusive outcome resolver가 있을 때만 Ranked Probability Score를 쓴다. 적정가 오차는 별도 model accuracy이며 probability calibration과 혼합하지 않는다. AUC와 accuracy를 calibration 지표로 표시하지 않는다.

초기 promotion gate는 versioned policy로 고정한다.

- 같은 forecast class·horizon의 독립 resolved event 200건 이상
- 20개 이상 회사, 8개 이상 분기 포함
- 표시 probability band당 30건 이상
- chronological out-of-sample Brier Skill Score가 연속 window에서 양수
- fixed-bin ECE 0.08 이하
- ambiguous/cancelled outcome 10% 이하

숫자는 초기 운영 기준이며 데이터가 쌓인 뒤 변경할 수 있다. 변경 시 새 calibration-policy version, historical holdout, forward validation을 요구한다.

## 10. Leakage and gaming guards

- target market price를 forecast feature 또는 outcome으로 사용하지 않는다.
- 사건 발생 후 outcome rule을 바꾸지 않는다.
- ambiguous/censored outcome을 임의로 성공 처리하지 않는다.
- 동일 사건의 반복 forecast를 독립 sample처럼 세지 않는다.
- 공식 평가는 사전 고정된 horizon snapshot 하나만 사용한다.
- company plan 발표를 사건 실현으로 보지 않는다.
- Researcher confidence와 calibrated frequency를 같은 필드에 저장하지 않는다.
- calibration 결과로 현재 valuation에 맞게 probability를 조정하지 않는다.
- Pharma 성공확률 mapping을 backlog conversion 같은 다른 forecast class에 전이하지 않는다.
- Scenario weight를 독립 hypothesis probability의 단순 곱으로 만들지 않는다.

## 11. Calibration 적용 순서

1. 구현 완료: 선언된 ProbabilityForecast를 감사 통과 `LIVE_PRIMARY` run에서 append-only로 저장한다.
2. 구현 완료: outcome writer는 primary Evidence와 `first_seen_at`을 강제한다.
3. 구현 완료: binary event Brier/Brier Skill/log loss/ECE와 promotion gate를 계산한다.
4. 운영 중: 최소 표본 전에는 `UNCALIBRATED`를 유지하고 실제 해소 이력을 축적한다.
5. Calibration report를 만들되 valuation probability 자동변환은 하지 않는다.
6. 충분한 표본 후 forecast class·horizon별 mapping을 versioned config로 제안한다.
7. mapping 변경은 chronological holdout과 forward period에서 검증 후 적용한다.
8. Bear/Base/Bull RPS는 deterministic outcome resolver가 마련된 뒤에만 추가한다.

## 12. Live validation build order

1. OCI primary shadow로 Compiler, actual-unit commodity evaluator, holding SOTP를 검증한다.
2. Oracle로 단일기업 software FCF와 RPO/CAPEX guard를 검증한다.
3. GE Vernova로 비지주 operating-segment aggregator와 loss-contract guard를 검증한다.
4. Bloom으로 contract quality, order/energy/JV/financing 혼합을 검증한다.
5. 네 회사가 모두 통과한 뒤 financials/pharma evaluator를 추가한다.
6. 그 이후에만 scheduled research와 portfolio layer를 검토한다.

각 회사는 독립 regression fixture와 한 개 이상의 adversarial blocked fixture를 가져야 한다.
