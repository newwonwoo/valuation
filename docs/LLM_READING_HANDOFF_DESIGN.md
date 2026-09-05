# LLM 판독 영역과 전달(핸드오프) 기초 설계안

> 상태: 설계 제안. 코드 변경 없음. 전제 조사: `docs/LLM_READING_SURFACE_SURVEY.md`.
> 원칙은 기존 로케이터 케이지(`llm_filing_locators.py`)를 그대로 확장한다 —
> **LLM은 위치·분류를 제안하고, 숫자는 기계가 재추출·대사한다.**

## 1. 왜 "파서로 하면 자주 고치는가" — 실측 신호

| 신호 | 근거 |
|---|---|
| 판매단가 앵커 사전이 런 3개에 **3번** 늘었다 (`제품등의 가격변동` → `제품별 구체적인 가격변동추이` → `주요 제품에 대한 가격변동(단가) 추이`) | `config/kr_filing_kpi_patterns.yaml:117-127` 주석 3개 |
| 단위 사전이 **2번** 늘었다 (`원/Ton`, `(/MT)`) | 같은 파일 :134, :137-141 |
| 영업부문 주석 파서가 **3번** 다시 쓰였다 (대한제강 열 밀림 → 고려아연 XBRL 축 표·접미사 없음 → 2단 헤더 #168) | `segment_note.py` 이력, PR #161/#168 |
| 정적 정규식이 실전 판매단가·가동률을 **0/6** 잡았다 | 4개 런 `filing_locator_analyst.json` |

패턴은 명확하다: **새 발행사 1개 = 파서 패치 1개.** 이 비용이 발생하는 콘텐츠는
정규식·앵커·격자 규칙이 "형식"이 아니라 "특정 회사의 습관"을 흉내 내고 있는 것이다.

### 1.1 판정 기준 — LLM 영역으로 둘 조건

아래 중 하나라도 해당하면 파서 고정이 아니라 LLM 판독 + 기계 검증으로 둔다.

- **A. 어휘 변동**: 표 제목·행 라벨·단위 표기가 발행사·연도마다 다르다 (앵커 사전이 자란다).
- **B. 구조 변동**: rowspan 다층, 빈 셀 생략, XBRL 축 표 vs 뷰어 표, 단위가 헤더/행에 분리, 소계·합계 행 위치 불규칙.
- **C. 표가 아니다**: 사실이 문장에 있다.
- **D. 판단이다**: 같은 원문에서 두 사람이 다르게 분류할 수 있다 (정책·경제유형·사유).
- **E. 자유서술 본문**: 제목 유형은 표준이지만 본문은 발행사가 자유롭게 쓴다 (수시공시).

반대로 **파서를 유지할 조건**: 법정 서식으로 고정된 표(주식의 총수), 계정 id가 있는 API(fnltt),
메타데이터(공시목록·rcept), 외부 수치 API(시세·금리).

## 2. LLM 영역 목록

### 2.1 사업의 내용 — 반정형 표 (조건 A·B)

| # | 콘텐츠 | 변동 이유 | 수요 |
|---|---|---|---|
| L1 | 제품 가격변동추이 (판매단가) | 제목 3사 3종, 단위 4종, 옆 원재료 표와 혼동 | `realized_price`, `output_price` |
| L2 | 원재료 가격변동추이·매입액 | 산출기준(기준품위·FX)이 표 밖 문장 | `input_price` |
| L3 | 생산능력·생산실적·가동률 (물리단위, 다층 rowspan, 사업장별) | 부문×공정×사업장 구조가 회사마다 다름, "대표 행" 선택이 판단 | `capacity`, `production`, `utilization` |
| L4 | 수주현황 (수주총액·잔고), 계약부채 | 당기 컬럼 표기가 없거나 다름 | contracted_backlog |
| L5 | 품목별 매출·매출 비중, 판매경로 | 표 형식 자유 | 부문 배분·판단 근거 |

### 2.2 재무제표 주석 — 비정형 표 (조건 A·B)

| # | 콘텐츠 | 변동 이유 | 수요 |
|---|---|---|---|
| L6 | 영업부문 정보 (IFRS 8) | 레이아웃 3종 실증, 부문명 자유, 반올림 잔차 | 부문 정본·SOTP 잔차 |
| L7 | 차입금·사채 상세, 만기표, 리스부채 | 계정명 자유, 메모행(유동성 대체분) 이중계상 | `ev_adjustment`, `debt`, `maturities` |
| L8 | 우발부채·약정사항, 담보제공, 채무보증 | 표+문장 혼합, 조건부 금액 | 브리지 조정, 레드팀 |
| L9 | 타법인출자 현황 | 열 밀림, 상장/비상장 구분 | 비영업자산 |
| L10 | 특수관계자 거래, 금융상품 범주별 | 회사별 분류 다름 | 브리지·잔차 해석 |

### 2.3 서술문 (조건 C)

| # | 콘텐츠 | 수요 |
|---|---|---|
| L11 | 가동률 서술("24시간 연속조업… 100%") | `utilization` |
| L12 | 생산능력 산출기준("시간당 능력×8,760=64만톤") | `capacity` |
| L13 | 가격변동 요인 (LME 평균가·재고·수급) | `benchmark_price`, kill condition |
| L14 | 원재료 공급 안정성·장기계약 | 스프레드 지속성 판단 |
| L15 | 수익인식 정책·계약 취소조항 | contracted_backlog 게이트 (코드가 "표로 못 읽음" 선언) |
| L16 | MD&A 유동성·자금조달, 부외거래 | 운전자본·브리지 판단 |

### 2.4 분류·판단 (조건 D — 리뷰 필수)

| # | 콘텐츠 | 수요 |
|---|---|---|
| L17 | 부문의 경제유형 (KSIC/원형) | `segments.yaml`, 분류맵 |
| L18 | 부문명 기간 간 매핑 ("기타부문" ↔ "폐기물처리 및 기타사업") | `match_note` |
| L19 | 정정공시·재무제표 재작성 사유 | 소스 신뢰도 |
| L20 | 감사의견 강조사항·내부통제 | 리스크 플래그 |
| L21 | 내부거래 제거액의 성격 | SOTP 잔차 해석 |

### 2.5 수시공시 본문 (조건 E)

| # | 유형 | 수요 |
|---|---|---|
| L22 | 주식소각·자기주식 처분·유상증자 결정 | `diluted_shares` 갱신 |
| L23 | 신규시설투자·장래사업계획 | `expansion_*` 9지표 (정의만 있고 공급 없음) |
| L24 | 영업(잠정)실적·실적 전망 | 미드사이클 최신 앵커 |
| L25 | 배당결정 | `forward_distribution` |
| L26 | 채무보증·채무증권 발행 | 브리지·우발부채 |
| L27 | 소송·경영권분쟁·제재·풍문해명 | 레드팀 이슈, kill condition |
| L28 | 대량보유·임원소유 변동 | 유통주식·지배구조 |

### 2.6 파서 유지 (LLM 영역 아님)

주식의 총수 표, fnltt 계정(확장 포함), 배당·주당이익 표, 공시목록 메타, 시세·금리·ERP 외부 API,
KOSIS 시계열. 이들은 §4 파서 확장 항목이며 여기서 제외한다.

## 3. 전달 설계 — "제안 → 검증 → 증거 → 가정" 4단 계약

현재 로케이터 한 종류(`LocatorProposal`)를 **제안 타입 4종**으로 늘리고, 검증·영수증·전달 규칙은
하나로 통일한다. 모든 제안은 **원문 좌표를 동반**하고, 어떤 제안도 숫자를 직접 원장에 쓰지 않는다.

### 3.1 판독 과제 등록부 (ReadingTask Registry)

`config/kr_filing_kpi_patterns.yaml`의 후속. 정규식 대신 **의미 필드**를 등록한다.

```yaml
reading_tasks:
  realized_price:
    definition: 보고기간 제품 평균 판매단가
    proposal_types: [table_cell, locator]        # 허용 제안 타입
    unit_dimension: PRICE_PER_MASS               # 단위 계약 레지스트리 차원
    period: current_reporting_period              # 연대 규칙
    table_identity:
      must_have_any: [제품, 판매, 단가, 가격변동]   # 표 정체 사후검증 어휘(작고 안정적)
      must_not_have: [원재료, 매입, 부재료]        # 세탁 차단(배제 규칙만 유지)
    identity_checks: []                           # 항등식 없음
    evidence_layer: REALIZED_OR_FILING
  segment_note:
    proposal_types: [table_grid]
    identity_checks: [parts_sum_equals_total]     # 부분합=합계 (반올림 허용 규칙 포함)
  borrowings_detail:
    proposal_types: [table_grid]
    identity_checks: [components_sum_equals_total, no_memo_row_double_count]
  revenue_recognition:
    proposal_types: [classification]
    vocabulary: [point_in_time, over_time, mixed]
    effect: review_required                        # 자동 효력 없음
  share_count_event:
    proposal_types: [event]
    event_types: [buyback_cancel, treasury_disposal, rights_issue]
    fields: {shares: COUNT, effective_date: DATE}
```

앵커 사전은 **자라지 않는다** — 표 정체는 LLM이 제안하고, 등록부의 작은 배제 어휘가
사후검증한다. 새 발행사가 와도 등록부는 안 바뀌는 것이 목표다.

### 3.2 제안 타입 4종 (LLM 출력 스키마)

| 타입 | 필드 | 재추출 방식 | 대상 |
|---|---|---|---|
| `locator` (기존) | member, quote(멤버 내 유일), value_text, unit_token | `re.escape(quote)` 패턴으로 `extract_dart_kpi` | L11·L12·L13 등 문장 속 수치 |
| `table_cell` (신규) | member, table_index, row_path[…], col_path[…], value_cell(r,c), unit_source{cell/header/row_label}, period_cell | `_expand_table` 격자에서 좌표 재추출; 행·열 라벨 경로가 격자 헤더와 일치해야 함 | L1~L5, L7, L9 |
| `table_grid` (신규) | member, table_index, role_map{row_label→field, col_label→segment/period}, total_row, total_col | 격자 전체를 구조로 읽고 **항등식 대사** | L6, L7, L8, L10 |
| `classification` (신규) | field, label ∈ vocabulary, quotes[≥1, 유일], rationale | 인용 유일성·연대만 검증; 값 효력 없음 | L15, L17~L21 |
| `event` (신규) | rcept_no, event_type ∈ enum, fields{name: {value_text, quote}} | 필드별 locator 재추출 + 공시목록 rcept 실존·연대 | L22~L28 |

공통 규칙: 제안은 **런 디렉토리 `declarations/staff/<role>.json`에 박제**된다(현 로케이터와 동일).
재실행은 모델 없이 박제된 제안을 검증기에 통과시켜 **결정론적으로 재생**된다 — "LLM은 한 번, 재생은 기계".

### 3.3 검증기 (Verifier) — 전부 결정론, 전부 거부형

| 검증 | 적용 타입 | 내용 |
|---|---|---|
| 인용 유일성 | 전부 | quote가 멤버 정규화 텍스트에 정확히 1회 |
| 표 정체 | table_* | 격자 헤더/제목 셀에 `must_have_any` 포함, `must_not_have` 불포함 (원재료 표로 판매단가 세탁 차단) |
| 좌표 정합 | table_cell | row_path/col_path가 격자의 실제 헤더 계층과 일치 (rowspan 전개 후) |
| 단위 차원 | 수치 전부 | unit_token → 단위 계약 레지스트리 차원 == 과제 차원. `(/KG)`를 톤당으로 못 씀 |
| 연대 | 전부 | 전기/전망/가정 어휘 배제, 당기 마커 또는 회계연도 문자열 (`validate_filing_period_context` 재사용) |
| 항등식 | table_grid | 부분합=합계(단위별 반올림 허용 `(n+1)×0.5`), Ⅳ=Ⅱ−Ⅲ, 메모행 이중계상 금지 |
| 교차 소스 | table_grid | 주석 표 총계 == fnltt 계정값 (예: 부문합계+조정 == 연결 매출) |
| 절단 감지 | 전부 | 멤버가 12,000자 절단됐고 제안이 `not_found`면 gap 사유를 `TRUNCATED`로 표기 |
| 사전 통제 | 전부 | 제안 안의 지시문·URL·키는 데이터로만 취급(기존 위협 모델 유지) |

### 3.4 산출물 — Evidence 영수증 확장

통과한 제안은 `EvidenceRecord`가 된다. 기존 `DARTKPI:<rcept>:<segment>:<metric>:<hash>` 영수증에 다음을 추가한다.

- `proposal_type`, `proposal_sha256`(박제된 제안 파일 해시), `reader_id`(모델/트랜스포트 식별자, 자격증명 없음)
- `grid_receipt`: table_index, (r,c), row_path, col_path, 격자 SHA-256
- `verifiers_passed`: 통과한 검증기 목록 + 항등식 잔차 값(예: 반올림 잔차 1,000원)
- 계층: 재추출 수치는 `REALIZED_OR_FILING`; `classification`은 **새 계층 `LLM_PROPOSED_CLASSIFICATION`** — 원장에는 남되 가정 컴파일 진입 금지, 리뷰를 거쳐 선언/분류맵 데이터로만 효력

### 3.5 다음 스텝으로의 전달 규칙

1. **브리지 드래프트가 선언 대신 증거를 인용한다.** 현재 4개 런의 bridge draft는 100% `UW:` 인용이다.
   판독 증거가 생기면 identity 드래프트는 `DARTKPI:`/`DARTGRID:` id를 인용하게 되고, 리포트의
   "공시 직접 인용 비율"이 0%에서 실제 값으로 올라간다.
2. **손 가공은 등록 transform으로 이관한다.** 연환산(반기×2), FX 환산, 합산 브리지, 자기주식 차감은
   `transform_id`(기존 `TRANSFORMS` 레지스트리)로 기계가 계산하고 입력 증거를 인용한다.
   → 조사의 "가공 14.6%"가 판단에서 빠져나온다.
3. **판단은 남는다, 그러나 분리된다.** 미드사이클 FCFF·터미널 성장·ROIC는 여전히 선언이다.
   선언 계층에 `origin: {transcribed, transformed, judged}` 표식을 두어 "전사 12.6%"가
   판단과 같은 신뢰도 0.6을 받지 않게 한다.
4. **실패는 이름 붙은 gap이다.** 사유 코드 `NOT_FOUND / AMBIGUOUS / UNIT_UNREGISTERED /
   PERIOD_UNMARKED / IDENTITY_MISMATCH / TABLE_IDENTITY_REJECTED / TRUNCATED`.
   정지 메시지가 작업지시서라는 원칙 유지 — 무엇을 어느 섹션에서 다시 읽어야 하는지 이름을 부른다.
5. **분류 제안은 리뷰 큐로 간다.** `classification` 결과는 PR/데이터 변경 제안으로 렌더되고,
   승인되면 `segments.yaml`·분류맵에 들어간다. #159/#161의 46/38 기각과 같은 경로.
6. **사건(event)은 영향 경로 규칙으로 연결한다.** 예: `buyback_cancel` → `diluted_shares`
   재계산 요구; `capex_commitment` → `expansion_*` 게이트 입력; `provisional_earnings` →
   컨디셔닝 최신성 경고. 연결은 결정론 규칙표, 사건 추출만 LLM.
7. **Variance gate와 맞물린다.** 판독이 증거 id로 표준화되면 두 실행자의 차이가
   "판독 차이(증거 id 불일치)"와 "판단 차이(선언 값 차이)"로 자동 분리된다.

## 4. 도입 순서 (작게, 실증된 것부터)

| 단계 | 내용 | 대상 | 검증 |
|---|---|---|---|
| 1 | `table_cell` 제안 + 표 정체 사후검증 + 연환산 transform | L1·L2·L3 (가격표·원재료·물리단위 생산/가동률) | 4개 런의 로케이터 JSON을 `table_cell`로 재작성해 동일 값 재생 |
| 2 | `table_grid` + 항등식·교차소스 대사 | L6·L7·L9 (부문 주석 잔여 행, 차입금 상세, 타법인출자) + fnltt 계정 확장(§2.6) | 고려아연 브리지 −1,666십억을 증거 인용으로 재구성 |
| 3 | `event` 제안 + 영향 경로 규칙 3종 | L22·L23·L24 | 고려아연 소각·증자 공시로 `diluted_shares` 재계산 재생 |
| 4 | `classification` + `LLM_PROPOSED_CLASSIFICATION` 계층 + 리뷰 큐 | L15·L17·L18 | 46/38 사례를 "제안→기각" 흐름으로 재현 |
| 5 | 서술문 로케이터 확대 (L11~L14·L16), 레드팀 입력(L19·L20·L27) | | 인용 검증만으로 케이지 |

## 5. 지키는 선

- LLM은 숫자를 쓰지 않는다. 좌표·인용·분류만.
- 등록부는 발행사마다 자라지 않는다. 자라는 건 배제 규칙뿐.
- 통과 못 한 제안은 gap이지 값이 아니다. 런은 사유를 이름으로 부르고 멈춘다.
- 재실행은 모델 없이 박제된 제안으로 재생된다.
- 분류는 리뷰된 데이터로만 효력이 생긴다.
