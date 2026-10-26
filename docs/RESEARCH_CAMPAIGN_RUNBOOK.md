# 민감도 우선 조사 운영

이 경로는 호스트 ChatGPT/Claude가 제공하는 검색·추론을 기존 밸류에이션 입력에 연결한다. LLM SDK나 별도 API 키를 요구하지 않는다. 검색 도구 자체를 Python 엔진이 제공하는 것은 아니다. 작업지시를 받은 호스트가 원문을 읽고 답변해야 한다.

## 실행

```bash
PYTHONPATH=src python scripts/run_research_campaign.py campaign.yaml \
  --workspace /tmp/company-research \
  --underwriting /path/to/run/declarations/underwriting.yaml
```

답변이 없으면 종료코드 2와 `requests/`의 작업지시를 받는다. 각 작업지시에는 필요한 변수·단위·회사·기준일·의존 결과·질문·요청 해시가 있다. 호스트는 독립 작업을 병렬 조사하여 `responses/<request_id>.json`에 응답한다. 같은 명령으로 재개한다. 응답 해시나 상위 결과가 바뀌면 해당 작업과 자손만 재검증하며, 바뀌지 않은 독립 결과는 재사용한다. 대기 결과는 완료 캐시에 저장하지 않는다.

선택적 `--provider module:callable`은 같은 작업지시를 받아 응답 객체를 반환한다. 이 callable이 호스트의 검색 기능과 연결된다. 파일 방식도 같은 인터페이스이며 권한을 더 받지 않는다. `--max-workers`는 병렬 작업 수, `--max-rounds`는 조사 재시도 한도다. 응답 형식 수정과 원문 재조사는 별개다. 직접 확인 → 독립 자료·증권사 단서 → 비교기업 조정 → 범위 추정으로 조사 방식을 바꾼다. 기본 5회 조사 예산 소진 시 작업지시와 이력을 보존하고 재개한다.

## 계획 예시

```yaml
schema_version: research-campaign/v1
target_id: "US:EXAMPLE"
as_of: "2026-09-12"
model_version: "contracted-business-1"
policy_version: "research-1"
requests:
  - request_id: capacity
    metric: executable_capacity
    segment: core
    unit: W
    economic_path_id: cloud-capacity
    mandatory: true
    depends_on: []
    question: "공급 가능한 전력과 장비를 함께 고려한 실제 가동 가능 용량은 얼마인가?"
```

처음 범위를 모르면 `bounds`를 생략한다. 이때 영향은 미측정이며 0이 아니다. 확인된 범위가 있으면 `bounds: {low, base, high, rationale, source_refs}`를 붙인다. 신사업의 실행 제약을 필수 요청으로 지정한다. 값이 확보되면 같은 모형으로 민감도를 재측정하고 다음 조사 배치에 사용한다. 할인율·전체 FCFF만 보는 기존 사후 민감도를 대체하지 않는다.

## 응답 예시

아래 값과 URL는 형식 설명용이며 실제 근거가 아니다. `content_sha256`는 실제 읽은 문서의 해시, 날짜는 실제 발행·최초 확인일로 작성해야 한다. 과거 분석 기준일에 맞추기 위해 확인일을 소급하지 않는다.

```json
{
  "schema_version": "research-response/v1",
  "request_id": "capacity",
  "request_hash": "작업지시에 있는 해시",
  "status": "answered",
  "kind": "inferred",
  "value": "100000000",
  "unit": "W",
  "bounds": {"low": "80000000", "high": "120000000"},
  "rationale": "전력 공급계약과 장비 인도 일정의 공통 가동 가능분을 이용한 추정입니다.",
  "uncertainty": "설비 인수검사 지연과 초기 가동률을 아직 확인하지 못했습니다.",
  "invalidation_condition": "전력 인입 또는 장비 인도가 계획보다 지연되면 재계산합니다.",
  "counterevidence": [],
  "counterevidence_search": "취소 공시와 인허가 지연 자료를 조사했으나 확인하지 못했습니다.",
  "sources": [{
    "source_id": "contract-1",
    "source_family": "company-contract",
    "url": "https://example.com/contract",
    "published_at": "2026-09-01",
    "first_seen_at": "2026-09-12",
    "locator": "계약 공시의 용량 및 공급 일정 표",
    "content_sha256": "실제 원문 SHA256 64자리",
    "excerpt": "수치 판단에 사용한 짧은 원문 발췌",
    "kind": "primary"
  }]
}
```

산출값은 `kind=calculated`와 `calculation`에 입력·단위·출처 ID·연산을 넣는다. 현재 재현 연산은 sum, difference, scale(금액 등 × ratio)이다. 그보다 복잡한 판단은 `inferred`로 분리하거나 신사업 현금흐름 모형으로 계산한다. 임의 Python/eval은 허용하지 않는다. 답을 만들 수 없으면 `status=unresolved`와 이유를 반환한다.

## 증권사 근거: blind baseline과 informed revision

2026-10-26 사용자 지시에 따른 명시적 정책 구분이다. 과거의 “대상기업 전망은 어떤 새 실행에서도 제외” 또는 “독립 원자료로 완전히 재구성해야만 채택”이라는 해석을 informed revision에 적용하지 않는다. **원래 blind baseline의 격리·동결은 유지하고, 후행 개별 판단을 거친 새 informed revision에서 전망을 가정으로 채택·조정할 수 있다.** NR은 추천 상태이며 자료의 품질이나 목표가 유무를 대신하지 않는다.

- 기본 `research_mode: BLIND_BASELINE`: 현재가·목표가·등급·원목표배수와 대상기업 broker 전망을 계산 입력으로 반입하지 않는다. `broker_lead`/`broker_estimate`는 `claim_type` 및 `subject_scope: target|industry|peer`로 구분한다. 대상기업을 industry/peer로 이름만 바꾸지 않는다. industry/peer의 방법·메커니즘·추정은 기존 보조 경로로 유지한다.
- 대상기업의 과거 사실·회사 계획을 브로커에서 발견했다면 원문 source ID에 연결한다. `subject_id`, `metric`, `period`, `perimeter`, `accounting_basis`, `unit`을 정확히 일치시키며 사실·계획의 `claim_value`와 `evidence_relation: supports`를 확인한다. 같은 metric의 다른 연도나 반대 증거는 확인 증거가 아니다. 사실은 primary, 회사 계획은 primary/company_plan 원문이 필요하다. 이것은 현재 사실·발표된 계획의 확인이지 미래 결과의 실현을 요구하는 규칙이 아니다. 이를 바탕으로 만든 조건부 범위는 `inferred`로 남는다.
- 옛 `broker_estimate`에 분류가 없으면 버리거나 승인하지 않고 `WORK_REQUIRED`로 보존한다. 호환 필드 `authority: analyst_underwriting`는 **보완 후 가능한 계층**일 뿐 승인 표시가 아니다. 값·승인 응답은 없고 Compiler/merge/완료 캐시로 들어가지 않는다. 기존 무분류 `broker_lead`는 실제 비브로커 근거와 함께 발견 단서로만 유지할 수 있다.
- 증권사 단서가 있으면 primary/independent 검색의 실제 시도와 결과를 `search_attempts`에 남긴다. 검색 실패는 부재 증거나 확실성 증명이 아니다. baseline 추정은 `independent_reasoning`, informed 추정은 `adoption_reasoning`(기존 필드도 호환)을 요구한다. 두 경우 모두 `inferred`이며 사실로 승격하지 않는다.

### 새 informed revision의 실행 결합

원평가 freeze → TP/NR/aggregate 자료 비교 → 호스트 LLM의 개별 근거·반례·의존성 판단 → 필요한 추가 조사 → 새 Evidence→Bridge→Compiler→계산·감사·freeze 순서다. LLM은 채택·조정·기각·보류를 제안한다. 결정론 검증은 출처·시점·기간·단위·회계범위·요청 해시와 계산 권한을 검사하며, “broker forecast이므로 경제적으로 틀림” 같은 일괄 판정을 하지 않는다. **타당한 개별 판단 후 같은 전망 숫자를 채택하는 것도 가능**하다. 독립 primary 전망이나 미래 실현의 확인을 보편적 필수조건으로 요구하지 않는다. 독립 검증이 필요한지와 증거의 충분성은 중요도·자료 내용에 따라 판단하고, 미확인·공동 원자료 의존성은 그대로 공개한다.

계획에 다음 문맥을 명시한다. receipt는 `broker_judgment.validate_broker_judgment_response`에서 만든 전체 typed receipt이며, 아래 자리표시자를 그대로 실행 입력으로 쓰지 않는다.

```yaml
research_mode: INFORMED_REVISION
informed_revision:
  schema_version: broker-informed-revision/v1
  baseline_run_id: 원래_동결_실행_ID
  new_run_id: 별도의_새_실행_ID
  review_receipt: # 실제 broker-review-receipt/v1 전체 객체
    # 원 freeze, 비교, 요청·응답·자료 내용 및 개별 제안 해시에 결합
```

작업지시는 이 문맥을 자신의 해시에 포함한다. `PROPOSE_NEW_RUN_ASSUMPTION`의 candidate마다 기존 요청의 `metric`, `unit`과 `inference_basis: {period_start, period_end, frequency, scope, perimeter, accounting_basis, currency}`를 정확히 연결한다. 응답에는 다음을 추가한다.

```yaml
kind: inferred
adoption_reasoning: 채택하거나 조정한 이유와 원전망 의존성·대안·불확실성의 검토 요약
informed_assumption:
  claim_id: 검토한_claim_ID
  candidate_id: 개별_가정후보_ID
  review_receipt_hash: 실제_receipt_해시
  inference_basis: # 요청 및 candidate와 같은 전체 기간·회계범위 객체
  derivation: 이 입력값 또는 범위를 선택한 검토 가능한 판단·산식 요약
```

점 후보는 그 후보값과 같아야 한다. 범위 후보는 low/high가 같아야 하며 범위 내 기준값은 호스트가 근거를 들어 선택한다. 엔진이 임의 중간값을 만들지 않는다. 후보를 바꿀 이유가 생기면 새 검토 receipt로 요청을 갱신한다. 요청·후보의 기간/범위와 원보고서의 기간/범위가 다르면 LLM의 명시적 정상화·판단을 후보와 receipt에 남긴다. 숫자를 서로 빼거나 비교할 수 있다고 자동 가정하지 않는다.

증권사 source에는 `review_claim_id`를 넣고 검토한 원문 URL·content_sha256·원발행일을 정확히 맞춘다. 원발행일이 알려지지 않은 집계는 informed 경로에서 `published_at: null`, `publication_status: UNKNOWN`과 실제 `first_seen_at`을 보존한다. 관측일을 원보고서 날짜로 채우지 않는다. 자료가 미래 기준일에 발견되었으면 이전 freeze를 소급 개선하지 않는다. 합리적 불확실성 판단에 따라 참고·추가조사·가정 제안은 가능하지만 원문 불명을 허위 확인으로 바꾸지 않는다.

원목표가·시장가격은 검토 계기이지 최종가치 답안이나 목표가 맞추기 입력이 아니다. 방법·배수의 경제적 논거는 개별 검토 후 지원 모델의 실제 가정 driver로 제안할 수 있으며, 원목표배수 필드의 이름만 바꾸어 심사 없이 반입하지 않는다. `PASS`, 최종가치, freeze token, 확정 확률 덮어쓰기는 연구 응답으로 허용하지 않는다. 새 run ID만으로 이미 노출된 분석이 blind가 되는 것도 아니다. 원자료의 내용·권리·독립성 검토는 호스트와 오케스트레이터 책임이며, receipt/hash 검증은 경제적 진실성 인증이 아니다.

여러 보고서가 같은 원자료를 재인용하면 `source_family`도 같아야 한다. 브로커의 공시 인용과 해당 원공시가 같은 family인 것은 허용되지만 두 개의 독립 관측으로 세지 않는다. 새 revision·receipt가 바뀌면 캐시를 무효화하며, 오래된 ACCEPTED 기록도 현재 정책으로 재검증한다. 실제 반입과 가치변화는 후속 전체 canonical 실행으로 입증해야 하고, 연구 승인·작업지시만으로 재평가가 끝났다고 보고하지 않는다.

## 신사업 모형과 수치 반입

선택적 `business_model`은 `kind: contracted_business`, `segment`, `path`, `driver_bindings`, `discount_rate`, `discount_rate_source_refs`, `discount_rate_rationale`, `metric_prefix`로 구성한다. `path`의 정확한 JSON 구조는 `business_path_from_dict` 및 `tests/test_research_business_integration.py`의 실행 예시를 참조한다.

- 각 숫자에 단위·경제경로·원문 출처·가정 참조를 요구한다.
- `driver_bindings`는 요청 ID를 `periods.0.capacity.value` 같은 입력에 연결한다. 단위와 경로가 같아야 하며 두 요청이 같은 필드를 덮어쓸 수 없다.
- 확정 계약잔고·연도별 수요·가동 가능한 생산능력 중 제약이 매출을 제한한다. 사용한 계약잔고는 차감한다.
- 연간 가동 지연은 생산능력 일정을 이동시킨다. CAPEX·고정비·감가상각·운전자본은 달력연도 입력 그대로이며, 같이 지연되는 비용은 별도로 수정한다.
- 비용·세금·CAPEX·운전자본을 차감해 `fcff_year_N` 추정값과 재계산 가능한 입력 receipt를 생성한다. 성장 CAPEX를 다시 순차입금에서 이중 차감하지 않는다.
- 민감도는 기준연도에서 각 예측연도까지 할인한 계약기간 현금흐름의 PV이다. 유한 계약에 자동 영구가치를 붙이지 않는다. 최종 기업가치와 주당가치는 기존 모형의 잔여가치·자본구조·희석 검증이 별도로 필요하다.
- Compiler 반입용 경로는 기준일 다음 연도부터 빠짐없이 제시해야 한다. 사업 개시 전의 투자비·고정비를 자동 0으로 채우거나 먼 미래 현금흐름을 1년차로 당기지 않는다.

결과는 모두 기존 `ANALYST_UNDERWRITING` 경로로 반입된다. 연구자가 observed라고 표시해도 자동으로 감사된 공시값으로 승격하지 않는다. 구조화된 연구 receipt와 신사업 계산 receipt가 Evidence 객체에 남고 원 입력 파일 해시에도 반영된다. 근거 구조 검증은 원문의 진실성 인증이 아니다.

## 기존 실행과 연결

```bash
PYTHONPATH=src python scripts/run_research_campaign.py campaign.yaml \
  --workspace /tmp/company-research --run-dir /path/to/prepared-kr-run
```

보완 완료 후 생성된 underwriting으로 기존 strict runner를 실행한다. 단계가 막히면 `valuation-gap` 작업지시가 남는다. 수정된 입력에 대한 Compiler·Evaluator·Audit·Freeze는 다시 실행한다. 이번 구현의 선택적 재사용 단위는 수집/추론 작업이며 기존 33단계 실행을 임의로 잘라 재개하지 않는다.

LLM 역할에는 `replay`/`assisted`/`live`가 있다. assisted의 역할별 요청은 `<run>/out/staff_requests/`, 답변은 `<run>/declarations/staff/responses/<role>.<prompt_sha256>.json`이다. 예전 프롬프트의 답은 재사용하지 않는다. 기본 replay는 기존 커밋된 응답을 재현하되 소진 후 마지막 답을 반복하지 않는다.

독립 `run_kr_live.py --underwriting-path`는 검증 실행이다. `run_research_campaign.py --run-dir`는 별도 복사본에 추정값을 반영하고 영속 상태로 계산한다. 역할 답변을 보완한 후 성공한 동일 실행의 프롬프트 결합 응답을 canonical replay 입력으로 저장하고 전체 감사를 다시 통과해 실제 불변 보고서 묶음을 생성한다. 원본 실행과 기존 정상 결과는 보존한다. 이 준비/검증 기능을 미국 실제 공시 수집 adapter가 완성됐다는 뜻으로 설명하지 않는다. 공통 조사와 신사업 모형은 국가 중립이며 `--run-dir` 연결은 현재 KR runner다.

## 검증 기준

동일 응답 재실행 0건, 상위 변경 시 독립 branch 재사용, 실제 병렬 수행, 오래된 응답/잘못된 단위/출처 변조 거절을 검증한다. 신사업은 생산능력 변화가 FCFF와 실제 Compiler 입력에 반영되는지 확인한다. 기존 KISCO 실행을 연구 응답으로 변경하고 전체 단계·확률보정·감사와 최종 가치 변경까지 검증한다. 이 테스트의 가상 수정값을 실제 회사 재평가로 소개하지 않는다.


## 비교기업 이익률 조정

`build_peer_margin_proposal`은 동일 EBIT/EBITDA·회계기준의 실제 비교기업 이익/매출 비율에 명시적 가중치를 적용한다. 회계·일회성 정상화와 대상기업의 가동률·규모·원가 등 차이는 각각 percentage_points의 low/base/high로 기록한다. 기간 비교 이유와 연도별 조정 경로를 명시한다. 자동 감점·수렴 연도는 없다. 단위는 비율(0.2=20%)과 퍼센트포인트(-5=-5%p)를 구분한다.

연구 요청의 `margin_basis`는 EBIT 또는 EBITDA여야 하고 `forecast_year`는 산출 대상 연도를 명시해야 한다. 응답은 `kind: inferred`, `peer_margin: {receipt, year, case}`로 생성된 receipt와 대상 연도·경로를 묶는다. ratio 응답에는 조정된 이익률을 넣는다. 이익 금액이 필요하면 `peer_margin.revenue: {value, unit, year, source_ids, rationale}`를 명시하며 코드가 같은 연도 매출×이익률과 low/high를 재계산한다. 이 범위는 지정된 매출 하의 이익률 범위이며 매출 불확실성까지 포괄하는 확률분포가 아니다. 입력 예시는 `tests/test_peer_margin_research.py`를 참조한다.

EBITDA는 감가상각 전 이익이며 현금흐름이 아니다. EBIT/EBITDA 차용값은 기존 Bridge·Compiler로 전달한다. 이후 세금·투자·운전자본 및 부채·희석 계산이 별도로 필요하다. 동일 고정비·감가상각을 이익률 조정과 현금흐름에서 중복 차감하지 않는다.

## 계산 보완과 보고서 생성

`--recovery-provider module:callable`은 단계·원인·입력 위치·필요한 역할 프롬프트를 받아 별도 prepared_run 입력을 보완한다. 반환값으로 감사 통과를 선언할 수 없다. `--completion-rounds`의 기본값은 10이다. 콜백 없이도 completion_request.json과 역할 요청을 호스트가 읽고 직접 보완한 후 동일 명령으로 재개할 수 있다. 사용자가 파일 형식을 작성하도록 넘기는 절차가 아니다.

추정값을 바꿨으면 투자자 문안의 수치·주장도 검토한다. `declarations/report_review.json`에 현재 `underwriting_sha256`, `profile_sha256`, 검토 이유 `rationale`를 기록한다. 이는 검토 대상 결합이며 내용 진실성을 자동 인증하지 않는다. 검토 요청이 두 해시를 제공한다. 개발 식별자는 투자자 보고서에 표시하지 않는다.

완주 응답은 `COMPLETED`, 실제 `versioned_report_path`, `latest_manifest_path`를 반환한다. 사업부 미평가·보고서 누락·미해결 입력은 완주가 아니다. 보정확률이 없는 경우에도 조건별 가치와 가정으로 보고서를 작성하되 확률가중 기대값과 구체적 매수가를 만들지 않는다. 여기서 보고서 출판은 로컬 파일 생성이며 GitHub 반영과 별개다.

### Informed revision runtime integrity

`INFORMED_REVISION` does not turn original observations into forecasts. Original
primary `observed`/`calculated` research keeps its normal provenance/arithmetic
validation; individually adopted forecasts remain inferred underwriting. Candidate
citations to frozen evidence or follow-up research require exact
`context_evidence_bindings` (context ID, source ref, source-content hash and payload
hash). An unknown source family remains `UNKNOWN`, never a new independent family.

The final underwriting file must retain its outer revision mode/context, including
for informed assumptions inside a derived `business_cashflow_receipt`. The typed
runtime factory binds the actual run ID to the reviewed new-run ID and validates
the parent freeze, persistent root-lineage journal and existing attempt budget.
A new name or a bare numerical retune cannot reset that budget. `run.yaml` declares
`informed_revision_parent_freeze_path`, `informed_revision_root_freeze_path`,
`informed_revision_journal_path` and `informed_revision_max_attempts` (default 10,
the existing completion budget). Root/journal paths are trusted orchestrator
configuration, not fields the reviewing model may choose.

Post-freeze `broker_review_context.json` may bind additional local research
artifacts by exact UTF-8 bytes/hash. This provides evidence to host review, not
new input authority. Preserve complete receipts privately; public reports contain
short derived summaries and original-source links, not full broker report bodies.
