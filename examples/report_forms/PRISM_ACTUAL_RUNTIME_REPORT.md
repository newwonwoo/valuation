# PRISM Verified Controlled-Run Report

- Run ID: `FULL-LIVE-1`
- Execution mode: `live_primary`
- Run status: **VERIFIED_FROZEN**
- Attestation hash: `24dd96b0a7510eef734e54effec838ceb7186034aed647e1417dd364c519a189`

## Verification
- Checks: **18/18 PASS**
- Canonical stages: **33/33 terminal traces**

## Frozen Identity Chain
- Evidence: `844702b379f405baffd8cea944854ac2c00a1b0e8141a693bfd75fd8934a786d`
- Assumptions: `f9a111745f4945d119f02f1708f026ff7473c9c96a6055c454370634d2a0e818`
- Scenarios: `363189a1674c763b0f3d2e60be59156f25e956d800342bf9f468dbf093c4538c`
- Valuation: `759890294b90fb9bda449cc6b539214a0795bb59ad27d1f46e37b42b8f99da06`
- Audit: `484915ff80ef965128618a753168b38ae268ebcc4f4656bfb8a9e84270a15d5a`
- Intrinsic Freeze: `77990c6f5d8c2fd9b152a537b6ecf4cf6e5140640e00c9f817acad2bf0105ed1`
- Auxiliary bindings: Capacity assessment `a3545801a2b8a62a817dc8625fd5baccc104aa9ed22e1476e89b8c440ce55462` · Capacity scenario `749eb5803378d1917242a7bbc628d9f735b5a3101a0593da19d5c3fa3a17ff24` · Capacity valuation `5e36a496bd37604aa33ffb0b4f80cd48eadf839b83218aaa9422a35649e297fe` · Capacity PER `09d1f570a1c55c08e4639a4c59546ddac49c2704ed6ebdf185cba5cd4457d478` · Capacity consistency `adfc3920a842875012b27720a55cf7324ede5d2ae4abf320d1c4484f3aafb1eb` · Capacity audit `5405620256db2ab82529b83171ef2e5f41bc1d1fd8d1785902318c52f5b0c353`

## Major Gate Summaries

### 1. Evidence and Routing — PASS (9/9)
- Result: append-only EvidenceLedger validated and canonical runtime snapshot frozen
- Risk: NONE · Next: `G2_INSIGHT_CHALLENGE`

### 2. Insight and Challenge — PASS (5/5)
- Result: Blind Red Team left no unresolved blocking issue
- Risk: NONE · Next: `G3_ASSUMPTIONS_METHOD_RISK`

### 3. Assumptions, Method and Risk — PASS (5/5)
- Result: selected exact economic method path does not require WACC
- Risk: NONE · Next: `G4_VALUATION_AUDIT_FREEZE`

### 4. Valuation, Audit and Freeze — WARNING (7/7)
- Result: audit, decision-impact record and generated doctrine coverage authorized intrinsic freeze
- Risk: PROBABILITY_DISTRIBUTION_ANALYSIS: scenario probabilities are not calibration-authorized; numeric expected value remains disabled · Next: `G5_POST_FREEZE_PERSISTENCE`

### 5. Post-Freeze Comparison and Persistence — PASS (7/7)
- Result: final report emitted from the same immutable payload saved in the run state
- Risk: NONE · Next: `FINAL_RESULT_REPORT`

## Final Report Delivery Contract
- Main body editorial target: 3–4 pages
- Audit appendix editorial target: 1–2 pages
- Combined editorial cap: 6 pages
- Typography: body ≥ 13pt, primary heading ≥ 22pt, section heading ≥ 18pt; dense wide tables forbidden.
- Mandatory: every claim source is mapped to a direct HTTP(S) original link in `Sources — Direct Verification`.

## Compact Audit Appendix — 33-Stage Trace
- **G1_EVIDENCE_ROUTING:** 1 `COMPANY_RESOLUTION`=pass · 2 `LOAD_COMPANY_STATE`=pass · 3 `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT`=pass · 4 `SOURCE_FRESHNESS_PRECHECK`=pass · 5 `SEGMENT_DECOMPOSITION`=pass · 6 `INDUSTRY_DNA_ROUTE`=pass · 7 `MODULE_REQUIREMENT_PLAN`=pass · 8 `PRIMARY_EVIDENCE_COLLECTION`=pass · 9 `EVIDENCE_LEDGER`=pass
- **G2_INSIGHT_CHALLENGE:** 10 `ROCKET_INSIGHT_SCAN`=pass · 11 `UPSTREAM_FUNDING_SCAN`=skipped_not_applicable · 12 `RESEARCHER_A`=pass · 13 `BLIND_RED_TEAM_B`=pass · 14 `RESEARCH_LOOP`=skipped_not_applicable
- **G3_ASSUMPTIONS_METHOD_RISK:** 15 `EVIDENCE_TO_ASSUMPTION_BRIDGE`=pass · 16 `SCENARIO_BUILD`=pass · 17 `VALUATION_METHOD_INTENT`=pass · 18 `HIERARCHICAL_BETA_ESTIMATION`=skipped_not_applicable · 19 `WACC_VALIDATION`=skipped_not_applicable
- **G4_VALUATION_AUDIT_FREEZE:** 20 `DETERMINISTIC_VALUATION`=pass · 21 `HIERARCHICAL_WARRANTED_PER`=skipped_not_applicable · 22 `DCF_PER_ASSUMPTION_CONSISTENCY_GATE`=pass · 23 `CROSS_METHOD_DOUBLE_COUNT_AUDIT`=pass · 24 `PROBABILITY_DISTRIBUTION_ANALYSIS`=warning · 25 `AUDIT_GATE`=pass · 26 `INTRINSIC_VALUE_FREEZE`=pass
- **G5_POST_FREEZE_PERSISTENCE:** 27 `STREET_REFERENCE_LOAD`=pass · 28 `STREET_GAP_ANALYZER`=pass · 29 `MARKET_PRICE_LOAD`=pass · 30 `MARKET_COMPARE`=pass · 31 `THESIS_DELTA`=pass · 32 `SAVE_STATE`=pass · 33 `FINAL_REPORT`=pass
- Exact rationales and output keys remain in the immutable `control_plane_trace.json` artifact.

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
- Status: UNCALIBRATED · Numeric weighting: WITHHELD
- Lineage: dataset `NOT_AVAILABLE` · snapshot `NOT_AVAILABLE`

## Street Gap
- 리포트 수: 2
- 평균 목표가: 70,000 KRW
- Base 대비: 0 (+0.0%)

## Current Market Compare
- 현재가: 65,000 KRW (2026-08-23)
- Base 기대수익 간격: 5,000 (+7.7%)

## Sources — Direct Verification
- **Company identity / Current market price / Street: BrokerA / Street: BrokerB / frozen filing fixture** — Evidence 8개: benchmark_price, capacity, cash_cost, cost_curve_position, inventory, production 외 2개 (effective 2026-06-30); company resolution; market price as of 2026-08-23; target price published 2026-08-01; target price published 2026-08-05 [원문 바로 열기](https://github.com/newwonwoo/valuation/blob/main/tests/test_full_live_primary_runtime.py)
- 전체 Evidence ID·지표·기준일 매핑은 동일 run의 immutable Evidence Ledger에 보존됩니다.

## Module Impact / Research Efficiency
- 측정 완료: DETERMINISTIC_VALUATION · 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE, KNOWLEDGE_PLACEMENT_GATE 외 7개
- 비적용: HIERARCHICAL_BETA_ENGINE, UPSTREAM_FUNDING_SCAN, WACC_ENGINE · 실패: 없음
- 조사비용: source queries 0, documents 0, LLM calls 0, elapsed 0.0s
- 하향 검토 후보: 없음 · 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## Audit & Coverage
- Audit: PASS (22 checks)
- Doctrine coverage: 27/27 terminally acceptable

## Thesis Delta
- 강화·신규: frozen primary evidence supports one unweighted Base scenario
- 약화·폐기: 없음

## Run Integrity
- Scope: FULL_INTRINSIC · Freeze: `77990c6f5d8c2fd9b152a537b6ecf4cf6e5140640e00c9f817acad2bf0105ed1`
- Chain: ledger `844702b379f405baffd8cea944854ac2c00a1b0e8141a693bfd75fd8934a786d` · assumptions `f9a111745f4945d119f02f1708f026ff7473c9c96a6055c454370634d2a0e818` · valuation `759890294b90fb9bda449cc6b539214a0795bb59ad27d1f46e37b42b8f99da06` · audit `484915ff80ef965128618a753168b38ae268ebcc4f4656bfb8a9e84270a15d5a`
- Calibration: dataset `NOT_APPLIED` · snapshot `NOT_APPLIED`
