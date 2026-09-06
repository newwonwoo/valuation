---
name: kr-live-run
description: 이 레포에서 실제 한국 상장 종목의 라이브 밸류에이션을 요청받았을 때 ("ㅇㅇ 분석해줘", "분석시작 ㅇㅇ", "라이브 런", 종목명/6자리 코드와 함께 가치평가·적정주가·기대값 요청). PRISM 엔진을 절차대로 부리는 런북을 로드한다 — 엔진 코드 수정이나 테스트 픽스처 작업에는 쓰지 않는다.
---

# KR 라이브 런 — 오퍼레이터 절차

정본은 `docs/RUNBOOK_KR_LIVE.md`. 이 스킬은 그 요약 + LLM 세션용 주의점이다.
**전 절차가 세 종목으로 실증되어 박제**되어 있다 — 파일 형식은 이 디렉토리를
그대로 본뜨면 된다:

- `runs/kisco-104700/` — 12월 결산 · EV 산출 방법 · 철강 코호트(기대값)
- `runs/shinhanalpha-293940/` — 3월 결산 REIT · 자본가치 방법(nav) · 리츠
  코호트(기대값). 비12월 결산이면 business_year는 회계연도가 **끝나는** 해;
  fnltt 응답의 rcept_no를 채택 보고서와 대조. 자본가치 방법은
  `ev_adjustment` 키가 없다 — 선언 만들지 마라.
- `runs/daehansteel-084010/` — **다부문 SOTP** (제강 DCF + 운송 스프레드
  DCF + 임대 NAV) · 리스크팩 요구 · 피어 회귀베타는
  `scripts/compute_peer_betas.py`로 커밋된 공개 시세에서 재현. 다부문이면:
  영업부문 주석 섹션 추가 → `declarations/segments.yaml`(주석 부문명과
  일대일) → run.yaml `segments:` 목록 → 가정 키는 `<부문>_<키>` 이름공간,
  변형은 `down_<부문>_<키>`. 상세: 런북 §2.2.

## 순서 (요약)

1. **run.yaml은 resolver가 쓴다**: 메타데이터 3종 수집 후
   `python scripts/resolve_kr_run.py runs/<종목>-<코드> --as-of … --ticker … --method …`
   → run.yaml + 근거 영수증 out/resolver.json. 정하지 못한 것은 이름 붙은 gap.
   원문 섹션은 `python scripts/collect_kr_filing.py runs/<종목>-<코드> --rcept <no>`
   가 역할(`config/kr_filing_toc_roles.yaml`)로 고른다 — 멱등, manifest 영수증.
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
5. **기대값까지 원하면** calibration 블록: **타깃 자기 이력**으로 적합한다.
   측정 = 타깃의 연도별 실현 동인(한 회계기준 위, 최소 5개 전이),
   선언 = 각 시나리오가 **가정하는** 동인 경로 →
   `scripts/build_self_calibration_artifact.py` → 출력된 BindingConstants를
   run.yaml에 붙여넣고 `self_calibrated: true`와
   `external_probability_source: target_realized_dispersion_monte_carlo`를
   같이 적는다 (둘이 어긋나면 러너가 멈춘다).
   **새 피어 코호트는 만들지 않는다** — 시나리오 확률은 공유 시장가격이 아니라
   그 회사의 경제가 정한다 (`AGENTS.md`). 철강·리츠 코호트는 규칙 이전에
   커밋된 것으로 해당 런 재생용으로만 남아 있고, 따라 할 선례가 아니다.
   전이가 모자라면 거부가 곧 답이다 — 기대값 없이 완주하고 사실대로 보고한다.
6. **실행**: `PYTHONPATH=src python scripts/run_kr_live.py runs/<종목>-<코드>`

## 철칙

- **정지 메시지가 작업지시서다.** 거기 이름 나온 것(누락 지표, 거부 사유)만
  채우고 재실행한다. 게이트를 완화하거나 값을 지어내서 완주를 사지 않는다.
- as_of 이후 공표물 금지. 타깃을 자기 코호트/피어에 넣지 않는다.
- 리포트 숫자는 절대 바꿔 말하지 않는다 — 전달은 `out/final_report.md` 원문,
  챗 계층은 `chat_dispatch` SHA-256 핸드오프.
- 막힘별 상세 대처표: `docs/RUNBOOK_KR_LIVE.md` §6.
