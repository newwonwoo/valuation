---
name: kr-live-run
description: 이 레포에서 실제 한국 상장 종목의 라이브 밸류에이션을 요청받았을 때 ("ㅇㅇ 분석해줘", "분석시작 ㅇㅇ", "라이브 런", 종목명/6자리 코드와 함께 가치평가·적정주가·기대값 요청). PRISM 엔진을 절차대로 부리는 런북을 로드한다 — 엔진 코드 수정이나 테스트 픽스처 작업에는 쓰지 않는다.
---

# KR 라이브 런 — 오퍼레이터 절차

정본은 `docs/RUNBOOK_KR_LIVE.md`. 이 스킬은 그 요약 + LLM 세션용 주의점이다.
**전 절차가 한국철강으로 실증되어 `runs/kisco-104700/`에 박제**되어 있다 —
모든 파일 형식은 그 디렉토리를 그대로 본뜨면 된다.

## 순서 (요약)

1. **런 디렉토리** `runs/<종목>-<코드>/` 생성, `runs/kisco-104700/run.yaml` 복사.
2. **raw/ 수집** — 키 불필요:
   - PlayMCP `opendart-find_company` → `raw/corp_search.json`
   - `opendart-get_company_info` → `raw/company.json` (induty_code = KSIC 라우팅)
   - `opendart-search_disclosures` → `raw/list.json` (최신 정기보고서 포함해야)
   - `opendart-get_full_financial_statement` → `raw/fnltt_<연도>_<OFS|CFS>.json`
   - 원문 섹션: `dart.fss.or.kr/dsaf001/main.do?rcpNo=…` HTML의 목차 트리에서
     dcmNo/eleId/offset/length 추출 → `report/viewer.do?…&dtd=dart4.xsd` →
     `raw/filing_<rcept>/<rcept>_<eleId>.xml`
   - **응답이 크면 수집을 서브에이전트에 격리**하고 파일만 받는다.
3. **declarations/** — underwriting.yaml(키 목록은
   `required_assumption_keys()`가 출력; rationale 20자+, 공시 수치 인용),
   market.yaml(공개 종가), street.json(무커버리지면 빈 reports 선언),
   베타 요구 방법이면 risk_pack.yaml(`scripts/draft_risk_pack.py`).
4. **staff/** — 네가 스태프 좌석이다: intelligence_officer / red_team_officer /
   bridge_analyst / (필요시) filing_locator_analyst 의 제안 JSON을 작성한다.
   존재하는 evidence_id만 인용, identity는 값=인용값, 로케이터 quote는 원문에
   유일 실재. 예시 런의 파일이 정확한 형식이다.
5. **기대값까지 원하면** calibration 블록: 동종사(타깃 제외) 이력 →
   `scripts/build_calibration_artifact.py` → 출력된 BindingConstants를
   run.yaml에 붙여넣기. 기존 코호트가 맞으면 재사용
   (철강: `config/kr_steel_calibration_artifact.json`).
6. **실행**: `PYTHONPATH=src python scripts/run_kr_live.py runs/<종목>-<코드>`

## 철칙

- **정지 메시지가 작업지시서다.** 거기 이름 나온 것(누락 지표, 거부 사유)만
  채우고 재실행한다. 게이트를 완화하거나 값을 지어내서 완주를 사지 않는다.
- as_of 이후 공표물 금지. 타깃을 자기 코호트/피어에 넣지 않는다.
- 리포트 숫자는 절대 바꿔 말하지 않는다 — 전달은 `out/final_report.md` 원문,
  챗 계층은 `chat_dispatch` SHA-256 핸드오프.
- 막힘별 상세 대처표: `docs/RUNBOOK_KR_LIVE.md` §6.
