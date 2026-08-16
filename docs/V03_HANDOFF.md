# v0.3 Codex Handoff

이 문서는 [GitHub Issue #1](https://github.com/newwonwoo/valuation/issues/1)을 실제 PR 순서로 바꾼 다음 Codex 작업의 시작점이다. 현재 `main`의 실제 코드와 새로 확정한 설계를 구분한다.

## 1. 현재 상태

| 영역 | 설계 | 현재 `main` 구현 |
|---|---|---|
| Market-last workflow | 완료 | 완료 |
| Basic Evidence provenance | 완료 | OCI legacy fixture 범위 구현 |
| Source conflict preservation | 완료 | 미구현 |
| Agent isolation | 완료 | contract/fixture 구현, live adapter 미구현 |
| Routing contract | 완료 | ModelSpec/segment validation 구현 |
| Audit primitives | 완료 | 핵심 일부 구현, evaluator별 audit 미구현 |
| Synthetic contract tests | 완료 | 구현 |
| OCI legacy formula/regression | 완료 | 구현 |
| Actual-unit 범용 모델 | 완료 | 미구현 |
| 산업별 deterministic evaluator | 완료 | 미구현 |
| Evaluator registry | 완료 | 미구현 |
| Evidence→Assumption compiler | 완료 | 미구현 |
| Company collection plan/adapters | 완료 | 미구현 |
| Generic holding/operating aggregator | 완료 | 미구현 |
| OCI primary-evidence replacement | 완료 | 미구현 |
| Oracle/GEV/Bloom live validation | 완료 | 미구현 |
| Probability calibration | 완료 | 원장·metrics·promotion gate 미구현 |

상세 계약:

- [Generic Valuation Engine Design](GENERIC_ENGINE_DESIGN.md)
- [Live Validation and Probability Calibration](LIVE_VALIDATION_AND_CALIBRATION.md)

## 2. 현재 구현된 수직 슬라이스

`분석시작 OCI홀딩스`는 이전 state를 읽고 OCI v1.1 fixture를 typed Evidence/Bridge로 감싼 뒤 Researcher와 Blind Red Team contract를 거쳐 기존 deterministic valuation과 Audit을 수행한다. Audit PASS 후에만 market loader를 호출하며 성공 Run만 current state로 승격한다.

구현 경계:

- 실제 구현: 기본 타입 검증, 추적성, 상태 저장, 최대 3회 loop, market-last gate, OCI math, blocked report.
- Fixture 구현: Evidence collection, Rocket Insight, Researcher A, Red Team B.
- 구현 아님: primary-source compiler, live research, 범용 evaluator, generic SOTP, probability calibration.

`build_oci_legacy_trace()`는 migration wrapper다. Bridge의 `new_value`를 재계산하지 않으므로 Evidence→Assumption compiler로 간주하면 안 된다.

## 3. 다음 최소 milestone

첫 PR은 **OCI polysilicon Base-case primary shadow slice**만 구현한다.

```text
frozen primary-source fixture
→ ConflictGroup을 보존하는 Ledger snapshot
→ registered transform 기반 Compiler
→ actual-unit commodity input
→ exact Evaluator Registry
→ throughput evaluator
→ legacy Base-case shadow reconciliation
```

포함:

- `Decimal` actual-unit kernel과 traced FX
- `EvidenceRole`, `SemanticKey`, `ConflictGroup`
- `AssumptionSpec`, deterministic transforms, `CompilationBlocked`
- commodity evaluator와 exact registry
- shadow-only result와 reconciliation artifact

제외:

- live DART/SEC network adapter
- 전체 OCI SOTP와 `other_business_pv` 승격
- 현재주가 조회 및 목표가 출력
- Oracle/Bloom/GEV 구현
- agent framework나 database

예상 파일 경계:

```text
src/valuation_engine/
  records.py                 # canonical Evidence/Bridge/compiled types
  units.py                   # Decimal Measure + UnitDef/FX conversion
  conflicts.py               # SemanticKey + ConflictGroup resolver
  transforms.py              # allowlisted deterministic transforms
  compiler.py                # fail-closed compilation
  evaluators/base.py         # protocol, ModelKey, typed errors
  evaluators/registry.py     # exact resolve, no fallback
  evaluators/commodity.py    # throughput_exit_multiple v1
examples/oci/primary_shadow/ # frozen public fixtures only
tests/
  test_conflicts.py
  test_compiler.py
  test_evaluator_registry.py
  test_commodity_evaluator.py
  test_oci_primary_shadow.py
```

기존 `models.py::Scenario`와 `engine.py`는 삭제하지 않고 compatibility regression path에 둔다.

Merge gate:

1. Critical Evidence 하나를 제거하면 compile이 차단된다.
2. Policy Evidence만 바꿔도 ASP와 value가 변하지 않는다.
3. Bridge proposal 숫자를 transform이 재계산하고 mismatch를 차단한다.
4. 동일 snapshot은 byte-stable hash와 같은 값을 낸다.
5. `kMT→kg`, `USD/kg×kg×KRW/USD` identity가 맞는다.
6. Unsupported evaluator는 fallback 없이 차단된다.
7. Shadow run은 target price를 읽거나 fair value로 표시하지 않는다.
8. 기존 OCI 4-scenario regression은 그대로 통과한다.

## 4. 후속 build order

1. M1 primary shadow slice.
2. Full OCI commodity shadow와 scenario integrity.
3. Holding SOTP: segment EV → segment debt → equity → ownership → parent debt.
4. OCI `other_business_pv`를 asset별 contribution으로 분해하고 `LIVE_PRIMARY` gate 추가.
5. CollectionPlan/SourceAdapter/Extractor와 frozen DART/IR fixture.
6. Live DART/SEC/IR adapter.
7. Oracle software FCF evaluator.
8. Operating-company aggregator와 GE Vernova.
9. Bloom order/energy/JV/financing hybrid.
10. Forecast/outcome ledger와 binary Brier calibration.

Bloom은 가장 마지막이다. 제품·서비스·전력·JV·financing이 섞여 있어 accounting/double-count 위험이 가장 크다.

## 5. 최고위험 요구사항

- `models.py`와 `records.py`의 중복 Evidence/Assumption schema를 새 경로에서 하나로 통합한다.
- Source layer와 Evidence role을 분리한다. 대상 현재가만 pre-audit에서 차단하고 FX/peer/상장지분 reference는 별도 Bridge로 관리한다.
- Plan, realized, policy, estimate를 같은 metric 값으로 평균하지 않는다.
- `LEGACY_REGRESSION → PRIMARY_SHADOW → LIVE_PRIMARY` 승격은 key가 아니라 run 단위다.
- OCI wafer ownership을 legacy EBITDA 단계에서 적용하는 방식은 live generic SOTP로 복사하지 않는다.
- Opaque `other_business_pv`가 남아 있으면 `LIVE_PRIMARY`를 차단한다.
- GE Vernova는 holding company가 아니라 operating multi-segment company로 집계한다.
- Bloom framework/MOU/financing ceiling은 firm backlog나 Core Value가 아니다.
- Probability calibration은 forecast class·horizon별로만 승격하고 현재가/수익률을 feature나 outcome으로 쓰지 않는다.

## 6. 반드시 유지할 기존 테스트

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

## 7. 상태 저장

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

`VALUATION_BLOCKED` run은 `valuation.json`을 `suppressed: true`, `market_compare.json`을 `not_loaded: true`로 저장한다. current state를 갱신하지 않는다.

## 8. Do not do

- 기존 OCI engine을 먼저 삭제하거나 generic engine으로 이름만 바꾸지 않는다.
- Bridge `new_value`를 compiler output으로 복사하지 않는다.
- Unknown industry를 generic DCF로 fallback하지 않는다.
- 실시간 adapter부터 만들지 않는다. Frozen fixture로 compiler/evaluator contract를 먼저 고정한다.
- Primary Evidence 부족을 legacy 값으로 자동 fallback하지 않는다.
- Shadow 결과를 목표가 또는 최신 valuation으로 표시하지 않는다.
