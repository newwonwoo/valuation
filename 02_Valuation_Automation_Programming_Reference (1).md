# 범용 밸류에이션 자동화 프로그램 — 프로그래밍 참고 문서

## 0. 목표

사용자가 채팅창에서 다음과 같이 입력한다.

```text
분석시작
```

그러면 별도의 수동 엑셀 작업이나 프롬프트 재작성 없이, 현재 대화의 **활성 종목(active target)** 을 대상으로 아래 과정이 자동 실행된다.

```text
대상 확인
→ 산업 유형 판별
→ 1차 자료 수집
→ 사실/회사계획/정책/외부자료 분류
→ 재무·산업 검증
→ 선행행동/병목/정황 분석
→ 밸류에이션 모델 선택
→ 미래가치 확률반영
→ 수식·논리 감사
→ 현재주가와 마지막 비교
→ 다음 검증 이벤트 저장
```

이 문서는 ChatGPT 같은 LLM이 **채팅 명령을 오케스트레이션 신호로 사용**하도록 만드는 사양이다. 사용자가 직접 OpenAI API 호출 코드를 작성하는 방식을 전제로 하지 않는다.

---

# 1. 프로그램의 핵심 구조

범용 엔진은 6개 레이어로 나눈다.

## Layer A. Command Router

사용자 명령을 해석한다.

최소 명령:

```text
분석시작
```

규칙:
- 세션에 `active_target`이 있으면 해당 기업 분석 시작
- 사용자가 `분석시작 OCI홀딩스`처럼 종목명을 주면 active_target 갱신 후 시작
- 대상이 전혀 없을 때만 종목 입력 요청

선택적 보조 명령:

```text
갱신
가정검증
밸류업데이트
킬컨디션점검
근거보여줘
```

하지만 최종 UX의 핵심은 `분석시작` 하나로 전체 파이프라인을 실행하는 것이다.

---

## Layer B. Evidence Store

모든 수집 정보는 문장이 아니라 구조화된 객체로 저장한다.

```yaml
Evidence:
  id: EV-0001
  target: OCI Holdings
  type: realized | company_plan | policy | external | market_context
  metric: TerraSus_capacity_2029
  value: 70
  unit: kMT
  effective_date: 2029-12-31
  observed_date: 2026-07-23
  source_name: OCI Holdings 2Q26 IR
  source_url: ...
  source_grade: L1
  confidence: 1.0
  status: active
  notes: company target, not realized
```

중요:
- 사실과 계획을 하나의 필드에 덮어쓰지 않는다.
- 같은 지표가 여러 시점에 존재하면 버전으로 남긴다.
- 최신값만 쓰되 변경이력은 삭제하지 않는다.

---

## Layer C. Insight Graph

LLM이 하는 일은 “목표가 생성”이 아니라 **관찰된 사실을 연결해 가설 그래프를 만드는 것**이다.

```yaml
Hypothesis:
  id: HY-0012
  title: non_china_poly_scarcity
  statement: 미국의 비중국 고순도 폴리실리콘 자유물량이 수요 대비 부족할 가능성
  observations:
    - EV-0003
    - EV-0009
    - EV-0011
  causal_chain:
    - US policy barrier
    - low-cost China supply loses access/price advantage
    - available non-China merchant tons tighten
    - customer LTA/prepayment rises
    - OCI utilization/ASP improves
  supporting_evidence: []
  contradicting_evidence: []
  probability_prior: 0.50
  probability_current: 0.65
  kill_conditions: []
  next_checks: []
```

가설은 반드시 **원인 → 경제 변수 → 가치 변수** 경로를 가진다.

---

## Layer D. Evidence-to-Assumption Bridge

Insight Graph의 가설을 Valuation Engine의 입력으로 연결한다.

```yaml
Bridge:
  hypothesis_id: HY-0012
  target_assumption: poly_actual_asp
  direction: up
  base_value: 19.5
  candidate_value: 21.0
  probability_weight: 0.65
  mechanism: supply scarcity + LTA premium
  double_count_guard: HY-0012/poly_actual_asp
```

허용되는 주요 연결:

| 증거/가설 | 밸류 변수 |
|---|---|
| 계약·backlog | 물량 / 가동률 / 인식시점 |
| 장기계약·Take-or-Pay | 하방 가동률 / 마진 안정성 / 멀티플 |
| 선수금 | 순차입금 / Funding Gap |
| 가격하한/관세 | 직접 반영 금지 → 실제 ASP 가정으로 연결 |
| 경쟁사 증설 | 가동률 / ASP / 장기 멀티플 |
| qualification | 매출 발생확률 / 램프업 시점 |
| 정책 일몰 | 수요 확률 / 할인율 / 기타사업가치 |
| 전략 고객 | 계약조건 확인 시 마진/지속성/확률 |

Bridge 없이 가설이 목표가에 직접 영향을 주면 모델 오류로 처리한다.

---

## Layer E. Valuation Engine

산업 라우터가 모델을 선택한다.

```yaml
ModelRouter:
  order_equipment: backlog_normalized_ebitda_dcf
  construction_shipbuilding: progress_dcf
  materials_commodity: price_volume_margin_exit_multiple
  energy_infrastructure: asset_npv_sotp
  software_platform: revenue_grossprofit_fcf
  pharma_bio: rnpv
  financials: pb_roe_residual_income
  holding_company: sotp_nav_discount
```

한 회사에 복수 사업이 있으면 SOTP 하위에 서로 다른 모델을 넣는다.

---

## Layer F. Audit Engine

모델의 답보다 감사 결과가 우선한다.

FAIL이면 사용자에게 목표가를 내지 않는다.

필수 검사:

```text
[ ] 현재주가가 Fair Value 수식에 참조되지 않는가
[ ] 확률 합계 = 100%
[ ] EV → Equity 변환에서 순차입금 반영됐는가
[ ] 정책값이 기업 ASP/물량을 거치지 않고 직접 가치에 들어가지 않는가
[ ] 미래 CAPEX와 그 CAPEX가 만든 EBITDA를 이중차감하지 않는가
[ ] 동일 계약/가설이 판매량과 옵션가치에 중복 반영되지 않는가
[ ] 단위 변환 오류가 없는가
[ ] 할인시점이 일관적인가
[ ] 회사계획을 실현값으로 취급하지 않았는가
[ ] 수식 오류가 0건인가
```

---

# 2. `분석시작` 실행 상태 머신

```text
IDLE
  ↓ 분석시작
RESOLVE_TARGET
  ↓
CLASSIFY_INDUSTRY
  ↓
COLLECT_PRIMARY_SOURCES
  ↓
BUILD_EVIDENCE_LEDGER
  ↓
VALIDATE_FINANCIALS
  ↓
RUN_INSIGHT_SCAN
  ↓
BUILD_HYPOTHESES
  ↓
MAP_TO_ASSUMPTIONS
  ↓
RUN_VALUATION
  ↓
RUN_AUDIT
  ├─ FAIL → ERROR_REPORT / 재검증
  └─ PASS
       ↓
COMPARE_MARKET_PRICE
       ↓
GENERATE_OUTPUT
       ↓
SAVE_TRIGGERS_AND_STATE
```

현재주가는 `RUN_AUDIT` 통과 후 `COMPARE_MARKET_PRICE`에서 처음 사용한다.

---

# 3. 데이터 소스 우선순위

## 한국 기업
1. DART 정기·수시 공시
2. 회사 공식 IR/실적자료
3. 정부 정책/법령 원문
4. 거래소/공식 주가 데이터
5. 고객·공급사 공식 자료
6. Reuters/Bloomberg/산업기관/논문
7. 증권사 추정
8. 일반 언론·커뮤니티

## 미국 기업
1. SEC filing
2. 회사 Investor Relations
3. 정부/규제기관
4. 공식 고객·공급사 자료
5. 신뢰도 높은 통신사·연구기관
6. sell-side / media

규칙:
- 하위 출처가 상위 출처와 충돌하면 상위 출처 우선
- 하위 출처만 있는 정보는 `external` 또는 `unverified`로 유지
- 고객명·계약량 등 미확정 정보는 실현 레이어로 승격하지 않는다

---

# 4. 범용 데이터 모델

권장 `analysis_state.yaml` 구조:

```yaml
analysis:
  id: A-20260815-OCI
  target:
    name: OCI Holdings
    ticker: "010060"
    market: KRX
  as_of: 2026-08-15
  industry:
    primary: holding_company
    submodules:
      - materials_commodity
      - energy_infrastructure
      - project_development

  evidence: []
  company_plans: []
  policies: []
  market_context: []
  hypotheses: []
  bridges: []

  valuation:
    models: []
    scenarios: []
    expected_value: null
    current_price: null
    current_price_used_in_fair_value: false

  triggers: []
  kill_conditions: []
  audits: []
  change_log: []
```

---

# 5. 시나리오 객체

```yaml
Scenario:
  id: SC-BASE
  name: Base
  probability: 0.50
  assumptions:
    price: {}
    quantity: {}
    mix: {}
    yield: {}
    margin: {}
    capex: {}
    net_debt: {}
    discount_rate: null
    exit_multiple: null
  evidence_ids: []
  hypothesis_ids: []
  equity_value: null
  fair_value_per_share: null
```

중요:
- Bear/Base/Bull은 EBITDA 하나만 바뀌면 안 된다.
- 동일 세계관 안에서 **P, Q, margin, funding, multiple, discount rate**가 논리적으로 함께 이동한다.

---

# 6. 확률 엔진 설계

확률은 현재주가에서 역산하지 않는다.

권장 방식은 **Prior + Evidence Update**다.

```text
P(H | E) ∝ P(H) × Evidence Strength
```

실제 구현은 복잡한 베이지안 모델이 아니어도 된다. 초기 버전은 설명 가능한 점수제로 시작할 수 있다.

예시 Evidence Strength:

- 5: 현금 입금 / 공식 계약 / 법적 효력 발생
- 4: 공식 CAPEX 착공 / 고객 qualification 완료 / 정부 원문
- 3: 회사 공식 계획 + 구체적 실행행동
- 2: 신뢰도 높은 외부 보도 / 채용 / 허가 / 공급사 행동
- 1: 정황성 기사 / 업계 추정
- -1~-5: 반증 증거

단, 점수→확률 매핑은 **config 파일에서 조정 가능**하게 만든다. 하드코딩된 진리가 아니다.

추천:

```yaml
probability_config:
  score_bands:
    - {min: -999, max: -4, probability: 0.10}
    - {min: -3, max: -1, probability: 0.25}
    - {min: 0, max: 2, probability: 0.40}
    - {min: 3, max: 5, probability: 0.60}
    - {min: 6, max: 8, probability: 0.75}
    - {min: 9, max: 999, probability: 0.90}
```

운영 후 과거 사례로 calibration 해야 한다.

---

# 7. 인사이트 스캐너 인터페이스

각 스캐너는 공통 형식을 반환한다.

```yaml
ScannerResult:
  scanner: bottleneck_scanner
  observations: []
  hypotheses_created: []
  valuation_variables_impacted: []
  confidence: 0.0
  next_checks: []
```

초기 Scanner 세트:

1. headline_decomposition
2. money_flow
3. bottleneck
4. behavior_sequence
5. exploration_confirmation
6. digital_trace
7. pxq_mix_yield
8. chain_proof
9. long_short
10. bottleneck_migration
11. second_order_effect
12. reserved_demand_slot
13. time_to_power
14. policy_revealed_preference
15. raw_material_policy_floor
16. quarterly_question_ledger
17. ipo_lockup_flow
18. macro_transmission
19. customer_concentration
20. kill_condition

---

# 8. 기존 OCI 엑셀 엔진 v1.1 설명

현재 `OCI_Holdings_Valuation_Skill_v1.1.xlsx`는 범용 프로그램의 **프로토타입**이다.

## 시트 구조

### `00_Skill_Rules`
모델 불변 규칙.

핵심:
- 현재주가 앵커링 ZERO
- 회사계획 ≠ 실현값
- MIP ≠ 실제 ASP
- CAPEX 이중차감 금지
- 미래가치 확률반영
- 변경 로그 필수

### `01_Facts`
실현·공시값과 시장 비교값.

프로그램의 `EvidenceStore.realized`에 해당한다.

### `02_Company_Plan`
회사 공식 IR 계획.

프로그램의 `EvidenceStore.company_plan`.

### `03_Model_Assumptions`
수정 가능한 모델 입력.

프로그램에서는 `ValuationAssumption` 객체로 대체한다.

### `04_Scenario_Engine`
Bear/Base/Bull/AI-Space의 경제 변수 계산.

프로그램에서는 `ScenarioEngine` 클래스.

### `05_Valuation`
기업가치 → 지분가치 → 주당가치 계산.

프로그램에서는 `ValuationEngine`.

### `06_Catalyst_Check`
어떤 이벤트가 어떤 가정을 바꾸는지 기록.

프로그램의 `TriggerEngine`.

### `07_Source_Audit`
가정 변경 감사로그.

프로그램의 `ChangeLog`.

### `08_Dashboard`
사용자 출력.

프로그램의 `ReportRenderer`.

### `09_Formula_Audit`
수식·단위·확률 무결성 검사.

프로그램의 `AuditEngine`.

### `10_Assistant_Runbook`
LLM이 엑셀을 업데이트하는 순서.

프로그램의 `Orchestrator Policy`.

---

# 9. OCI 엑셀의 핵심 수식

## 9.1 폴리실리콘

```text
EBITDA/kg
= Actual ASP
- Cash Cost
- Other Cash EBITDA Cost
```

```text
Poly EBITDA (KRW trillion)
= Capacity_kMT × 1,000,000
× Utilization
× EBITDA_per_kg_USD
× FX_KRW_USD
÷ 1,000,000,000,000
```

## 9.2 웨이퍼

```text
Wafer EBITDA (KRW trillion)
= Capacity_GW × 1,000,000,000
× Utilization
× Additional_EBITDA_per_W_USD
× FX
× Economic_Ownership
÷ 1,000,000,000,000
```

중요: 웨이퍼는 폴리실리콘의 전체 이익을 다시 계산하지 않고 **추가 가공 EBITDA만** 반영한다.

## 9.3 기업가치

```text
Poly EV   = Poly EBITDA × Poly EV/EBITDA
Wafer EV  = Wafer EBITDA × Wafer EV/EBITDA
Terminal EV = Poly EV + Wafer EV
```

```text
Terminal Core Equity
= Terminal EV - Terminal Net Debt
```

```text
PV Core Equity
= Terminal Core Equity / (1 + Discount Rate)^Years
```

```text
Total Equity Value
= PV Core Equity + Other Business Current Value
```

```text
Fair Value Per Share
= Total Equity Value × 1e12 / Shares Outstanding
```

```text
Probability Weighted Value
= Σ(Scenario Probability × Scenario Fair Value)
```

현재주가는 이 수식들 어디에도 들어가면 안 된다.

---

# 10. 기존 OCI 엑셀에서 범용 프로그램으로 가져갈 불변 테스트

## Test A. Current Price Isolation

```python
before = fair_value()
market_price *= 0.5
after = fair_value()
assert before == after
```

## Test B. Policy-to-Economics Isolation

```python
before = fair_value()
policy_mip = 30
# ASP assumption unchanged
assert fair_value() == before
```

정책이 자동으로 회사 가격이 되는 오류를 막는다.

## Test C. Economic Sensitivity

```python
base = fair_value()
actual_asp += 1
assert fair_value() > base
```

## Test D. Probability Integrity

```python
assert abs(sum(s.probability for s in scenarios) - 1.0) < tolerance
```

## Test E. Unit Consistency

- kMT → kg
- GW → W
- million/billion/trillion
- KRW/USD
- ownership %

## Test F. Double Count Guard

```text
same evidence_id + same economic_path
→ only one valuation impact allowed
```

---

# 11. 범용 모델 클래스 설계 예시

```python
class AnalysisOrchestrator:
    def run(self, command, context):
        target = self.resolve_target(command, context)
        industry = self.industry_router.classify(target)
        evidence = self.collector.collect(target, industry)
        ledger = self.evidence_store.normalize(evidence)

        hypotheses = self.insight_engine.scan(ledger, industry)
        bridges = self.bridge_engine.map(hypotheses, ledger)

        model = self.model_router.create(industry, ledger)
        scenarios = self.scenario_engine.build(model, bridges)

        audit = self.audit_engine.run(model, scenarios)
        if not audit.pass_all:
            return self.render_audit_failure(audit)

        valuation = self.valuation_engine.calculate(model, scenarios)
        market = self.market_context.load_after_valuation(target)

        report = self.renderer.render(
            ledger=ledger,
            hypotheses=hypotheses,
            valuation=valuation,
            market=market,
            triggers=self.trigger_engine.build(hypotheses, ledger),
        )
        self.state_store.save(...)
        return report
```

---

# 12. LLM의 역할과 코드의 역할을 분리한다

## LLM이 잘하는 일
- 산업 구조 이해
- 기사/공시 문맥 해석
- 행동 순서 연결
- 경쟁 가설 생성
- Kill Condition 작성
- 증거가 어떤 경제변수에 영향을 주는지 판단
- 모순 탐지

## 코드가 맡아야 할 일
- 수식
- 단위변환
- DCF
- 확률 합산
- 시나리오 계산
- 데이터 스키마 검증
- 현재주가 참조 차단
- 중복 반영 탐지
- 변경 로그

원칙:

> **LLM은 의미를 판단하고, 코드는 숫자의 일관성을 강제한다.**

LLM에게 계산까지 자유롭게 맡기면 기준이 다시 흔들릴 가능성이 높다.

---

# 13. 채팅 UX 설계

사용자:

```text
분석시작
```

권장 진행 표시:

```text
[1/8] 대상/산업 판별
[2/8] 공시·IR·정책 수집
[3/8] 실적 및 재무 검증
[4/8] 돈의 흐름·병목·선행행동 분석
[5/8] 미래 가설 및 반증 검토
[6/8] 산업별 밸류 모델 실행
[7/8] 수식·논리 감사
[8/8] 미래가치 확률반영 및 현재가격 비교
```

최종 답변은 상세 계산 로그를 전부 노출할 필요는 없지만 다음은 보여준다.

```text
결론
핵심 자산가치
미래가치 확률반영
검증 Bull
현재가 비교
핵심 가정
무엇이 이미 사실인지
무엇이 회사 계획인지
무엇이 추정인지
상향 트리거
하향 Kill Condition
```

---

# 14. 자동 업데이트 규칙

분석할 때마다 모든 가정을 바꾸지 않는다.

`AssumptionChangePolicy`:

```yaml
change_allowed_if:
  - new_realized_result
  - new_official_guidance
  - policy_effective_change
  - contract_confirmed
  - prepayment_confirmed
  - capacity_startup_confirmed
  - material_competitor_supply_change
  - kill_condition_triggered
```

단순한 주가 상승/하락은 가정 변경 사유가 아니다.

---

# 15. 개발 단계 제안

## Phase 1 — Excel-backed prototype
- 현재 OCI 엑셀 구조를 범용 템플릿으로 변경
- 입력 JSON/YAML → Excel assumptions 자동 매핑
- 산업별 valuation module 추가
- Formula Audit 자동화

## Phase 2 — Structured State
- `analysis_state.yaml`
- `evidence_ledger.json`
- `hypothesis_graph.json`
- `change_log.json`

엑셀은 계산 결과 검증 및 사람이 보는 디버깅 도구로 유지한다.

## Phase 3 — Chat command orchestration
- `분석시작` command router
- active_target 세션 유지
- 자동 데이터 수집/분류
- LLM insight scanner 실행
- valuation engine 실행

## Phase 4 — Calibration
- 과거 분석 결과를 저장
- 확률이 실제 계약/실적로 얼마나 적중했는지 측정
- scanner별 신뢰도 재조정
- 산업별 prior 재학습

---

# 16. 최소 파일 구조 제안

```text
valuation-system/
├─ SKILL.md
├─ config/
│  ├─ source_grades.yaml
│  ├─ probability.yaml
│  └─ model_router.yaml
├─ state/
│  └─ <ticker>/analysis_state.yaml
├─ engine/
│  ├─ orchestrator.py
│  ├─ evidence.py
│  ├─ insight.py
│  ├─ bridge.py
│  ├─ scenario.py
│  ├─ audit.py
│  └─ valuation/
│     ├─ dcf.py
│     ├─ sotp.py
│     ├─ commodity.py
│     ├─ rnpv.py
│     └─ financials.py
├─ scanners/
│  ├─ money_flow.py
│  ├─ bottleneck.py
│  ├─ behavior_sequence.py
│  ├─ policy.py
│  ├─ quarterly_validation.py
│  └─ macro_transmission.py
├─ templates/
│  └─ valuation_engine.xlsx
└─ tests/
   ├─ test_price_anchor.py
   ├─ test_policy_bridge.py
   ├─ test_units.py
   ├─ test_probability.py
   └─ test_double_count.py
```

---

# 17. 프로그램이 절대 하면 안 되는 것

1. 현재주가를 보고 Base 가정을 정한다.
2. 정책 가격을 기업 ASP로 자동 치환한다.
3. 증권사 목표가 평균을 모델 입력값으로 사용한다.
4. 회사 목표를 실현 실적으로 표시한다.
5. CAPEX와 증설 EBITDA를 이중차감한다.
6. 같은 계약을 물량, 옵션, 멀티플에 중복 반영한다.
7. Bear/Base/Bull에서 한 변수만 움직인다.
8. 산업 유형과 맞지 않는 DCF/PER를 강제로 적용한다.
9. 수식 Audit 실패 상태에서 목표가를 출력한다.
10. 새 기사 하나만으로 확률을 크게 변경한다.

---

# 18. 이 프로그램의 최종 철학

전통 밸류에이션은 “현재 숫자의 정확도”에 강하다.
인사이트 분석은 “다음 숫자가 만들어지는 과정”에 강하다.

이 프로그램의 목표는 둘을 섞어 흐리게 만드는 것이 아니다.

```text
확정된 가치
+
근거가 있는 미래 변화 × 확률
=
미래가치 확률반영
```

그리고 마지막에만:

```text
미래가치 확률반영 ↔ 현재 시장가격
```

을 비교한다.

그렇게 해야 **통찰은 살리고, 밸류에이션 기준은 흔들리지 않는다.**

