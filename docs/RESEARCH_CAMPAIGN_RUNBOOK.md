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

증권사 자료의 `broker_lead`/`broker_estimate`는 단서·보조 추정이다. primary/independent 검색 시도와 결과를 `search_attempts`로 남긴다. 증권사 추정은 `inferred` 및 `independent_reasoning`을 요구한다. 목표가·현재가·추천등급·목표배수는 금지하며, 대상 회사 실적 컨센서스를 정답으로 직접 복사하는 경로가 아니다. 원문 출처의 내용·독립성 검토는 호스트 책임이다. 여러 보고서가 같은 원자료를 재인용하면 source_family도 같아야 한다.

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
