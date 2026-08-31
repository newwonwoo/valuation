# RUNBOOK — 한국 상장사 라이브 밸류에이션

이 문서는 절차서다. 엔진 설계는 다른 문서들이 설명한다; 여기는 **"종목 X를
지금 돌리려면 정확히 무엇을 하는가"** 만 다룬다. 이 절차 전체가 세 번 실행되어
리포에 박제되어 있고 (회귀: `tests/test_kr_live_runbook.py`), 아래 모든 단계는
그 실행에서 실제로 밟은 것이다. 앞의 두 런은 완주 회귀이고, 대한제강 런은
연결 다부문을 단일 `core`로 축약하지 않는 실패 폐쇄 회귀다:

- `runs/kisco-104700/` — 한국철강: 12월 결산, EV 산출 방법
  (normalized_multiple), 캘리브레이션 포함(기대값까지).
- `runs/shinhanalpha-293940/` — 신한알파리츠: **3월 결산** REIT, **자본가치
  산출 방법**(nav), 리츠 코호트 캘리브레이션(기대값까지).
- `runs/daehansteel-084010/` — 대한제강: **최초의 진짜 다부문 SOTP** —
  IFRS 8 영업부문 주석이 제강/운송/기타 3개 부문의 존재를 정하고,
  `declarations/segments.yaml`이 각 부문의 경제 성격(KSIC)을 선언하며,
  부문마다 자기 방법(철강 DCF·운송 스프레드 DCF·임대 NAV)과 자기 가정
  이름공간(`steel_fcff_…`)으로 평가돼 합산된다. 리스크팩 요구 방법 —
  L1~L4 피어 회귀베타는 커밋된 공개 시세에서
  `scripts/compute_peer_betas.py`로 재현, 코호트는 타깃 제외 재적합.

**실행자가 LLM 세션이라면**: `.claude/skills/kr-live-run`이 이 절차의 요약을
자동 로드한다. 이 문서는 그 스킬의 원본이다.

## 0. 준비물과 원칙

- OpenDART **API 키가 없어도 된다**: 공시목록·기업개황·전체 재무제표는 키리스
  공개 브릿지(예: 카카오 PlayMCP `opendart-*` 도구)로, 사업보고서 원문 섹션은
  DART 공개 뷰어(`dart.fss.or.kr/report/viewer.do`)로 온다. 키가 있으면
  `generic_kr_cli.py` 경로로 직접 라이브 페치해도 된다 — 형식은 동일하다.
- 원칙 세 가지: **원시 그대로**(가공된 요약이 아니라 raw JSON을 저장),
  **지식시점**(as_of 이후 공표물은 쓰지 않는다), **판단은 선언으로**(모델·사람
  누구의 판단이든 rationale과 출처를 단 선언 파일로만 들어간다).

## 1. 런 디렉토리를 만든다

```
runs/<종목>-<코드>/
  run.yaml                # §5
  raw/                    # §2 — 공개 원천에서 그대로
  declarations/           # §3~4 — 오퍼레이터/스태프 산출물
  out/                    # 러너가 쓴다 (커밋 안 함)
```

## 2. 원시 데이터 수집 → `raw/`

| 파일 | 원천 | 내용 |
|---|---|---|
| `corp_search.json` | find_company("회사명") | `{"companies":[{corp_code,corp_name,stock_code},…]}` 검색 결과 그대로 |
| `company.json` | get_company_info | 기업개황 raw (induty_code가 KSIC 라우팅을 결정) |
| `list.json` | search_disclosures | 공시목록 raw — **최신 정기보고서**(사업/반기/분기)가 lookback 540일 안에 있어야 한다 |
| `fnltt_<연도>_<OFS\|CFS>.json` | get_full_financial_statement | 전체 재무제표 raw (`account_id`가 있는 형태). 연결 없는 회사는 OFS |
| | | **12월 결산이 아니면** (`company.json`의 acc_mt): business_year는 회계연도가 **끝나는** 해다 (2026-03 결산 사업보고서 → bsns_year "2026"). 응답의 `rcept_no`가 채택한 정기보고서와 일치하는지 반드시 대조 — 다르면 전기 보고서를 받은 것이다 |
| `filing_<rcept_no>/` | DART 공개 뷰어 | 최신 정기보고서의 "사업의 내용" 섹션들 (§2.1) |

### 2.1 원문 섹션 뜨는 법 (키리스)

1. `dart.fss.or.kr/dsaf001/main.do?rcpNo=<rcept_no>` HTML을 받는다.
2. HTML 안의 목차 트리에서 원하는 섹션 제목(예: "원재료 및 생산설비",
   "매출 및 수주상황", "주요 제품 및 서비스", "주식의 총수")을 찾아 그 노드의
   `dcmNo / eleId / offset / length` 를 읽는다.
3. `report/viewer.do?rcpNo=…&dcmNo=…&eleId=…&offset=…&length=…&dtd=dart4.xsd`
   로 각 섹션 HTML을 받아 `filing_<rcept_no>/<rcept_no>_<eleId>.xml` 로 저장.
   러너가 이 디렉토리를 원문 아카이브로 조립하고, 추출 영수증(멤버 SHA-256·
   스팬)은 평소처럼 남는다.

### 2.2 다부문 회사라면 (스크린이 "multiple operating segments"로 멈출 때)

1. 최신 정기보고서의 **연결재무제표 주석 "영업부문 정보"** 섹션을 §2.1
   방법으로 받아 `filing_<rcept>/` 에 추가한다 — 부문의 존재와 부문별
   매출·영업이익의 유일한 정본이다 (스크린이 뱉는 목록은 경보이지 부문
   목록이 아니다: 공정명·소계행·업종명이 섞여 있다).
2. `declarations/segments.yaml` 을 쓴다: 주석의 부문명과 **정확히 일대일**로
   `segment_id / disclosed_name / ksic_code / rationale`. 회사 KSIC는 회사를
   타이핑할 뿐 운송부문을 타이핑하지 못하므로, 부문별 KSIC 선언이 곧 부문별
   원형 라우팅이다 (미등재 KSIC는 분류맵 추가 — 리뷰되는 데이터 변경).
3. run.yaml은 `method:` 대신 `segments:` 목록으로 부문별 방법을 적는다.
   `filing.segment_id`는 공시 KPI 추출(단가·가동률 표)이 붙을 부문이다.
4. **가정 키는 부문 이름공간을 쓴다**: `steel_fcff_year_1`,
   `transport_ownership` 처럼 `<segment_id>_<키>`. 시나리오 변형은 한정어를
   앞에 — `down_steel_fcff_year_1`. 언더라이팅 각 행에 `segment:` 필드를
   달고, 두 부문이 같은 지표(input_price 등)를 가지면 그 지표를 **행
   리스트**로 선언한다 (증거 ID는 `UW:<타깃>:<지표>:<부문>`이 된다).
5. 부문 합산(SOTP)의 잔차 — 부문합계와 연결 전체의 차이(내부거래 제거) — 는
   엔진이 명시적으로 들고 다닌다. 잔차를 무시한 합산은 구조적으로 불가능하다.

## 3. 오퍼레이터 선언 → `declarations/`

- **`underwriting.yaml`** (필수): 방법이 요구하는 가정 키 전부. 키 목록은
  엔진에게 물어라 —
  `required_assumption_keys(method_choices=…, forecast_years=…)`.
  자본가치 산출 방법(nav·ddm·ffo_multiple·pb_roe 등)은 `ev_adjustment`가
  키 목록에 없다 — EV→자본 브릿지가 없는 방법에 그 선언을 만들지 마라
  (브릿지 제안이 존재하지 않는 evidence_id를 인용하게 된다).
  값·단위·**20자 이상의 rationale**(가능한 한 공시 수치 인용) 필수.
  선언마다 그 판단을 실제로 뒷받침한 `source_ref` 또는 `source_refs`를 적는다.
  여러 공시를 함께 썼다면 파일 상단의 공통 링크로 뭉개지 말고 해당 선언의
  `source_refs`에 원문 링크를 모두 보존한다.
  다중 시나리오면 사이클 민감 키의 시나리오 한정 변형
  (`down_normalized_ebitda` 등)도 여기 선언하고 §5의
  `extra_required_evidence`에 등록한다.
- **`market.yaml`** (권장): 공개 시세 종가 + as_of + source_ref.
- **`street.json`** (권장): 인증된 증권사 export. **커버리지가 없으면
  `{"authorization_basis":"explicit_permission","reports":[]}`** — 무커버리지
  선언이지 생략이 아니다. 시장가격 기준일과 각 증권사 보고서 발간일은 모두
  `run.yaml`의 as_of 이하여야 한다.
- **`risk_pack.yaml`** (DCF·NPV·DDM 등 베타 요구 방법일 때):
  `python scripts/draft_risk_pack.py template`으로 골격을 뽑고, 채운 뒤
  `… check <파일> --ticker <코드>` 로 런타임과 동일 검증을 미리 돌린다.

## 4. 스태프 제안 → `declarations/staff/`

역할당 JSON 파일 하나(`intelligence_officer.json`, `red_team_officer.json`,
`bridge_analyst.json`, 정적 패턴이 못 읽는 지표가 있으면
`filing_locator_analyst.json`). 파일이 배열이면 수리 루프의 연속 턴이다.

- 형식은 예시 런의 파일을 그대로 본떠라. 규칙은 엔진이 강제한다: 존재하는
  evidence_id만 인용, identity 트랜스폼은 값=인용값, 로케이터 quote는 원문에
  유일하게 실재. **거부되면 정지 메시지에 사유가 그대로 나온다 — 제안을
  고쳐서 재실행하는 것이 수리 루프다.**
- 라이브 모델을 붙일 거면 `VALUATION_LLM_TRANSPORT`(모듈:빌더). 기성 빌더:
  `scripts/anthropic_transport.py` — stdlib HTTP만 쓰고 자격증명은
  `ANTHROPIC_API_KEY` 환경변수에서만 읽는다
  (`export VALUATION_LLM_TRANSPORT=anthropic_transport:build`,
  모델은 `VALUATION_LLM_MODEL`, 프록시는 `ANTHROPIC_BASE_URL`, 출력 한도는
  `VALUATION_LLM_MAX_TOKENS`). 러너(`run_kr_live.py`)는 **하이브리드**다:
  파일이 있는 역할은 항상 파일이 이기고(커밋된 런의 리플레이 불변), 파일이
  없는 역할만 라이브 모델에 위임된다. `generic_kr_cli.py` 경로는 전 좌석이
  트랜스포트다 — 계약은 동일하다. 라이브 transport의 비밀키를 제외한 이 설정과
  모듈 코드도 실행 입력 해시에 묶이므로 바꾸면 기존 번들을 재사용하지 않는다.

## 5. `run.yaml`

`runs/kisco-104700/run.yaml`을 복사해 고쳐라. 핵심 필드:
`company_query / as_of / scenario_ids / method(archetype/method[/version]) /
filing(business_year·report_code·fs_div·fiscal_period_end) /
extra_required_evidence / market_currency`.

**기대값(확률가중)까지 원하면 `calibration:` 블록**: 코호트 아티팩트가 있어야
한다. 없으면 —
1. 동종사(타깃 **제외**) 5곳 이상 × 다년 실적 이력을 §2와 같은 원천에서 모아
   `{"rows":[{company_id,period_end,published_at,values,source_ref}]}` 로 저장
   (예: `config/kr_steel_cohort_dataset.json` 12사 91행,
   `config/kr_reit_cohort_dataset.json` 7사 57행).
   **결산 주기가 섞이면 안 된다** — 반기 결산 리츠의 6개월 성장률과 12월
   결산사의 연간 성장률은 같은 축이 아니다. 타깃과 같은 주기의 회사만
   코호트에 넣는다 (리츠 코호트가 반기 7사로 좁혀진 이유).
2. `python scripts/build_calibration_artifact.py --dataset … --drivers … \
   --scenarios Down,Base,Bull --path-length 5 --exclude-ticker <코드> \
   --conditioning-json …` → 아티팩트·프로버넌스 파일과 **BindingConstants**가
   출력된다. 그 상수를 `calibration.constants`에 그대로 붙여넣는다.
3. conditioning은 **타깃 자신의 최신 실측**(값 + 출처 URL + first_seen_at +
   원천 파일 sha256)이다.

캘리브레이션이 없으면 그 블록을 빼라 — 시나리오 범위는 나오고 기대값은
정직하게 "미산출"로 남는다.

## 6. 실행과 막힘 대처

```bash
PYTHONPATH=src python scripts/run_kr_live.py runs/<종목>-<코드>
```

완주하면 사용자 전달본은 `out/bundles/<종목>_<기준일>_TP<기준가>_<해시>/`
아래의 버전 고정 Markdown이며, 같은 디렉토리에 감사 JSON·33단계 trace·SVG
2장·실행 증명이 함께 보존된다. `out/<종목>_LATEST_REPORT.json`이 그 번들과
각 SHA-256을 가리키고, `out/final_report.md`는 자동화용 최신 별칭일 뿐이다.
**정지하면 정지 메시지가 작업지시서다**:

| 정지 지점 | 뜻 | 대처 |
|---|---|---|
| `PRIMARY_EVIDENCE_COLLECTION` — required … missing: metrics=… | 이름 나온 지표의 증거가 없다 | 공시에 있으면 §2.1 섹션 추가+로케이터, 판단이면 §3 선언 추가 |
| `INDUSTRY_DNA_ROUTE` — unmapped KSIC | 분류맵에 없는 업종 | `config/kr_industry_classification_map.yaml`에 prefix 행 추가 (리뷰되는 데이터 변경) |
| `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT` — multiple operating segments | 연결 공시가 둘 이상의 부문을 명시 | 단일 `core`로 축약하지 말고 다부문 방법 의도·입력 지원 전까지 중단 |
| `RESEARCHER_A/BRIDGE` — proposal failed: … | 스태프 제안이 계약 위반 | 메시지의 사유대로 §4 파일 수정 (없는 ID 인용, 값 불일치 등) |
| `HIERARCHICAL_BETA_ESTIMATION` — no LIVE_PRIMARY provider | 베타 요구 방법인데 리스크팩 없음 | §3의 risk_pack.yaml |
| `STREET_REFERENCE_LOAD` — not configured | street.json 자체가 없음 | 무커버리지면 빈 reports로 선언 |
| freshness/지식시점 위반 | as_of가 데이터보다 이르거나 공시가 너무 오래됨 | as_of·수집물 정합 확인 |

같은 지표의 정지가 반복되면 그 지표는 이 회사 공시에 없는 것이다 — 선언으로
채우든가, 채울 수 없으면 그 gap이 곧 이 회사에 대한 정직한 결론의 일부다.

## 7. 끝났으면

- 재현 가치가 있는 런이면 디렉토리를 커밋한다 (`out/`은 제외 —
  `.gitignore`). 커밋된 런은 `tests/test_kr_live_runbook.py` 패턴으로 값을
  pin해 전체 파이프라인 회귀가 된다.
- 리포트 전달은 `LATEST_REPORT.json`이 가리키는 **버전 고정 Markdown** 원문
  그대로다. 최신 별칭은 전달하지 않는다. 챗 계층은 `chat_dispatch`의
  SHA-256 핸드오프로 숫자 무변조를 강제할 수 있다.

## 8. F1 — 공시 트리거 자동 재실행 (감시 루프)

커밋된 런은 일회성 스냅샷이 아니라 감시 대상이다. 정기 Routine(예: 평일
KST 아침, Claude Code Remote 트리거)이 새 세션을 띄워 다음을 수행한다:

1. 작업 브랜치를 체크아웃하고, `runs/` 아래 커밋된 각 런의 `run.yaml`에서
   종목·`as_of`를 읽는다.
2. `python scripts/check_new_filings.py` — DART 공개 검색
   (`dsab007/detailSearch.ax`, 키·MCP 불필요)으로 런별 **as_of 이후** 신규
   공시를 조회해 정기보고서·주요사항보고를 `ACTIONABLE`로 표시한다
   (있으면 exit 1). PlayMCP가 있는 세션이면
   `opendart-search_disclosures`로 교차 확인해도 된다.
3. 신규 정기보고서·주요사항보고가 없으면 아무 산출물 없이 종료한다 —
   무변화 알림은 소음이다.
4. 있으면 이 런북 §2~6 절차로 그 런 디렉토리를 갱신(raw 재수집, as_of
   전진, 선언 보수)하고 재실행한 뒤, **직전 커밋된 리포트와의 시나리오
   값·논지 변화(델타)** 를 요약해 보고한다. 리포트 숫자는 언제나
   `out/<종목>_LATEST_REPORT.json`이 지목한 버전 고정 Markdown 원문이다.
5. 재현 가치가 있으면 같은 브랜치에 커밋·푸시한다. 게이트 완화·수치 임의
   변경 금지는 여기서도 동일하다.

트리거 관리는 CCR MCP 도구(`list_triggers` / `update_trigger` /
`delete_trigger`)로 한다.

## 하지 않는 것

숫자를 지어내지 않는다. 타깃을 자기 코호트·자기 피어에 넣지 않는다. as_of
이후 지식을 쓰지 않는다. 게이트를 완화해서 완주를 사지 않는다 — 이 절차의
모든 "막힘"은 버그가 아니라 엔진이 요구하는 다음 입력의 이름이다.
