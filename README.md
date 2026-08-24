# PRISM Auto Investment Research OS

`분석시작 <기업>`을 Evidence-first 연구, 결정론적 밸류에이션, Audit, Intrinsic Freeze, Street·시장 비교로 연결하는 저장소입니다.

## 현재 구현 수준

PRISM에는 동일한 Control Plane을 사용하는 **33단계 `LIVE_PRIMARY` runtime**이 구현되어 있습니다. `run_prism()`은 Company Resolution부터 Evidence Collection, Industry DNA, Scanner/Funding, Bridge/Compiler, Method Intent, Beta/WACC, exact valuation, Audit, Freeze, Street/Market, State Save와 Final Report까지 한 경로로 조립합니다.

다만 `LIVE_PRIMARY runtime 존재`는 `모든 기업·국가·산업의 production provider 완성`을 뜻하지 않습니다. 현재 주요 제한은 다음과 같습니다.

- OpenDART 표준 재무 facts 외 기업별 주석·IR·KPI source coverage가 부분적입니다.
- 산업별 Scanner/Funding runner와 Beta/WACC/PER live provider는 route에 맞게 공급해야 합니다.
- 일부 Economic Archetype evaluator와 Driver→FCFF 모델은 아직 부분구현 또는 미구현입니다.
- production probability calibration cohort와 repository-provided Street source adapter가 없습니다.
- 실제회사 OCI·Oracle·Bloom Energy·GE Vernova LIVE acceptance fixture가 아직 완결되지 않았습니다.

지원되지 않은 provider나 evaluator는 generic fallback 없이 `NOT_IMPLEMENTED`, `RECOVERY_REQUIRED`, `AWAITING_USER_DECISION` 또는 `VALUATION BLOCKED`로 종료합니다.

## 설치와 검증

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
```

CI는 Unit Contract, Industry/Method Registry, Module Requirement Plan, LIVE_PRIMARY Readiness, Probability Calibration policy, 전체 pytest와 OCI 회귀를 검증합니다.

## LIVE_PRIMARY CLI

`분석시작 <기업>`은 LIVE_PRIMARY provider factory가 명시된 경우에만 `run_prism()`을 실행합니다.

```bash
.venv/bin/valuation-engine "분석시작 삼성전자" \
  --provider-factory my_runtime.providers:build_config \
  --state-root ../valuation-vault-local \
  --jurisdiction KR
```

또는 환경변수를 사용할 수 있습니다.

```bash
export VALUATION_LIVE_PROVIDER_FACTORY=my_runtime.providers:build_config
.venv/bin/valuation-engine "분석시작 삼성전자" \
  --state-root ../valuation-vault-local
```

Factory는 `LiveAnalysisRequest`를 받고 `LivePrimaryRuntimeConfig`를 반환해야 합니다. CLI가 정한 기업 query, run ID, jurisdiction과 state root를 factory가 임의로 변경하면 실행 전에 차단됩니다.

Provider code는 API key, 유료·비공개 자료와 private state를 public repository 밖에서 관리할 수 있습니다. 저장소에는 자격증명, 유료 증권사 원문, 개인 포지션을 커밋하지 않습니다.

상세 계약은 `src/valuation_engine/cli_runtime.py`와 `docs/LIVE_PRIMARY_CLI.md`를 기준으로 합니다.

## 명시적 OCI 회귀 모드

OCI v0.3 fixture workflow는 회귀 보존을 위해 남아 있지만 자동 fallback으로 사용되지 않습니다.

```bash
.venv/bin/valuation-engine "분석시작 OCI홀딩스" \
  --legacy-oci \
  --config examples/oci/company.yaml \
  --state-root ../valuation-vault-local
```

`--legacy-oci` 없이 provider factory가 없으면 `LIVE_PROVIDER_FACTORY_REQUIRED`로 종료합니다. 범용 분석 요청을 OCI fixture로 바꾸어 실행하지 않습니다.

기존 YAML deterministic fixture 실행도 유지됩니다.

```bash
.venv/bin/valuation-engine examples/oci/company.yaml
```

## Canonical runtime 순서

```text
COMPANY_RESOLUTION
→ LOAD_COMPANY_STATE
→ LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT
→ SOURCE_FRESHNESS_PRECHECK
→ SEGMENT_DECOMPOSITION
→ INDUSTRY_DNA_ROUTE
→ MODULE_REQUIREMENT_PLAN
→ PRIMARY_EVIDENCE_COLLECTION
→ EVIDENCE_LEDGER
→ ROCKET_INSIGHT_SCAN
→ UPSTREAM_FUNDING_SCAN
→ RESEARCHER_A
→ BLIND_RED_TEAM_B
→ RESEARCH_LOOP
→ EVIDENCE_TO_ASSUMPTION_BRIDGE
→ SCENARIO_BUILD
→ VALUATION_METHOD_INTENT
→ HIERARCHICAL_BETA_ESTIMATION
→ WACC_VALIDATION
→ DETERMINISTIC_VALUATION
→ HIERARCHICAL_WARRANTED_PER
→ DCF_PER_ASSUMPTION_CONSISTENCY_GATE
→ CROSS_METHOD_DOUBLE_COUNT_AUDIT
→ PROBABILITY_DISTRIBUTION_ANALYSIS
→ AUDIT_GATE
→ INTRINSIC_VALUE_FREEZE
→ STREET_REFERENCE_LOAD
→ STREET_GAP_ANALYZER
→ MARKET_PRICE_LOAD
→ MARKET_COMPARE
→ THESIS_DELTA
→ SAVE_STATE
→ FINAL_REPORT
```

`LIVE_PRIMARY`, `PRIMARY_SHADOW`, `LEGACY_REGRESSION`은 서로 다른 실행모드이며 key 단위로 혼합하지 않습니다. LIVE 실행은 Shadow나 Legacy로 자동 후퇴하지 않습니다.

## 구현된 핵심 계약

### Evidence와 연구 통제

- append-only Evidence Ledger, supersession, freshness와 snapshot hashing
- Segment-first multi-label Industry DNA routing
- Industry DNA → Module Requirement Plan
- Module Requirement Plan → deterministic Company Collection Plan
- Knowledge Placement, source layer와 current-price/Street isolation
- typed Researcher, Blind Red Team, bounded Recovery Loop
- Evidence → Hypothesis → Bridge → deterministic Assumption Compiler

### Valuation과 Risk

- exact `(archetype, method, version)` Evaluator Registry
- no generic DCF/NPV fallback
- normalized multiple
- explicit FCFF DCF discounting kernel
- finite-life `project_npv`, `reserve_npv`, `cohort_npv`
- calibration-certified single-event `rnpv`
- SOTP, ownership, EV→Equity와 parent adjustments
- L1→L4 Hierarchical Beta와 target relevering
- currency/structure-consistent WACC validation
- Core / Expansion / Market-Realization Warranted PER
- DCF–PER consistency와 economic-path double-count protection

### Audit, Freeze와 State

- generic Audit와 Doctrine Coverage
- Decision Impact / ablation tracking
- hash-bound Intrinsic Freeze Token
- post-freeze Street/Market comparison
- blocked result intrinsic redaction
- immutable run history와 atomic last-good state
- OCI regression preservation

## 실행 불변조건

1. 현재주가와 target-company Street 자료는 Intrinsic Freeze 전 intrinsic input이 아닙니다.
2. Street에서 발견한 새로운 사실은 primary/independent Evidence로 재검증한 새 run에서만 intrinsic에 반영합니다.
3. 모든 valuation assumption은 Evidence, Bridge와 `economic_path_id`를 가져야 합니다.
4. 정책가격은 실제 ASP·물량·원가·자금조달 전달경로 없이 가치에 직접 들어가지 않습니다.
5. Beta는 고정 가중 평균이 아니라 L1→L4 risk-driver hierarchy와 partial pooling을 사용합니다.
6. WACC는 통화, target structure, marginal debt cost와 terminal assumptions가 일관되어야 합니다.
7. Customer advances는 FCFF/ROIC에 먼저 반영하고 실제 credit improvement가 있을 때만 WACC 후보가 됩니다.
8. Core PER는 positive normalized forward EPS와 Core DCF의 성장·마진·재투자 세계관을 공유합니다.
9. 동일 경제효과를 FCF, 확률, Beta/WACC, PER premium과 SOTP option에 중복 반영하지 않습니다.
10. unsupported route, missing critical provider, unresolved Red Team 또는 Audit failure는 fair value 대신 `VALUATION BLOCKED`를 반환합니다.
11. 차단된 실행은 intrinsic value를 출력하지 않고 last-good state를 교체하지 않습니다.
12. 기존 OCI 회귀값은 의도적 모델 변경이 없는 한 ±1원 이내로 유지합니다.

## 주요 구조

```text
AGENTS.md
SKILL.md
.agents/skills/valuation-analysis/SKILL.md
01_Rocketesla_Insight_Valuation_Framework.md

docs/
  CONTROL_PLANE_ARCHITECTURE.md
  GENERIC_ENGINE_DESIGN.md
  LIVE_PRIMARY_READINESS.md
  LIVE_COMPANY_VALIDATION_AND_CALIBRATION.md
  LIVE_PRIMARY_CLI.md

config/
  control_plane_stage_registry.yaml
  live_primary_readiness.yaml
  archetype_module_registry.yaml
  valuation_method_capability_registry.yaml
  industry_source_registry.yaml
  unit_contract_registry.yaml

src/valuation_engine/
  live_runtime.py
  cli_runtime.py
  collection_plan.py
  valuation_method_intent.py
  valuation_plan_compiler.py
  evidence_adapter.py
  scanner_runtime.py
  funding_adapter.py
  risk_adapters.py
  per_adapters.py
  valuation_execution.py
  orchestrator.py
  generic_reporting.py

tests/
  test_full_live_primary_runtime.py
  test_live_cli.py
  test_oci_regression.py
  ...
```

## Readiness 해석

- `LIVE_READY`: typed live resolver/loader/runner 계약과 fail-closed 실행경로가 존재합니다.
- `PARTIAL_LIVE`: reusable live path가 있으나 source·provider·method breadth가 불완전합니다.
- `RUNTIME_READY`: typed upstream input이 공급되면 deterministic stage가 완결됩니다.
- `ADAPTER_REQUIRED`: 공통 component는 있지만 repository-provided live source adapter가 없습니다.

상태의 기준은 `config/live_primary_readiness.yaml`과 exact method capability registry입니다. 테스트 통과 또는 callback 존재만으로 모든 실제회사 coverage가 완료됐다고 간주하지 않습니다.
