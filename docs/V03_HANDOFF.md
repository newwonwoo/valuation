# v0.3 Codex Handoff

## 구현된 수직 슬라이스

`분석시작 OCI홀딩스`는 이전 state를 읽고, OCI v1.1 fixture를 typed Evidence/Bridge로 이관한 뒤 Researcher와 Blind Red Team 계약을 거쳐 deterministic valuation과 Audit을 수행합니다. Audit PASS 후에만 market loader를 호출하며, 성공 Run만 current state로 승격합니다.

## 실제 코드와 문서의 경계

- **실제 구현:** 타입 검증, 추적성, 상태 저장, 제한 Research Loop, market-last gate, deterministic OCI math, 차단 보고서.
- **Fixture 구현:** Evidence 수집, Rocket Insight, Researcher A, Red Team B. 기본 함수는 orchestration 검증용이며 live research가 아닙니다.
- **미구현:** DART/SEC/IR adapter의 EvidenceRecord 출력, 실제 LLM/tool 호출 adapter, source conflict resolver, generic SOTP aggregator, Position Engine.

## 다음 Codex 작업 순서

1. `data/adapters`가 raw source와 source hash를 반환하도록 구현한다.
2. 수집 결과를 `EvidenceRecord`로 정규화하고 source hierarchy 충돌을 보존한다.
3. Researcher/Red Team callable을 실제 Codex subagent/tool workflow에 연결한다. Red Team에는 market capability를 주지 않는다.
4. `ValueContribution`을 OCI segment output까지 연결해 `other_business_pv`의 불투명성을 제거한다.
5. SOTP에서 subsidiary debt → equity → ownership → parent debt/NCI 순서를 fixture로 검증한다.
6. Oracle, Bloom Energy, GE Vernova fixture를 추가하되 각 산업 공식 model contract부터 작성한다.

## 반드시 유지할 테스트

- OCI 4개 scenario 및 확률가중 가치 회귀
- current-price isolation
- policy-only-to-ASP rejection
- Bridge completeness
- unit conversion
- economic-path 및 CAPEX double count
- three-round block
- audit failure valuation suppression
- market loader call order
- blocked run non-promotion
- root/repo-scoped Skill 동등성

## 상태 저장

기본 구조:

```text
<private-state-root>/
  state/<ticker>/current_state.json
  runs/<ticker>/<run_id>/
    manifest.json
    evidence_delta.json
    researcher.md
    redteam.md
    bridge.json
    assumptions.json
    valuation.json
    audit.json
    market_compare.json
    thesis_delta.json
    final_report.md
```

`VALUATION_BLOCKED` run은 `valuation.json`을 `suppressed: true`, `market_compare.json`을 `not_loaded: true`로 저장합니다. current state를 갱신하지 않습니다.

## 알려진 리스크

- OCI fixture의 `other_business_pv_trn_krw`는 아직 구성요소가 없는 legacy add-on입니다.
- OCI wafer 경제적 지분은 legacy 회귀를 위해 EBITDA 단계에서 반영됩니다. 범용 SOTP에서는 subsidiary net debt와 NCI 검증 후 equity 단계 ownership을 적용해야 합니다.
- Legacy Excel input은 회귀 자료이며 primary evidence가 아닙니다. `LEGACY_REGRESSION` 등급과 낮은 confidence를 유지합니다.
- 확률은 `UNCALIBRATED`입니다. 과거 예측과 실제 발생률이 축적되기 전에는 정밀 확률로 표현하지 않습니다.
