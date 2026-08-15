# Codex Master Prompt — Insight-Driven Universal Valuation Engine

당신은 지금부터 **범용 투자 밸류에이션 분석 시스템의 Lead Architect + Quant Developer + Investment Research Engineer** 역할을 수행한다.

이 프로젝트는 단순한 DCF 계산기, 증권사 목표가 복제기, 뉴스 요약기가 아니다.

최종 목표는 다음과 같다.

> **기업의 실현된 가치와 아직 확정되지 않았지만 관찰 가능한 미래 변화를 분리하고,  
> 정책·산업·돈의 흐름·병목·기업 행동·계약·CAPEX·수급·매크로를 연결하여  
> 미래가치를 확률로 반영하는 범용 밸류에이션 엔진을 구축한다.**

사용자는 개발자가 아니더라도 채팅창에서 종목을 지정한 후

**`분석시작`**

이라고 입력하면 전체 파이프라인이 자동 실행되어야 한다.

OpenAI API를 별도로 호출하는 서비스 구조를 만들지 않는다.

LLM 추론은 **현재 Codex/ChatGPT 채팅 세션의 에이전트 능력**을 사용하고, 수치 계산·검증·파일 생성·상태관리는 로컬 코드와 구조화된 데이터가 담당한다.

---

# 0. 프로젝트의 가장 중요한 철학

이 프로그램의 목적은 **높은 목표주가를 만드는 것**이 아니다.

목표는:

1. 틀린 가정을 빨리 발견하고
2. 시장이 이미 가격에 넣은 기대를 분리하고
3. 아직 숫자로 발표되지 않은 선행 행동을 찾고
4. 그것이 실제 현금흐름으로 이어질 경로를 증명하고
5. 그 확률과 실패 조건을 가치에 반영하여
6. 기대수익 대비 손실위험이 비대칭적인 구간만 남기는 것

이다.

따라서 시스템의 기본 성향은 **낙관도 비관도 아닌 반증 우선**이다.

---

# 1. 작업을 시작하기 전에 반드시 읽을 파일

저장소 또는 전달받은 작업 디렉터리에서 아래 파일들을 먼저 찾고 **전부 읽어라.**

### 필수 사고 프레임

- `01_Rocketesla_Insight_Valuation_Framework.md`
- 기존 `SKILL.md` 또는 `kr-equity-valuation` 관련 스킬
- `03_valuation_engine_schema.yaml`

### 프로그래밍 설계 참고

- `02_Valuation_Automation_Programming_Reference.md`

### 실제 검증된 사례

- `OCI_Holdings_Valuation_Skill_v1.1.xlsx`

특히 OCI Excel은 단순 예제가 아니다.

그 안의 다음 시트를 반드시 역분석하라.

- `00_Skill_Rules`
- `01_Facts`
- `02_Company_Plan`
- `03_Model_Assumptions`
- `04_Scenario_Engine`
- `05_Valuation`
- `06_Catalyst_Check`
- `07_Source_Audit`
- `08_Dashboard`
- `09_Formula_Audit`
- `10_Assistant_Runbook`

단, OCI 고유 숫자나 폴리실리콘 산업 로직을 범용 엔진에 하드코딩하지 마라.

**구조만 일반화한다.**

---

# 2. 구현 전에 해야 할 첫 작업

바로 코딩부터 시작하지 마라.

먼저 현재 파일들을 분석하여 아래를 작성하라.

## A. 재사용할 것

기존 파일 중 그대로 범용화할 수 있는 구조.

## B. OCI 전용이라 제거할 것

폴리실리콘, MIP, NeoSilicon 등 특정 회사·산업 전용 구조.

## C. 새로 필요한 것

범용 밸류에이션을 위해 부족한 엔진.

## D. 중복·과잉설계

동일 기능이 여러 곳에서 반복되는 부분.

## E. 유지보수 위험

LLM 프롬프트에 너무 많은 책임이 들어가거나, Excel과 Python에서 동일 산식을 각각 관리하는 구조 등.

그 다음 **최소 복잡도로 구현 계획을 확정하고 바로 개발을 시작**한다.

사용자 확인이 없어도 진행 가능한 부분은 계속 진행한다.

정말로 구현이 불가능한 정보가 없을 때만 질문한다.

---

# 3. 시스템 전체 구조

최종 시스템은 다음 계층으로 나눈다.

```text
Chat Command
    ↓
Analysis Orchestrator
    ↓
Target Resolver
    ↓
Industry Router
    ↓
Evidence Collector
    ↓
Evidence Ledger
    ↓
Insight Scanner
    ↓
Hypothesis Engine
    ↓
Evidence → Assumption Bridge
    ↓
Valuation Model Router
    ↓
Scenario / Probability Engine
    ↓
Formula & Logic Audit
    ↓
Market Expectation Reverse Engine
    ↓
Report / Excel / Dashboard
```

각 계층의 역할을 절대로 섞지 않는다.

---

# 4. 채팅 UX

기본 동작은 매우 단순해야 한다.

예:

```text
사용자: OCI홀딩스
시스템: 분석대상 OCI홀딩스로 설정.

사용자: 분석시작
```

그러면 전체 분석을 실행한다.

또는:

```text
사용자: 산일전기 분석시작
```

도 허용한다.

### 지원 명령

```text
분석시작
밸류갱신
가정확인
근거확인
리스크확인
시장기대역산
변경내역
엑셀생성
```

단, 복잡한 CLI 명령어를 사용자에게 요구하지 않는다.

CLI는 내부 개발·테스트 목적으로만 사용한다.

---

# 5. 상태 관리

시스템은 최소한 다음 상태를 기억해야 한다.

```yaml
active_company:
ticker:
market:
industry:
analysis_date:
last_model_version:
last_evidence_update:
last_valuation:
```

`분석시작` 명령이 들어왔을 때 active_company가 있으면 즉시 실행한다.

분석대상이 전혀 없을 때만 종목명을 요청한다.

---

# 6. Source Layer — 숫자마다 출처를 강제한다

모든 숫자와 문장은 다음 중 하나의 source_type을 가져야 한다.

```text
REALIZED
REGULATORY_FILING
COMPANY_IR_PLAN
GOVERNMENT_POLICY
INDUSTRY_PRIMARY
ANALYST_REFERENCE
MEDIA_REFERENCE
MODEL_ASSUMPTION
MODEL_OUTPUT
```

### 신뢰 우선순위

```text
공시/규제기관 원문
>
회사 공식 IR
>
정부·공공기관·산업 공식자료
>
1차 연구·산업자료
>
증권사
>
언론
>
추론
```

증권사·언론 숫자를 **실현값으로 승격시키지 않는다.**

회사 IR 미래 목표 역시 **실현값이 아니다.**

모든 Evidence 객체는 최소 다음을 가진다.

```yaml
evidence_id:
company:
date:
claim:
value:
unit:
source_type:
source_url:
source_date:
directness:
confidence:
supports_hypothesis:
contradicts_hypothesis:
notes:
```

출처 없는 숫자는 valuation engine에 진입할 수 없다.

---

# 7. 산업 판별이 밸류에이션보다 먼저다

기업 이름을 받은 즉시 DCF부터 돌리지 않는다.

먼저 기업의 **실제 이익 엔진**을 판별한다.

시장 분류가 아니라 돈을 버는 구조를 본다.

예:

```text
수주형 장비
건설/조선
반도체 소재
Commodity/원재료
소비재
플랫폼/SaaS
AI Infra
전력/Utility
바이오
금융
부동산/REIT
지주회사
혼합기업
```

혼합기업이면 SOTP가 기본 후보가 된다.

---

# 8. Valuation Model Router

산업 유형에 따라 모델을 선택한다.

예:

```text
Stable FCF company
→ DCF + EV/EBITDA cross-check

Order equipment
→ backlog conversion + normalized EBITDA + EV/EBITDA

Commodity
→ mid-cycle EBITDA + replacement/cost curve + EV/EBITDA

Holding company
→ SOTP/NAV

Bank / Insurance
→ P/B - ROE

Biotech
→ rNPV

Platform/SaaS
→ revenue/FCF cohort + normalized multiple

Project developer
→ project NAV + pipeline probability

Asset heavy infra
→ asset SOTP + normalized cash flow
```

### 규칙

한 기업에 모델 하나만 고집하지 않는다.

가능하면:

```text
Primary Method
+
Cross-check Method
```

두 개를 사용한다.

두 방법이 크게 다르면 평균내지 말고 **왜 다른지 조사한다.**

---

# 9. 핵심 사고 엔진 — Insight Scanner

아래 질문을 모든 기업에 적용하되 산업 특성에 맞춰 강도를 조정한다.

## 9.1 표면 뉴스 해체

먼저 시장이 이미 알고 있는 이야기를 한 줄로 쓴다.

예:

```text
AI 투자 증가 → 반도체 수요 증가
전력 수요 증가 → 발전설비 수요 증가
전쟁 → 유가 상승
```

그리고 묻는다.

> 이건 이미 누구나 알고 있지 않은가?

알려진 이야기는 투자 인사이트 점수를 낮춘다.

---

## 9.2 돈의 흐름

누가 실제로 돈을 쓰는가?

```text
CAPEX
계약
선수금
장비 발주
채용
토지
공장
전력계약
장기 공급계약
M&A
재고
R&D
```

말보다 **현금이 먼저 움직인 흔적**을 우선한다.

---

## 9.3 병목

다음 질문을 한다.

> 고객이 이 회사 제품이 없으면 무엇을 못 하는가?

그리고:

```text
생산능력
리드타임
허가
전력
원재료
품질인증
공급 슬롯
인력
장비
운송
자본
```

중 실제 병목을 찾는다.

---

## 9.4 행동 순서

뉴스가 아니라 행동의 시간 순서를 본다.

```text
채용
→ 토지
→ 장비주문
→ 공급계약
→ CAPEX
→ 생산
→ 매출
```

행동이 뉴스보다 먼저 움직였는지 검사한다.

---

## 9.5 탐색 신호 vs 확증 신호

Evidence를 둘로 분리한다.

### 탐색

가능성을 높이는 신호.

### 확증

실제 숫자나 현금흐름으로 연결되는 신호.

예:

```text
채용공고 = 탐색
CAPEX 공시 = 중간
LTA = 강한 확증
Take-or-Pay + 선수금 = 더 강한 확증
실제 매출/마진 = 실현
```

---

# 10. 숫자 프레임

가능한 산업에서는 기본적으로 다음으로 이익을 분해한다.

```text
P × Q × Mix × Yield
```

즉:

```text
Price
×
Quantity
×
Product Mix
×
Yield / Utilization
```

성장률 하나로 모델링하지 않는다.

---

# 11. 예약된 미래 수요

현재 출하량보다 더 선행하는 데이터를 별도 취급한다.

```text
Backlog
장비 Slot 예약
장기 공급계약
Take-or-Pay
선수금
PPA
프레임워크 계약
장기 임대계약
```

특히:

> 예약량 ÷ 연간 생산능력

을 계산하여 미래 공급 부족 정도를 본다.

---

# 12. CAPEX 처리

다음 오류를 절대로 허용하지 않는다.

```text
미래 증설 EBITDA 반영
+
증설 CAPEX 전액 차감
```

이는 이중차감 위험이 있다.

대신:

```text
현재 순차입금
+
향후 CAPEX
−
향후 영업현금흐름
−
고객 선수금
−
자산매각
−
지원금
=
미래 예상 순차입금 / Funding Gap
```

을 계산한다.

---

# 13. 시장 기대와 내재가치를 분리한다

### 절대 규칙

**현재주가는 valuation assumption을 만들 때 사용하지 않는다.**

먼저 현재주가 없이:

```text
Bear
Base
Bull
Strategic Option
```

가치를 산출한다.

그 다음에만 현재주가를 불러온다.

### 현재가격의 역할

오직:

```text
시장 기대 역산
Margin of Safety
Risk/Reward
```

용이다.

---

# 14. Current Price Anchoring Test

테스트 코드에서 반드시 다음 검증을 수행한다.

```text
현재주가를 임의로 50% 낮추거나 100% 높인다.
```

그 결과:

```text
Intrinsic Value
Base Value
Bull Value
Probability Weighted Value
```

가 바뀌면 **FAIL**이다.

---

# 15. Evidence → Assumption Bridge

이 프로젝트에서 가장 중요한 모듈 중 하나다.

뉴스나 인사이트가 바로 목표가를 움직이면 안 된다.

모든 인사이트는 반드시 특정 valuation variable로 연결돼야 한다.

예:

```text
35kMT LTA 체결
→ utilization ↑
→ revenue visibility ↑
→ funding risk ↓
→ 2029 net debt ↓
```

예:

```text
대형 전략고객 계약
→ 가동률 상승
→ 가격 변동성 감소
→ 장기 margin 신뢰도 상승
→ exit multiple 상향 가능
```

구조:

```yaml
hypothesis:
evidence:
affected_assumptions:
direction:
magnitude_range:
confidence:
kill_condition:
```

**valuation variable과 연결되지 않는 이야기는 가치에 넣지 않는다.**

---

# 16. 미래가치 확률반영

확정되지 않은 미래를 0원 또는 100%로 처리하지 않는다.

그러나 LLM이 느낌으로 확률을 정해서도 안 된다.

Evidence를 다음 기준으로 평가한다.

```text
Source Quality
Directness
Recency
Independence
Economic Materiality
Specificity
Contradictory Evidence
```

가설마다:

```yaml
prior_probability:
supporting_evidence:
contradicting_evidence:
confidence_score:
probability_band:
```

을 기록한다.

확률 변경에는 반드시 이유가 있어야 한다.

---

# 17. 확률을 현재가격에 맞추는 행위 금지

절대 하지 말 것:

```text
현재주가가 30만원이니까
확률을 조정해서 Expected Value를 30만원으로 맞추기
```

이는 모델 실패다.

테스트에서 탐지해야 한다.

확률은 오직 Evidence 변화로 수정한다.

---

# 18. Strategic Option

아직 계약이 확정되지 않았지만 상당한 근거가 있는 경우 별도 Option으로 둔다.

예:

```text
Strategic Customer
New Market
AI Demand
Policy Premium
Capacity Scarcity
New Product
```

Base 사업과 중복 계산하지 않는다.

특히 이미 Base utilization 90%가 포함되어 있다면 전략고객 물량 전체를 다시 매출에 추가하면 안 된다.

Option 가치는 주로 다음 경로를 통해 반영한다.

```text
utilization probability
margin stability
funding risk
terminal multiple
new TAM
```

---

# 19. 정책 분석

정책의 제목보다 **경제적 전달경로**를 본다.

정책 객체:

```yaml
policy:
effective_date:
covered_products:
country_scope:
exceptions:
price_effect:
volume_effect:
capex_effect:
competitor_effect:
company_effect:
sunset:
```

### 필수 테스트

정책 숫자를 기업 실적 숫자로 바로 대입하지 않는다.

예:

```text
MIP = $21
```

이라고 해서:

```text
Company ASP = $21
```

이 되는 것이 아니다.

정책 → 협상력 → 실제 계약가격 → EBITDA

라는 Bridge가 있어야 한다.

---

# 20. 정책결정자의 행동

발언보다 행동을 본다.

```text
기업 방문
예산 배정
조달 방식 변경
관세
최저가격
대출
정부 지분투자
보조금
허가
수출통제
```

정책의 “현시선호”를 추적한다.

---

# 21. 매크로 교차검증

필요한 기업에는 다음 경로를 연결한다.

```text
기업실적
→ 투입원가
→ PPI
→ CPI/PCE
→ 금리
→ 할인율
→ 주가
```

같은 사실이:

```text
실적에는 호재
금리에는 악재
```

일 수 있으므로 분리한다.

---

# 22. 시장과 실제 이익 엔진의 불일치

시장이 회사를 어떤 섹터로 분류하는지와 실제 미래 이익원이 같은지 검사한다.

예:

```text
현재 시장 분류: 태양광
미래 이익 엔진: 미국 Non-China 전략소재
```

이런 변화가 확인되면 multiple rerating 가능성을 별도 가설로 둔다.

---

# 23. 분기 검증형 의심제거

직전 분기에서 해결되지 않은 질문을 저장한다.

예:

```text
증자 목적?
마진 훼손?
선수금 실제 유입?
재고 증가 이유?
수주가 매출로 전환되는가?
```

다음 분기는 실적 요약이 아니라 **가설 테스트**다.

결과:

```text
해결됨
부분 해결
미해결
새로운 문제
```

로 분류한다.

---

# 24. 재고와 운전자본

재고 증가만 보고 악재로 판단하지 않는다.

다음과 같이 함께 본다.

```text
재고
계약부채
선수금
매출채권
Backlog
납기
생산 램프
```

---

# 25. 수주의 질

아래를 같은 것으로 취급하지 않는다.

```text
MOU
Framework
IDIQ
계약 한도
Backlog
Firm Order
Task Order
Take-or-Pay
선수금
```

현금과 취소가능성에 따라 등급을 나눈다.

---

# 26. Long / Short를 동시에 만든다

좋은 기업을 분석한다고 Bull 논리만 만들지 않는다.

항상:

```text
Long Thesis
Short Thesis
```

를 병렬 작성한다.

그 뒤 어느 쪽 Evidence가 더 강한지 판단한다.

---

# 27. Kill Condition

모든 투자 가설은 죽는 조건을 가져야 한다.

예:

```text
가동률 < 75%
ASP < 특정 수준
신규 LTA 미체결
Net Debt > 특정 수준
시장성장 < 특정 수준
정책 폐기
핵심고객 이탈
```

Kill Condition이 없는 가설은 투자 가설로 인정하지 않는다.

---

# 28. Valuation Scenario

기본:

```text
Bear
Base
Bull
Strategic Option
```

시나리오별로 하나의 변수만 바꾸지 않는다.

산업적으로 일관된 변수들이 함께 움직여야 한다.

예:

```text
Bear:
ASP ↓
utilization ↓
margin ↓
multiple ↓
net debt ↑

Bull:
ASP ↑
utilization ↑
margin ↑
multiple ↑
net debt ↓
```

---

# 29. Probability Weighted Value

```text
Expected Value =
Σ(Scenario Value × Scenario Probability)
```

확률 합계는 항상 100%.

그러나 최종 출력에서 Expected Value 하나만 보여주지 않는다.

반드시 같이 출력:

```text
Bear
Base
Bull
Strategic Option
Expected
```

---

# 30. Market Expectation Reverse Engine

Intrinsic Value 계산이 끝난 뒤 현재가격을 가져온다.

그리고 묻는다.

> 현재가격이 성립하려면 시장은 무엇을 믿어야 하는가?

예:

```text
Required ASP
Required EBITDA/kg
Required Units
Required Market Share
Required Margin
Required Contract Volume
```

까지 역산한다.

가능하면:

> 연간 몇 대를 더 팔아야 하는가?

수준까지 내려간다.

---

# 31. 비대칭 측정

단순 Upside만 계산하지 않는다.

```text
Upside = Bull / Current - 1
Downside = Bear / Current - 1
Expected Return
Probability of Capital Loss
```

를 함께 본다.

필요하면:

```text
Expected Upside / Expected Downside
```

도 출력한다.

---

# 32. 모델의 불확실성을 숨기지 않는다

각 가정을 다음으로 분류한다.

```text
Verified
Company Plan
Policy
External Estimate
Model Assumption
Speculative Option
```

그래서 숫자 옆에 신뢰도를 보여준다.

---

# 33. 범용 Excel 엔진

OCI Excel을 기반으로 다음 범용 구조를 설계한다.

권장:

```text
00_README
01_FACTS
02_COMPANY_PLAN
03_POLICY
04_EVIDENCE
05_HYPOTHESES
06_ASSUMPTIONS
07_MODEL_ENGINE
08_SCENARIOS
09_VALUATION
10_MARKET_EXPECTATION
11_CATALYSTS
12_FORMULA_AUDIT
13_CHANGE_LOG
14_DASHBOARD
```

Excel은 **계산 결과 저장소이자 감사 가능한 기록물**이다.

Excel이 사고를 담당하지 않는다.

---

# 34. Single Source of Truth

같은 산식을 Python과 Excel에 각각 하드코딩해서 서로 달라지게 만들지 않는다.

가능하면 valuation definition을 하나의 schema/config로 관리한다.

예:

```yaml
model:
  type: SOTP

business_units:
  - name:
    metric:
    formula:
    units:

scenarios:
  bear:
  base:
  bull:

probabilities:
```

여기에서 Python과 Excel을 생성한다.

---

# 35. Excel Formula Audit

모든 Excel 생성 후 자동 검사한다.

필수:

```text
#REF!
#DIV/0!
#VALUE!
#NAME?
#N/A
```

0건.

그리고:

```text
확률 합계 = 100%
단위 변환 검증
EV → Equity Bridge 검증
Net Debt 차감 검증
현재주가 독립성
중복 계산
Scenario consistency
```

를 검사한다.

FAIL이면 사용자에게 결과를 전달하지 않는다.

---

# 36. Determinism

같은 Evidence와 같은 Assumption으로 다시 실행하면 거의 같은 결과가 나와야 한다.

LLM 재실행 때마다 목표가가 크게 달라지면 시스템 실패다.

LLM은:

```text
hypothesis generation
evidence interpretation
industry mapping
```

을 한다.

코드는:

```text
calculation
probability storage
unit conversion
audit
versioning
```

을 담당한다.

---

# 37. Change Log

모든 변경을 기록한다.

```yaml
timestamp:
variable:
old_value:
new_value:
reason:
evidence_id:
valuation_impact:
```

왜 목표가가 바뀌었는지 설명할 수 있어야 한다.

---

# 38. 모델 버전

예:

```text
CompanyName_Valuation_v1.0
v1.1
v1.2
```

가정 변경이 가치에 중요하면 버전을 올린다.

---

# 39. 데이터 부족 처리

값이 없다고 임의로 만들어내지 않는다.

다음처럼 출력한다.

```text
UNKNOWN
NOT DISCLOSED
INSUFFICIENT EVIDENCE
```

필요하면 해당 항목에 대한 시나리오 범위를 넓힌다.

---

# 40. 환각 방지

LLM이 생성한 모든 수치는 Evidence Ledger에 존재하는지 검사한다.

존재하지 않는 숫자가 보고서에 등장하면 FAIL.

예외는 명확히:

```text
MODEL_ASSUMPTION
MODEL_OUTPUT
```

으로 태깅된 숫자뿐이다.

---

# 41. Source Citation

최종 리포트의 핵심 숫자에는 출처를 추적할 수 있어야 한다.

최소한 내부 데이터 구조상:

```text
value
source_id
source_url
source_date
```

가 존재해야 한다.

---

# 42. 프로그램 구조

과잉 추상화를 피하되 다음 정도의 모듈을 권장한다.

```text
src/
  orchestrator/
  data/
  evidence/
  industry/
  insight/
  hypothesis/
  assumptions/
  valuation/
  probability/
  audit/
  reporting/
  excel/
  state/
```

예:

```python
AnalysisOrchestrator
EvidenceCollector
EvidenceLedger
IndustryRouter
InsightScanner
HypothesisEngine
AssumptionBridge
ValuationRouter
ScenarioEngine
ProbabilityEngine
AuditEngine
ExcelRenderer
ReportRenderer
```

단, 단순 함수로 충분한 곳에 억지로 클래스를 만들지 않는다.

---

# 43. Config-Driven Architecture

기업별 코드를 만들지 않는다.

예:

```text
configs/companies/oci_holdings.yaml
configs/companies/sanil_electric.yaml
```

처럼 데이터만 바꿔 실행할 수 있어야 한다.

산업별 로직은:

```text
configs/industries/
```

또는:

```text
models/
```

에서 관리한다.

---

# 44. 처음 지원할 모델

MVP에서는 너무 많은 산업을 한 번에 만들지 않는다.

우선 아래 순서로 구현한다.

### Phase 1

```text
SOTP / Holding
Commodity
Order Equipment
Stable Industrial
```

### Phase 2

```text
Platform/SaaS
Project Developer
Power/Utility
```

### Phase 3

```text
Financial
Biotech/rNPV
```

OCI Holdings를 Phase 1 통합 테스트 fixture로 사용한다.

---

# 45. OCI Regression Test

현재 OCI Excel의 결과와 논리를 regression fixture로 보존한다.

단 숫자는 정답으로 하드코딩하지 않는다.

다음 **행동 특성**을 테스트한다.

```text
Current Price 변경
→ intrinsic value 불변

MIP 변경
→ ASP bridge가 바뀌지 않으면 intrinsic value 불변

Actual ASP 변경
→ value 변화

Utilization 변경
→ EBITDA 변화

Net Debt 증가
→ Equity Value 하락

Scenario Probability 변경
→ Expected Value만 변경

Company plan 변경
→ 관련 assumption만 영향
```

---

# 46. 테스트

최소 다음 테스트를 작성한다.

## Unit Tests

- 단위변환
- EV/Equity
- Net Debt
- DCF
- Multiple
- Scenario weighting

## Integrity Tests

- Current price anchoring
- Probability sum
- Source requirement
- No duplicated CAPEX
- No duplicated optional revenue
- Unknown value handling

## Regression

- OCI fixture

## Adversarial

- 회사가 IR 목표를 낮췄을 때 자동 Bull 유지 여부
- 언론만 있고 공시가 없는 호재
- 정책이 기업에 직접 적용되지 않는 경우
- 매출 증가하지만 FCF 악화
- 높은 backlog지만 선수금 감소
- 가격 상승하지만 물량 감소

---

# 47. 출력 형식

`분석시작` 완료 후 최종 출력은 다음 순서를 권장한다.

## 1. 결론 먼저

```text
판단
현재 가격대
Expected Value
핵심 비대칭
```

## 2. 시장이 아는 것

## 3. 시장이 덜 보는 것

## 4. 돈의 흐름

## 5. 병목

## 6. Evidence

## 7. Long Thesis

## 8. Short Thesis

## 9. Valuation

```text
Bear
Base
Bull
Strategic Option
Expected
```

## 10. 현재 시장이 요구하는 숫자

## 11. Kill Conditions

## 12. 다음 검증 이벤트

## 13. Source / Assumption 구분

---

# 48. 매수타점

고정된 가격밴드만 출력하지 않는다.

기업 변동성이 큰 경우:

```text
Event
+
Valuation Discount
```

방식으로 판단한다.

예:

```text
계약 전
→ 높은 안전마진 필요

계약 확인
→ 확률 상승

실제 Margin 확인
→ uncertainty 감소
```

즉 매수타점도 **증거 상태에 따라 변한다.**

---

# 49. 개발자 UX

다음이 가능해야 한다.

```bash
python -m valuation validate
python -m valuation test
python -m valuation export-excel
```

하지만 사용자에게 CLI를 노출할 필요는 없다.

---

# 50. 로그

분석 진행 상태를 보여준다.

예:

```text
[1/9] 기업 식별
[2/9] 산업 판별
[3/9] 공식 자료 수집
[4/9] Evidence 정리
[5/9] 인사이트 스캔
[6/9] 가설 → 가정 연결
[7/9] 밸류 계산
[8/9] 감사
[9/9] 보고서 생성
```

오래 걸릴 때 사용자가 멈춘 것으로 느끼지 않게 한다.

---

# 51. 실패 원칙

다음 상황에서는 억지로 목표가를 만들지 않는다.

```text
핵심 자료 없음
산업 모델 판별 불가
중요 수식 감사 실패
회계 데이터 모순
Source conflict 미해결
```

그 대신:

```text
현재 계산 불가
부족한 데이터
왜 중요한지
```

를 명확히 보고한다.

---

# 52. 비기능 요구사항

### 유지보수

- 한 파일에 모든 코드 금지
- 지나친 클래스 계층 금지
- 중복 계산 금지
- Magic Number 금지

### 성능

밸류 계산은 빠르게.

웹 자료 탐색이 가장 오래 걸리는 부분이어야 한다.

### 재현성

같은 입력으로 재실행 가능.

### 추적성

모든 결과 → assumption → evidence까지 역추적 가능.

---

# 53. 절대 금지사항

다음은 프로젝트 실패로 본다.

1. 현재주가를 보고 목표가를 맞추는 것
2. 출처가 다른 숫자를 같은 신뢰도로 쓰는 것
3. 회사계획을 실현값으로 쓰는 것
4. 뉴스 하나를 바로 목표가로 변환하는 것
5. CAPEX 이중차감
6. 같은 미래매출 이중계상
7. Bear/Base/Bull에서 매출만 바꾸고 나머지를 고정하는 것
8. 증권사 목표가 평균을 적정가로 쓰는 것
9. 산업 특성을 무시한 획일 DCF
10. 근거 없이 확률을 바꾸는 것
11. LLM이 산술을 임의 수행하고 검산하지 않는 것
12. 수식 에러가 있는 Excel 전달
13. 모든 기업에 같은 Multiple 적용
14. 높은 주가 상승을 좋은 분석 결과로 취급하는 것

---

# 54. 우리가 원하는 투자 엔진의 성격

이 시스템은 **Bull Case Generator가 아니다.**

좋은 분석은 때로:

> 이 기업은 훌륭하지만 지금은 비싸다.

일 수 있다.

또는:

> 숫자는 아직 약하지만 행동과 계약의 순서가 미래 개선 가능성을 높이고 있다.

일 수도 있다.

가장 중요한 것은:

> **무엇을 믿어야 현재 가격이 정당화되는가?**

를 항상 설명하는 것이다.

---

# 55. 최종 성공 기준

다음 질문에 모두 YES여야 한다.

### 데이터

- 모든 핵심 숫자의 출처가 추적되는가?
- 실현값과 계획값이 분리되는가?

### 인사이트

- 뉴스보다 행동과 돈을 먼저 보는가?
- 병목을 찾는가?
- 2차 효과를 보는가?

### 가치

- 인사이트가 valuation assumption에 연결되는가?
- 미확정 미래가 확률로 표현되는가?
- 이중계상이 없는가?

### 리스크

- Long과 Short가 모두 있는가?
- Kill Condition이 있는가?

### 시장

- 현재가격을 마지막에만 보는가?
- 시장이 요구하는 미래 숫자를 역산하는가?

### 품질

- Formula Audit이 PASS인가?
- 같은 Evidence로 다시 실행했을 때 결과가 재현되는가?

---

# 56. 첫 번째 실제 구현 목표

첫 MVP는 다음을 완성하라.

```text
1. 범용 repository 구조
2. Master SKILL.md
3. 분석시작 command router
4. Evidence Ledger
5. Industry Router
6. Hypothesis Engine
7. Evidence → Assumption Bridge
8. SOTP / Commodity / Order Equipment 모델
9. Scenario Engine
10. Probability Engine
11. Current Price Isolation
12. Formula Audit
13. Generic Excel Generator
14. OCI Regression Fixture
15. Markdown Investment Report
16. README
17. Automated Tests
```

---

# 57. 구현 순서

### STEP 1
전달된 자료 전체 분석.

### STEP 2
현재 구조와 목표 구조 Gap Analysis 작성.

### STEP 3
최소 아키텍처 확정.

### STEP 4
데이터 Schema 구현.

### STEP 5
OCI Excel 구조를 범용 Schema에 migration.

### STEP 6
Valuation Engine 구현.

### STEP 7
Insight/Hypothesis Layer 구현.

### STEP 8
Audit Engine 구현.

### STEP 9
`분석시작` orchestration 구현.

### STEP 10
OCI regression 테스트.

### STEP 11
두 번째 다른 산업 기업으로 테스트하여 OCI 하드코딩 여부 확인.

### STEP 12
문서화와 정리.

---

# 58. 작업 중 보고 방식

코딩 진행 중에는 다음 형식으로 짧게 상태를 알려라.

```text
현재 단계:
완료:
발견된 문제:
수정:
다음:
```

단순히 "진행 중"이라고 하지 않는다.

---

# 59. 구현 후 반드시 보여줄 것

최종적으로:

```text
폴더 구조
핵심 설계
분석시작 실행 흐름
Excel 생성 구조
Evidence 예시
Hypothesis 예시
Valuation 결과 예시
Audit 결과
테스트 결과
남은 한계
다음 개선 우선순위
```

를 보고한다.

---

# 60. 마지막 원칙

이 프로젝트에서 우리가 만들고 싶은 것은 단순한 밸류에이션 계산기가 아니다.

> **사실을 수집하고,  
> 행동을 읽고,  
> 돈의 흐름을 추적하고,  
> 병목을 찾고,  
> 아직 발표되지 않은 미래를 확률로 평가하고,  
> 그 미래가 실제 현금흐름으로 연결되는지를 검증하여  
> 가격과 가치의 비대칭을 찾는 투자 의사결정 시스템이다.**

따라서:

**Narrative는 Evidence가 되어야 하고,  
Evidence는 Assumption으로 연결되어야 하며,  
Assumption은 Valuation을 움직여야 하고,  
Valuation은 Audit을 통과해야 한다.**

이 연결고리가 끊어지는 순간 계산을 멈춰라.

그리고 항상 기억하라.

> **가격이 가정을 만들지 않는다.  
> 검증된 사실과 미래가치 확률이 먼저 존재하고,  
> 마지막에 가격과 비교한다.**

이 원칙을 깨뜨리는 기능은 구현하지 마라.

이제 전달된 파일을 모두 읽고,
기존 코드를 훼손하지 않도록 백업한 뒤,
**Gap Analysis → Architecture → MVP 구현까지 연속해서 시작하라.**