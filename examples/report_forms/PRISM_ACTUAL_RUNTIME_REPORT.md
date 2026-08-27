# PRISM Verified Controlled-Run Report

- Run ID: `FULL-LIVE-1`
- Execution mode: `live_primary`
- Run status: **VERIFIED_FROZEN**
- Attestation hash: `cb0f4b8281b73bd20d6b2d341c86bfc0c22078b9b878cd26f258e8ab635ef153`

## Execution Attestation

| Check | Result | Detail |
|---|---:|---|
| `live_primary_mode` | **PASS** | the report was produced by LIVE_PRIMARY |
| `run_unblocked` | **PASS** | the controlled run has no blocking reason |
| `canonical_stage_sequence` | **PASS** | all 33 canonical stages executed in order |
| `terminal_stage_statuses` | **PASS** | every stage ended in a non-blocking terminal status |
| `intrinsic_freeze_token` | **PASS** | the same run issued an IntrinsicFreezeToken |
| `evidence_ledger_hash` | **PASS** | the frozen Evidence Ledger hash is present |
| `assumption_set_hash` | **PASS** | the compiled assumption-set hash is present |
| `scenario_set_hash` | **PASS** | the bound scenario-set hash is present |
| `valuation_hash` | **PASS** | the deterministic valuation hash is present |
| `audit_hash` | **PASS** | the generic audit passed and its hash is present |
| `persisted_final_report` | **PASS** | the final report was emitted from the persisted run payload |
| `major_gate_reporting_contract` | **PASS** | all five major gates produced compact terminal summaries |
| `major_gate_delivery` | **PASS** | major-gate summary delivery recorded no reporter failure |
| `selected_method_contract` | **PASS** | selected valuation methods are typed |
| `capacity_assessment` | **PASS** | the typed Capacity Commitment assessment and hash are present |
| `capacity_audit` | **PASS** | the Capacity omission/double-count audit passed |
| `freeze_hash_binding` | **PASS** | Freeze is bound to the same Evidence, assumptions, valuation and audit |

## Immutable Run Identities

| Artifact | Hash |
|---|---|
| Evidence Ledger | `3081f4574bf2b47592b44dae62d62555296ba1197312b70399f8b105d53fcbde` |
| Assumption set | `091053e3e982493c131b3d77d7fce59e7e8b8c9f2b9da3b8fcdb7c9a2e9f0979` |
| Scenario set | `ef87d8f464c8272a2538047127ef06645348ac5552fe97a7f73f53992f8514bc` |
| Beta | `NOT_APPLICABLE` |
| WACC | `NOT_APPLICABLE` |
| Capacity assessment | `a3545801a2b8a62a817dc8625fd5baccc104aa9ed22e1476e89b8c440ce55462` |
| Capacity consumption | `NOT_APPLICABLE` |
| Capacity scenario | `749eb5803378d1917242a7bbc628d9f735b5a3101a0593da19d5c3fa3a17ff24` |
| Capacity valuation | `26a1aaf558a3a1626e711265723d816d56e3f175919c8e9f19fd28856cfa4e58` |
| Capacity PER | `d7d0730b0c54c63b2586b3e8f355b08f1e5205b7d697202fdb91ad4ac37a5e34` |
| Capacity consistency | `dd6b3bfce8b91907a4d89c14f661e93b957449d343c920d16e7352a7805e4333` |
| Capacity audit | `c3c87aec1adf84bd72369c1be022579ecc1491a81c9e19c31ed53076a315e4d9` |
| Valuation | `9a85e0c4c2aa4258604a46f7c660105157c494da64aa63e9f3c726971e81b862` |
| Audit | `469b553cdeb7496048dddcea9c4d6638db0b54feefcccdd92a490160bc4eeae9` |
| Intrinsic Freeze | `ae680222f1feb481134d92748629befa4578e3065d9ff331f4155d0740fe5b3f` |

## Major Gate Summaries

| Gate | Status | Progress | Decisive result | Residual risk | Next |
|---|---|---:|---|---|---|
| 1. `G1_EVIDENCE_ROUTING` | `pass` | 9/9 | append-only EvidenceLedger validated and canonical runtime snapshot frozen | NONE | `G2_INSIGHT_CHALLENGE` |
| 2. `G2_INSIGHT_CHALLENGE` | `pass` | 5/5 | Blind Red Team left no unresolved blocking issue | NONE | `G3_ASSUMPTIONS_METHOD_RISK` |
| 3. `G3_ASSUMPTIONS_METHOD_RISK` | `pass` | 5/5 | selected exact economic method path does not require WACC | NONE | `G4_VALUATION_AUDIT_FREEZE` |
| 4. `G4_VALUATION_AUDIT_FREEZE` | `warning` | 7/7 | audit, decision-impact record and generated doctrine coverage authorized intrinsic freeze | PROBABILITY_DISTRIBUTION_ANALYSIS: scenario probabilities are not calibration-authorized; numeric expected value remains disabled | `G5_POST_FREEZE_PERSISTENCE` |
| 5. `G5_POST_FREEZE_PERSISTENCE` | `pass` | 7/7 | final report emitted from the same immutable payload saved in the run state | NONE | `FINAL_RESULT_REPORT` |

## Final Report Delivery Contract

- Main body editorial target: 6–8 pages
- Audit appendix editorial target: 3–4 pages
- Combined editorial cap: 12 pages
- The 33-stage trace remains in the audit appendix; routine progress output uses only the five major-gate summaries.

## Stage Trace

| # | Stage | Status | Blocking | Rationale |
|---:|---|---|---:|---|
| 1 | `COMPANY_RESOLUTION` | `pass` | NO | company identity resolved from a declared live resolver contract |
| 2 | `LOAD_COMPANY_STATE` | `pass` | NO | no prior company state; first-run empty state is valid \| loaded 0 immutable module-impact learning record(s) |
| 3 | `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT` | `pass` | NO | versioned Industry Knowledge snapshot loaded and hash-verified |
| 4 | `SOURCE_FRESHNESS_PRECHECK` | `pass` | NO | live source-watch precheck passed |
| 5 | `SEGMENT_DECOMPOSITION` | `pass` | NO | authoritative-lineage-backed segment decomposition completed |
| 6 | `INDUSTRY_DNA_ROUTE` | `pass` | NO | all decomposed segments routed to evidence-backed multi-label Industry DNA profiles |
| 7 | `MODULE_REQUIREMENT_PLAN` | `pass` | NO | compiled canonical Module Requirement Plan and non-destructive learned research loadout |
| 8 | `PRIMARY_EVIDENCE_COLLECTION` | `pass` | NO | primary evidence collected with complete required segment/metric coverage and planned source lineage |
| 9 | `EVIDENCE_LEDGER` | `pass` | NO | append-only EvidenceLedger validated and canonical runtime snapshot frozen |
| 10 | `ROCKET_INSIGHT_SCAN` | `pass` | NO | live Rocket Insight scanner dispatch completed |
| 11 | `UPSTREAM_FUNDING_SCAN` | `skipped_not_applicable` | NO | selected Industry DNA does not require a dedicated upstream funding scan |
| 12 | `RESEARCHER_A` | `pass` | NO | LLM Intelligence Officer produced typed hypotheses and an auditable environment-change/corporate-strength linkage decision without committing assumptions |
| 13 | `BLIND_RED_TEAM_B` | `pass` | NO | Blind Red Team completed with no unresolved blocker |
| 14 | `RESEARCH_LOOP` | `skipped_not_applicable` | NO | Blind Red Team left no unresolved blocking issue |
| 15 | `EVIDENCE_TO_ASSUMPTION_BRIDGE` | `pass` | NO | no capacity_manufacturing segment requires Capacity Commitment Gate \| LLM Bridge proposals validated and converted to compiler requests; no assumptions committed |
| 16 | `SCENARIO_BUILD` | `pass` | NO | no Core-inclusion capacity project requires bridge consumption \| Bridge proposals deterministically compiled and bound into generic scenarios \| no Core-inclusion capacity project requires scenario binding |
| 17 | `VALUATION_METHOD_INTENT` | `pass` | NO | economic valuation-method intent resolved before Beta/WACC; exact evaluator construction remains downstream |
| 18 | `HIERARCHICAL_BETA_ESTIMATION` | `skipped_not_applicable` | NO | selected exact economic method path does not require Hierarchical Beta |
| 19 | `WACC_VALIDATION` | `skipped_not_applicable` | NO | selected exact economic method path does not require WACC |
| 20 | `DETERMINISTIC_VALUATION` | `pass` | NO | registered deterministic evaluators and SOTP aggregation completed \| no Warranted PER cross-check requires a DCF fingerprint \| no Core-inclusion capacity project requires valuation binding |
| 21 | `HIERARCHICAL_WARRANTED_PER` | `skipped_not_applicable` | NO | selected Industry DNA does not route any segment to Warranted PER \| Warranted PER is not applicable; capacity PER double-count path is closed |
| 22 | `DCF_PER_ASSUMPTION_CONSISTENCY_GATE` | `pass` | NO | DCF-PER consistency gate is not applicable \| capacity assessment, scenario, valuation and PER identities are consistent |
| 23 | `CROSS_METHOD_DOUBLE_COUNT_AUDIT` | `pass` | NO | cross-method economic paths are unique |
| 24 | `PROBABILITY_DISTRIBUTION_ANALYSIS` | `warning` | NO | scenario probabilities are not calibration-authorized; numeric expected value remains disabled |
| 25 | `AUDIT_GATE` | `pass` | NO | pre-freeze Broker Research is not configured for this run \| capacity omission, baseline and double-count audit passed \| decision-impact record and run-bound generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present |
| 26 | `INTRINSIC_VALUE_FREEZE` | `pass` | NO | audit, decision-impact record and generated doctrine coverage authorized intrinsic freeze |
| 27 | `STREET_REFERENCE_LOAD` | `pass` | NO | target-company Street references loaded after a valid same-run Freeze Token |
| 28 | `STREET_GAP_ANALYZER` | `pass` | NO | Street gap preserved as scenario envelope because probability weighting is not calibrated |
| 29 | `MARKET_PRICE_LOAD` | `pass` | NO | target-company market price loaded only after intrinsic freeze |
| 30 | `MARKET_COMPARE` | `pass` | NO | current price compared with each intrinsic scenario; no Expected Value fabricated |
| 31 | `THESIS_DELTA` | `pass` | NO | current thesis compared with the prior immutable successful state |
| 32 | `SAVE_STATE` | `pass` | NO | immutable learning/run artifacts saved and audit-passed current state promoted |
| 33 | `FINAL_REPORT` | `pass` | NO | final report emitted from the same immutable payload saved in the run state |

## Persisted Research Report

# Frozen Commodity Co PRISM Research & Valuation Report

## LLM Insight Layer — Environment × Corporate Strength
- Boundary: 이 영역은 외부 환경 변화와 기업의 기존 강점 사이의 비자명한 연결을 발견·반증하는 사고 계층이며, 밸류에이션 공식을 직접 변경하지 않습니다.
- Status: NOT_APPLICABLE
- Reason: This frozen acceptance fixture validates deterministic runtime integrity and does not assert an external-change investment thesis.

## Intrinsic Value
- Base intrinsic: 70,000 KRW/share
- Expected Value: 미산출 — 시나리오 확률이 CALIBRATED 상태가 아니므로 숫자 가중을 보류했습니다.

## Probability Calibration
- Status: UNCALIBRATED
- Numeric weighting: WITHHELD
- Dataset hash: NOT_AVAILABLE
- Snapshot hash: NOT_AVAILABLE

## Street Gap
- 리포트 수: 2
- 평균 목표가: 70,000 KRW
- Base 대비: 0 (+0.0%)

## Current Market Compare
- 현재가: 65,000 KRW (2026-08-23)
- Base 기대수익 간격: 5,000 (+7.7%)

## Module Impact / Research Efficiency
- 측정 완료: DETERMINISTIC_VALUATION
- 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE, KNOWLEDGE_PLACEMENT_GATE 외 7개
- 비적용: HIERARCHICAL_BETA_ENGINE, UPSTREAM_FUNDING_SCAN, WACC_ENGINE
- 실패: 없음
- 조사비용: source queries 0, documents 0, LLM calls 0, elapsed 0.0s
- 하향 검토 후보: 없음
- 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## Audit & Coverage
- Audit: PASS (22 checks)
- Doctrine coverage: 27/27 terminally acceptable

## Thesis Delta
- 강화·신규: frozen primary evidence supports one unweighted Base scenario
- 약화·폐기: 없음

## Run Integrity
- Valuation scope: FULL_INTRINSIC
- Ledger snapshot: 3081f4574bf2b47592b44dae62d62555296ba1197312b70399f8b105d53fcbde
- Assumption set: 091053e3e982493c131b3d77d7fce59e7e8b8c9f2b9da3b8fcdb7c9a2e9f0979
- Valuation: 9a85e0c4c2aa4258604a46f7c660105157c494da64aa63e9f3c726971e81b862
- Audit: 469b553cdeb7496048dddcea9c4d6638db0b54feefcccdd92a490160bc4eeae9
- Freeze token: ae680222f1feb481134d92748629befa4578e3065d9ff331f4155d0740fe5b3f
- Calibration dataset: NOT_APPLIED
- Calibration snapshot: NOT_APPLIED
