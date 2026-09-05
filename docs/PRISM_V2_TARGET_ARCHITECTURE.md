# PRISM v2 목표 아키텍처 — 강점은 그대로, 사람이 하던 전달만 기계로

> 상태: 설계 제안 (코드 변경 없음). 전제 조사: `docs/LLM_READING_SURFACE_SURVEY.md`,
> `docs/LLM_READING_HANDOFF_DESIGN.md`. 기존 헌법(`docs/CONTROL_PLANE_ARCHITECTURE.md`)의
> 권위 사슬은 바꾸지 않는다. 바뀌는 것은 **사슬의 각 마디를 누가 채우느냐**다.

## 0. 한 줄 결론

지금 시스템에서 구식인 것은 결정론 엔진이 아니라 **사람이 트랜스포트 역할을 하는 것**이다.
고려아연 런의 기계 재생은 **3.8초**, 사람이 채운 준비 작업은 **하루**였다. 엔진·감사·해시체인은
그대로 두고, 준비 작업의 세 마디(수집 → 판독 → 판단 초안)를 기계와 LLM에게 넘기면
같은 정확도 보증 아래 종목당 소요가 하루에서 시간 단위로 내려온다.

| 지표 | 현재 (4호 런 실측) | v2 목표 |
|---|---|---|
| 종목당 준비 시간 (사람/에이전트 손) | ~1일 | 무인 30분 이내 + 사람 검토 1시간 이내 |
| 기계 재생 시간 | 3.8초 | 동일 (변경 없음) |
| 사람이 직접 쓰는 라인 | run.yaml 61 + underwriting 397 + risk_pack 183 + segments + staff 4파일 | 판단 키 승인 diff만 (KZ 기준 50키 중 ~30키) |
| 리포트 "공시 직접 인용" 비율 | 0.0% (문턱 20%) | ≥ 40% |
| 정적 파서 실전 적중 (판매단가·가동률) | 0/6 | 판독기 + 검증기로 대체, 등록부 무증설 |
| 두 실행자 결과 차이 설명 | 수작업 추적 (50만원 차이 미해명) | 판독/판단/엔진 3분해 자동 표 |
| 발행사 1개당 파서 패치 | 1개 (앵커 3회·단위 2회·주석 파서 3회) | 0 (배제 어휘만 증설 허용) |

## 1. 실측 — 시간과 정확도가 새는 자리

### 1.1 사람이 채우는 파일 (고려아연 런)

| 파일 | 라인 | 지금 누가 | 무엇을 | v2 |
|---|---|---|---|---|
| `run.yaml` | 61 | 사람 | 다른 런 복사 후 as_of·결산·부문·코호트 상수 손편집 | **Resolver가 생성** |
| `raw/*` | 20+ 파일 | 에이전트 | 뷰어 목차에서 eleId 손으로 골라 섹션 수집 | **Collector가 목차 역할로 수집** |
| `declarations/segments.yaml` | ~30 | 사람 | IFRS 8 주석 읽고 부문·KSIC·rationale 작성 | **Reader 제안 → 리뷰 승인** |
| `declarations/underwriting.yaml` | 397 (50키) | 사람 | 11키는 공시 전사, 39키는 판단. 전부 손으로 | **전사·가공은 기계, 판단은 LLM 초안 + 승인** |
| `declarations/risk_pack.yaml` | 183 | 스크립트+사람 | 피어 선정·시세 수집·베타 계산 후 편집 | **Resolver 피어 후보 + Collector 시세 + 기존 스크립트** |
| `declarations/market.yaml`, `street.json` | 소 | 사람 | 종가 전사, 무커버리지 선언 | **Collector** |
| `declarations/staff/*.json` | 4파일 12제안 | 사람이 LLM 역할 대행 | 정보장교 5·레드팀 3·브리지 2·로케이터 2 | **실 LLM 좌석 (트랜스포트 이미 존재, #23)** |
| calibration 블록 | 25 | 스크립트+사람 | 코호트 구축 후 BindingConstants 붙여넣기 | **코호트 등록부가 해석 (main #162/#167)** |

핵심: 397라인 중 판단(rationale)이 필요한 건 39키다. 나머지와 그 39키의 *근거 인용*까지
사람이 손으로 옮기고 있다. 조사(§2.1 서베이)에서 4개 런 151행 중 27.2%가 전사·가공이었다.

### 1.2 정확도가 새는 자리

- **전사 오류 위험**: 사람이 공시 숫자를 YAML로 옮기며 단위·기간을 바꿔 쓸 수 있다. 검증기는 선언 값이 아니라 인용을 보므로 잡지 못한다.
- **인용 0%**: 브리지 드래프트가 `UW:` 선언만 인용해 리포트가 "공시 직접 인용 0.0%"를 찍는다. 감사는 통과하지만 근거 사슬이 선언에서 끊긴다.
- **판단의 범위 무통제**: 판단 키에 코호트 분포 대비 범위 게이트가 런 시점에 안 걸린다 (main #164 범위 규칙 등록부는 있으나 브랜치 런에는 미적용).
- **두 실행자 불일치**: 같은 종목을 두 에이전트가 돌리면 50만원 차이가 나고, 차이가 판독인지 판단인지 엔진인지 손으로 추적해야 한다.

### 1.3 조직이 새는 자리

- 2주간 453 커밋, `run/koreazinc-*` 브랜치 12개, 닫힌 PR 4개, 같은 결함(부문 스코프 evaluator)을 두 에이전트가 각자 고침.
- 원인: 런 브랜치가 엔진 수정을 같이 실었고, 정지 메시지가 작업지시서인데 작업 소유자가 없었다.

## 2. 지키는 강점 — 변경 금지 목록

1. **컴파일러가 국경이다.** 숫자는 컴파일된 가정에서만 나온다. LLM은 좌표·인용·분류·초안만 낸다.
2. **결정론 재생.** 박제된 제안·선언으로 모델 없이 byte-identical 재생. 4개 런의 숫자가 회귀 테스트다.
3. **Fail-closed.** 게이트는 완화하지 않는다. 정지 메시지가 작업지시서다.
4. **해시 체인.** Evidence → Assumption → Scenario → Valuation → Freeze 체인과 `run_input_sha256`.
5. **코호트 규율.** 타깃 배제, 결산 주기 일치, 등록부 결합, as_of 이후 공표물 금지.
6. **자격증명 격리.** 엔진은 벤더 SDK·키를 들지 않는다. 트랜스포트는 밖에 산다.
7. **위협 모델.** 제안 안의 지시문·URL은 데이터다 (`LLM_CONTAINMENT_THREAT_MODEL.md`).

## 3. 목표 아키텍처 — 다섯 층

```
[0 Entry]    prism analyze 010130 --as-of 2026-08-29
   │  Resolver: 종목 → corp_code·KSIC·결산주기·최신 정기보고서·부문 주석 유무·코호트·피어 후보
   ▼          → run.yaml 자동 생성 (lock 해시)
[1 Collect]  Collector (기계, 병렬, 멱등): list / fnltt / 섹션(목차 역할) / 시세 / 피어 → raw/
   ▼
[2 Read]     Reader (LLM 1회, 제안만) → Verifier (기계) → Evidence 영수증 → declarations/staff/reader.json
   ▼
[3 Judge]    Draft Generator: 전사·가공 키 = 기계 계산 / 판단 키 = LLM 초안(판단 계약) → 사람 승인 diff
   ▼          → declarations/underwriting.yaml (origin 표식)
[4 Compute]  기존 33단계 그대로: 컴파일 → 엔진 → 감사 → 동결 → 시장 → 리포트 (3.8초)
   ▼
[5 Variance] 같은 종목의 두 런을 판독/판단/엔진으로 3분해 → variance.md
```

각 층은 **파일로만** 다음 층과 만난다. 어떤 층도 다음 층의 파일을 건너뛰어 쓰지 못한다.
그래서 사람이 오늘 하던 일은 v2에서도 *가능*하다 — 다만 기본값이 아니다.

### 3.1 Layer 0 — Entry + Resolver

입력: 종목코드 또는 회사명, as_of (기본 = 오늘), 선택 옵션 없음.
출력: `runs/<종목>-<코드>/run.yaml` + `resolver.json` (모든 결정의 근거).

Resolver가 결정하는 것과 근거:

| 결정 | 근거 소스 | 실패 시 |
|---|---|---|
| corp_code, KSIC | `opendart-find_company`, `get_company_info` | 정지: 회사 미식별 |
| 결산 주기, business_year | 최신 정기보고서 rcept와 fnltt `rcept_no` 대조 (신한알파 교훈) | 정지: 불일치 이름 부름 |
| 채택 보고서 (정정 포함) | 공시목록의 정정 관계, as_of 이전 | 정정이 더 최신이면 정정 채택 + 사유 기록 (KZ 20260813001726 교훈) |
| 부문 수 | IFRS 8 주석 존재 → Reader `segment_note` 과제 예약 | 주석 없음 → 단일 부문 `core` |
| 방법 | `kr_industry_classification_map.yaml` (KSIC → 원형 → 실행 패밀리) | 미매핑 → 정지 (46/38 소유자 결정 유지) |
| 코호트 | `kr_calibration_cohort_registry.yaml` (`resolve_production_calibration_cohort`) | 미등록 → 기대값 없이 3-시나리오까지, 리포트에 명시 |
| 피어 후보 | 같은 KSIC 3자리, 타깃 제외, 상장 | 후보 <4 → 리스크팩 사람 지정 |

`run.yaml`은 사람이 편집할 수 있으나, `generated_by: resolver@<sha>`와 `resolver_lock`
해시가 붙고 편집하면 lock이 깨져 `run_input_sha256`에 잡힌다. 오늘 61라인 손편집이 0이 된다.

### 3.2 Layer 1 — Collector

지금은 뷰어 목차에서 eleId를 사람이 고른다. v2는 **목차 역할(ToC role)**로 고른다.

```yaml
# config/kr_filing_toc_roles.yaml — 제목 어휘가 아니라 역할
sections:
  business_overview:      {toc_match: ["II. 사업의 내용"], required: true}
  segment_note:           {toc_match: ["영업부문", "부문정보", "부문별 정보"], required_if: multi_segment}
  borrowings_note:        {toc_match: ["차입금", "사채"], required: false}
  equity_investments:     {toc_match: ["타법인출자"], required: false}
  share_count:            {toc_match: ["주식의 총수"], required: true}
  audit_opinion:          {toc_match: ["감사인의 감사의견"], required: true}
```

- 멱등: `raw/<rcept>/<eleId>.xml`이 있으면 재수집 안 함 (세션 재시작 두 번의 교훈).
- 병렬: 섹션·피어·시세는 서로 독립 → 동시 수집. 큰 응답은 서브프로세스 격리(현행 유지).
- 절단 기록: 12,000자 절단 시 `raw/manifest.json`에 `truncated: true` → Reader gap 사유 `TRUNCATED`.
- 수집 산출은 전부 커밋 가능한 공개 데이터만 (현행 유지). manifest에 각 파일 sha256.

### 3.3 Layer 2 — Reader + Verifier

`docs/LLM_READING_HANDOFF_DESIGN.md` §3을 그대로 구현한다. 여기서는 **운영 계약**만 덧붙인다.

- **과제 단위 병렬**: (멤버, 판독 과제) 쌍마다 독립 호출. KZ 기준 섹션 8개 × 과제 평균 3개 = 24호출, 동시 실행 시 1분 내.
- **1회 원칙**: 제안은 `declarations/staff/reader.json`에 박제. 재실행은 모델 없이 검증기만 돈다.
- **캐시 키**: `(멤버 sha256, 과제 등록부 sha256, reader_id)`. 같은 공시를 다른 종목 런에서 다시 읽지 않는다.
- **이중 판독 (선택)**: 정확도 모드에서 같은 과제를 온도 0으로 2회 → 좌표 불일치면 `AMBIGUOUS` gap. 값 평균 같은 건 없다.
- **검증기 전부 거부형** (인용 유일성·표 정체·좌표·단위 차원·연대·항등식·교차소스·절단). 통과 못 하면 값이 아니라 gap.

산출: `EvidenceRecord` + 영수증(`proposal_sha256`, `grid_receipt`, `verifiers_passed`).
기존 4개 좌석 중 **filing_locator_analyst는 Reader로 흡수**된다.

### 3.4 Layer 3 — Draft Generator + 판단 계약

`required_assumption_keys()`가 내는 키를 세 갈래로 나눈다. 갈래는 키 등록부가 정한다.

```yaml
# config/assumption_origin_registry.yaml
transcribed:   # 증거 값 그대로. 기계가 쓴다. LLM·사람 개입 없음.
  diluted_shares:      {from: share_count_table, transform: issued_minus_treasury}
  <seg>_ownership:     {from: segment_note|equity_investments, transform: identity_observation}
  capacity:            {from: reading_task:capacity}
  production:          {from: reading_task:production}
  realized_price:      {from: reading_task:realized_price}
transformed:   # 등록 transform으로 기계가 계산. 입력 증거 id 인용.
  <seg>_ev_adjustment: {from: [fnltt:borrowings, fnltt:cash, fnltt:financial_assets], transform: net_debt_bridge}
  input_price:         {from: reading_task:input_price, transform: fx_convert|annualize_half_year}
judged:        # LLM 초안 + 사람 승인. 판단 계약 적용.
  <seg>_fcff_year_1..5, <seg>_terminal_growth, <seg>_terminal_roic,
  down_*/bull_* 변형, cash_cost, benchmark_price, inventory, product_yield, plant_runs, turnaround
```

KZ 50키 기준: transcribed 8, transformed 3, judged 39. **사람이 보는 건 judged 39키의 diff뿐이다.**

**판단 계약 (Judgment Contract)** — judged 키 하나당 LLM이 채워야 하는 필드:

```yaml
smelting_fcff_year_1:
  value: 900
  unit: KRW_billion
  origin: judged
  evidence_ids: [DARTGRID:20260814003958:segment_note:smelting:operating_income, DARTKPI:...:capex]
  derivation: "H1 OI 1,241.5bn ×2 → NOPAT(26.4%) − (capex 943bn − D&A) − ΔNWC ≈ 900bn"
  range_check: {rule: assumption_range_rule_registry, band: cohort_p10_p90, status: inside}
  rationale: "…20자 이상, 현행 규칙…"
```

- `evidence_ids`는 **실존 증거만** (Reader 산출·fnltt). 없으면 컴파일러가 거부.
- `derivation`은 사람이 읽는 한 줄 식. 기계가 검증하지 않지만 diff에 그대로 보인다.
- `range_check`는 main의 `assumption_range_rule_registry.yaml`을 런 시점에 건다. 밖이면 값이 아니라 `AWAITING_USER_DECISION` — 게이트 완화 없음.
- 사람 승인 인터페이스는 "39줄 diff + 각 줄의 evidence 링크"다. 승인하면 `approved_by`, `approved_at`가 붙고 `run_input_sha256`에 들어간다.

**무인 모드**: 판단 키를 승인 없이 쓰려면 run.yaml에 `judgment_policy: auto_within_band`를 선언해야
하고, 리포트 첫 줄에 "판단 가정 39개 중 39개 자동, 검토자 없음"이 박힌다. 숨길 수 없다.

### 3.5 Layer 4 — Compute: 33단계는 남기고 등급을 붙인다

단계를 지우면 doctrine coverage(무언 생략 금지)가 깨진다. 지우지 않는다. 대신 **세 등급**을 붙여
사람이 읽어야 할 것만 보이게 한다.

| 등급 | 의미 | 단계 (예) |
|---|---|---|
| GATE | 멈출 수 있다. 정지 메시지 = 작업지시서 (층·파일·키 이름 포함) | SEGMENT_DECOMPOSITION, EVIDENCE_TO_ASSUMPTION_BRIDGE, WACC_VALIDATION, AUDIT_GATE, INTRINSIC_VALUE_FREEZE, PROBABILITY_DISTRIBUTION_ANALYSIS |
| CHECK | 경고만. 리포트 부록으로 | DCF_PER_CONSISTENCY, CROSS_METHOD_DOUBLE_COUNT, THESIS_DELTA, STREET_GAP |
| TRACE | 기록만. 사람에게 안 보임 (trace.json에만) | LOAD_COMPANY_STATE, SOURCE_FRESHNESS_PRECHECK, SAVE_STATE 등 |

정지 메시지 형식을 통일한다: `layer=3 file=declarations/underwriting.yaml key=other_terminal_roic reason=RANGE_OUTSIDE band=[…]`.
오늘의 "정지 메시지가 작업지시서"가 사람이 아니라 Layer 2/3 재실행의 **입력**이 된다:
Layer 4가 `EVIDENCE_GAP`을 내면 Reader가 그 과제만 다시 읽고, `RANGE_OUTSIDE`면 Draft Generator가
그 키만 다시 초안한다. 최대 3회, 그 뒤엔 사람.

이 층의 코드는 **바뀌지 않는다.** 등급표는 `config/stage_tiers.yaml` 하나다.

### 3.6 Layer 5 — Variance Ledger

같은 종목의 두 런(다른 실행자, 다른 날, 다른 모델)을 자동 3분해:

```
variance: 010130 @ 2026-08-29 — A(claude) vs B(gpt)
  per-share expected:   A=1,298,564   B=<B의 리포트 값>   Δ=<차이>
  ── 판독 차이 (evidence id 불일치) ──────────── 기여 Δ <재생으로 산출>
     smelting operating_income H1: A=DARTGRID:…:1,241.5bn  B=UW-only (인용 없음)
  ── 판단 차이 (같은 증거, 다른 값) ─────────── 기여 Δ <재생으로 산출>
     smelting_fcff_year_3: A=900  B=<B 값>  (둘 다 band 안이면 판단 차이로 분류)
  ── 엔진 차이 (같은 선언, 다른 값) ─────────── 기여 Δ 0 이어야 함 (해시 동일)
  (예시 형식. B 값은 GPT 런 디렉토리가 커밋되어야 채워진다.)
```

기여도는 A의 선언에 B의 값을 키 하나씩 바꿔 넣어 재생(3.8초 × 키 수)으로 구한다 — 새 수학 없음.
이것이 GPT가 제안한 Variance Gate와 Delta Ledger의 구체 형태이고, 게이트가 아니라 **표**다.
멈추지 않고 보여준다. 멈추는 건 Layer 4 게이트만이다.

## 4. 런 디렉토리 v2

```
runs/koreazinc-010130/
  run.yaml                     # Resolver 생성, resolver_lock
  resolver.json                # 모든 결정 + 근거 + 후보
  raw/  manifest.json          # 파일별 sha256, truncated 표식
  declarations/
    segments.yaml              # Reader classification → 승인 (approved_by)
    underwriting.yaml          # origin: transcribed/transformed/judged, evidence_ids, approved_by
    risk_pack.yaml             # 현행 스크립트 산출
    market.yaml  street.json   # Collector
    staff/
      reader.json              # 판독 제안 (박제)
      reviewer.json            # 레드팀·브리지 제안 (박제) — 현 3좌석 통합
  out/                         # 현행 (bundles, state, final_report.md)
  variance/<other_run_id>.md   # Layer 5
```

좌석은 4 → 2로 준다. intelligence_officer(가설)·bridge_analyst(브리지)·red_team(반증)은
각자 다른 프롬프트로 같은 증거를 보고 있었다. v2에서 가설·브리지는 Draft Generator의 판단 계약이
흡수하고, 반증은 `reviewer` 한 좌석이 **승인 전 diff에 대한 반론**으로 낸다. 반론은 `AWAITING_USER_DECISION` 항목이 된다.

## 5. 속도 — 어디서 얼마가 빠지나

| 구간 | 현재 | v2 | 방법 |
|---|---|---|---|
| run.yaml 준비 | 30분+ (복사·편집·정정보고서 판단) | 10초 | Resolver |
| raw/ 수집 | 2~4시간 (목차 손탐색, 세션 끊김 2회) | 3~5분 | 목차 역할, 병렬, 멱등 재개 |
| 부문·KPI 판독 | 1~2시간 (앵커 실패 → 앵커 증설 → 재시도) | 1~2분 | Reader 병렬 24호출, 검증기 |
| underwriting 397라인 | 3~5시간 | 기계 11키 즉시 + LLM 39키 2분 + 사람 검토 30~60분 | Draft Generator |
| 리스크팩 | 1시간 | 10분 | 피어 후보 자동, 시세 Collector, 기존 스크립트 |
| 코호트 | 반나절 (신규 구축 시) | 0 (등록됨) / 반나절 (신규, 변경 없음) | 등록부 |
| 실행·재생 | 3.8초 | 3.8초 | 변경 없음 |
| 정지 → 재시도 루프 | 회당 10~30분 (사람) | 회당 1~2분 (Layer 2/3 자동 재입력, 3회 한도) | 작업지시서 기계 소비 |

무인 경로 합계 30분 이내가 목표이고, 사람 시간은 **판단 diff 검토 1시간**으로 모인다.

## 6. 정확도 — 어디서 올라가나

1. **전사 오류 0**: transcribed/transformed 키는 사람 손을 거치지 않는다. 검증기가 인용·단위·기간·항등식을 본 값만 원장에 들어간다.
2. **인용 사슬 복원**: 브리지가 `DARTKPI:`/`DARTGRID:`를 인용하므로 "공시 직접 인용" 0% → 실측값. 20% 문턱이 형식이 아니라 실제 게이트가 된다.
3. **판단 범위 게이트**: judged 키가 코호트 분포 밖이면 사람 결정으로 올라간다. 지금은 그런 값이 조용히 통과한다.
4. **판독 이중화**: 정확도 모드에서 좌표 불일치는 gap. 틀린 값이 원장에 들어가는 대신 "여기 다시 읽어라"가 나온다.
5. **차이 분해**: 두 실행자의 50만원 차이가 어느 키의 어느 판단인지 표로 나온다. 오늘은 그게 없어서 "다르다"에서 끝난다.
6. **회귀 4종 불변**: 4개 런의 숫자는 마이그레이션 매 단계의 통과 조건이다. 정확도를 올린다면서 재생을 깨면 그 단계는 실패다.

## 7. 두 에이전트 규율 — 같은 결함을 두 번 고치지 않기

- **런 브랜치는 엔진을 싣지 않는다.** `run/*`에는 `runs/`와 `config/` 데이터만. `src/` 변경이 있으면 CI가 거부.
- **엔진 변경 PR의 통과 조건** = 4개 런 재생 + 전체 스위트. 이미 `tests/test_kr_live_runbook.py`가 한다. 규칙만 명문화.
- **정지 메시지는 이슈가 된다.** Layer 4 GATE 정지 중 3회 자동 재입력 후에도 남는 것은 `layer/file/key/reason` 제목의 이슈로 열린다. 소유자 1명.
- **층당 소유자 1명.** Reader 등록부, Draft 등록부, 코호트 등록부, 분류맵은 각각 리뷰어가 정해져 있고 PR로만 바뀐다 (46/38 결정이 이미 이 경로다).

## 8. 마이그레이션 — 작게, 재생 불변으로

| 단계 | 내용 | 통과 조건 | 크기 |
|---|---|---|---|
| M0 | Resolver + run.yaml 생성기 + Collector 목차 역할·멱등·manifest | 4개 런의 raw/·run.yaml을 재생성해 sha 동일 (정정보고서 채택 포함) | 1주 |
| M1 | Reader `table_cell`·`locator` + 검증기 + `reader.json` (핸드오프 설계 1단계) | 4개 런의 locator JSON을 reader.json으로 재작성 → 숫자 불변; 정적 앵커 사전 동결 | 2주 |
| M2 | Draft Generator: origin 등록부, transcribed/transformed 기계 계산, judged 판단 계약, 범위 게이트 | KZ underwriting 재생성 → 기계 키 byte-equal, judged 키는 diff 리포트; 4개 런 불변 | 2주 |
| M3 | `stage_tiers.yaml` + 작업지시서 형식 + Layer 2/3 자동 재입력 (3회) | 정지 사례 5종(§6 런북 표)이 자동 재입력으로 해소되거나 이슈로 열림 | 1주 |
| M4 | Variance Ledger | KZ의 두 런(claude·gpt)을 3분해한 variance.md 산출 — GPT 런 커밋이 선행 조건 | 1주 |
| M5 | 좌석 4→2, `reviewer` 반론 → 승인 diff, 무인 모드 표식 | 4개 런 재생 불변 + 리포트 첫 줄 표식 | 1주 |
| M6 | Entry: `prism analyze` MCP 도구, 승인 diff UI | "ㅇㅇ 분석해줘" → 무인 30분 → diff → 리포트 | 2주 |

각 단계는 독립 PR이고, 이전 단계 없이도 오늘의 손작업 경로와 공존한다 (파일 계약만 지키면 된다).
M0·M1은 서베이·핸드오프 설계에서 이미 위치가 정해져 있어 즉시 착수 가능하다.

## 9. 하지 않는 것

- LLM이 숫자를 원장에 쓰지 않는다. 좌표·인용·분류·초안까지다.
- 게이트를 완화하지 않는다. 무인 모드도 게이트를 못 넘는다 — 넘는 게 아니라 표식이 붙는다.
- 33단계를 지우지 않는다. 등급만 붙인다.
- 코호트를 자동 생성하지 않는다. 등록부에 없으면 기대값 없이 끝나고 그렇게 쓴다.
- 분류(KSIC·원형)는 자동 효력이 없다. 리뷰 데이터로만 효력이 생긴다.
- 유료 자료·자격증명·비공개 런은 레포에 들어오지 않는다 (현행).

## 10. 요약 — 무엇이 어디로 가나

| 일 | 지금 | v2 |
|---|---|---|
| 읽기 (공시 표·문장) | 정규식 + 사람 | LLM 제안 + 기계 검증 |
| 옮겨 적기 (전사·가공) | 사람 | 기계 (증거 id 인용) |
| 판단 (미드사이클·터미널) | 사람 | LLM 초안 + 사람 승인 diff |
| 검증·계산·감사·동결 | 기계 | 기계 (변경 없음) |
| 차이 설명 | 사람 | 기계 표 |
| 멈춤 처리 | 사람 | 기계 3회 → 사람 |

강점(결정론·감사·fail-closed)은 단 한 줄도 양보하지 않는다. 사람은 판단만 한다. 나머지는 기계다.
