# 산일전기(062040) PRISM LIVE_PRIMARY 보고서

- 데이터 기준일: **2026-08-25**
- 검증 상태: **VERIFIED_FROZEN**
- 투자검토 상태: **Preliminary source-backed underwrite**
- 현재가(Freeze 후 로드): **169,300원**
- Street 참고 목표가(Freeze 후 로드): **310,000원**
- Down / Core / Bull: **83,265원 / 106,758원 / 126,091원**
- Hierarchical Beta: **1.297**
- WACC: **9.852%**
- Core 반영 Capacity 프로젝트: **SANIL_SECOND_FACTORY_RAMP**

## PM 결론

산일전기는 수요 검증 단계를 넘어 생산능력과 ramp가 가치의 핵심 병목이 된 회사입니다. 이번 run은 부지 통제·확정 CAPEX·ramp Evidence를 Core에서 누락하지 않고, 동일 프로젝트의 Capacity·CAPEX·ramp 경로를 Scenario와 DCF가 실제 소비한 뒤 Beta·WACC, Audit, Freeze를 통과했습니다.

현재가는 확률가중 기대값이 아니라 개별 Down/Core/Bull 세계관과 비교해야 합니다. 역사적 calibration cohort가 아직 충분하지 않아 Expected Value는 의도적으로 산출하지 않았습니다. 이 보고서의 FCFF 경로는 회사 가이던스가 아니라 2025 사업보고서와 2026년 2분기 IR을 기반으로 한 **PRISM analyst underwrite**입니다.

## Evidence Confidence / Underwriting Status

- 회사 실적·수주·Capacity·부지·CAPEX: 회사 공시·IR 기반, **높은 증거 신뢰도**
- Beta peer 관측: 실제 상장회사와 공개 `Beta (5Y)` 자료 기반, **중간 증거 신뢰도**
- Beta 공급자는 benchmark·빈도·표준오차를 공개하지 않아 `beta_standard_error`를 임의 생성하지 않았습니다.
- WACC 거시입력과 country-risk lambda: 출처가 명시된 외부 시장자료 및 PRISM 판단값, **중간 신뢰도**
- Down/Core/Bull FCFF: 공시 사실에서 파생한 분석가 가정이며 회사 가이던스가 아닙니다.
- 공식 KRX 수익률 회귀 provider가 가용해지면 현재 외부 Beta 스냅샷을 교체하는 것이 다음 품질개선 항목입니다.

## Source Register

- 2025 사업보고서: https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm
- 2026년 2분기 IR: https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002&mode=view&page=1&s_cate=&s_keyword=&s_type=
- 실제 peer Beta·WACC 원장: https://github.com/newwonwoo/valuation/blob/main/docs/SANIL_RISK_SOURCE_REGISTER.md
- PRISM underwriting assumptions: https://github.com/newwonwoo/valuation/blob/main/config/sanil_live_snapshot.yaml#scenarios
- Street 참고자료: https://www.yna.co.kr/amp/view/AKR20260811028700008
- 현재가: https://data.krx.co.kr/

---

# PRISM Verified Controlled-Run Report

- Run ID: `SANIL-062040-20260825`
- Execution mode: `live_primary`
- Run status: **VERIFIED_FROZEN**
- Attestation hash: `6b4047c5f21323baa3967a2471e7ecaf6cdd2229aab6261f7c56c61f5f244871`

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
| `selected_method_contract` | **PASS** | selected valuation methods are typed |
| `beta_wacc_same_run_chain` | **PASS** | Beta and WACC snapshots are executed and bound to one risk chain |
| `capacity_assessment` | **PASS** | the typed Capacity Commitment assessment and hash are present |
| `capacity_audit` | **PASS** | the Capacity omission/double-count audit passed |
| `capacity_core_consumption_chain` | **PASS** | Core Capacity, CAPEX and ramp paths are bound through valuation |
| `freeze_hash_binding` | **PASS** | Freeze is bound to the same Evidence, assumptions, valuation and audit |

## Immutable Run Identities

| Artifact | Hash |
|---|---|
| Evidence Ledger | `3dc77e6b69167052a84e618030d3f525c7cb37ed469d47916e6bce686d8a7ccd` |
| Assumption set | `2eadbb684885cbddc817fa1a0a090e0bfeadcc055c89eee39701337b67233f65` |
| Scenario set | `b1386b2822c9a5c962f18f5a289664b4930ebfebb1b8145a2f51160587a10d9b` |
| Beta | `381bde9f06e4696a7312b9101b27480ccc241a5afa9ec381eb765dd64b1b08e4` |
| WACC | `e3cde4b2f7b99b1ad5af3350d8e395bfa2828439558604c1b6283c7a0646d958` |
| Capacity assessment | `c12b740b58356e43f623e446c44e0b1702f4b77deb353b5b4fa5d7c45c77553d` |
| Capacity consumption | `da9cf6c223a74002008ab9cc71965841b9b613f06ff036ad9998be6b3b995e83` |
| Capacity scenario | `d510f0f592e32fbda77b02ac638461f7387af814cdb173e35536d1afcff4a5ae` |
| Capacity valuation | `60102044ac71025db38332ec33987e1ee05ec47db72284c8e9c846bf74143768` |
| Capacity PER | `0bea0d594d764ed67e1176ddbb1e4a91b8f15ffbb32064adcf4de873535317b5` |
| Capacity consistency | `c2dd9c39ebaf090884b6fa21ecc3aaa9ca3776548d0e796e9616d04250d5af80` |
| Capacity audit | `7f7a8ee44063b3eba3105951f65f1df8132a60847ab837275e1fa8927d3f5a32` |
| Valuation | `a417aae2e779c930ef2a8473ac2f662818fcf98766a5e8383f06e5718b4e9ce6` |
| Audit | `77cd47509df8a7aeda2516db44b95a81cf7662d9fb87a6cfa0a0d7514f6af1f5` |
| Intrinsic Freeze | `53606140ec9e89e14e953aab54c1f2b8eabfb4f4e3715f1252970d9deee5a221` |

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
| 10 | `ROCKET_INSIGHT_SCAN` | `warning` | NO | live Rocket Insight scanner dispatch completed with warnings |
| 11 | `UPSTREAM_FUNDING_SCAN` | `pass` | NO | live upstream funding scan completed; result is evidence/hypothesis input only and does not directly change WACC |
| 12 | `RESEARCHER_A` | `pass` | NO | LLM Intelligence Officer produced typed hypotheses and an auditable environment-change/corporate-strength linkage decision without committing assumptions |
| 13 | `BLIND_RED_TEAM_B` | `pass` | NO | Blind Red Team completed with no unresolved blocker |
| 14 | `RESEARCH_LOOP` | `skipped_not_applicable` | NO | Blind Red Team left no unresolved blocking issue |
| 15 | `EVIDENCE_TO_ASSUMPTION_BRIDGE` | `pass` | NO | canonical project gates were classified and Core capacity obligations frozen \| LLM Bridge proposals validated and converted to compiler requests; no assumptions committed |
| 16 | `SCENARIO_BUILD` | `pass` | NO | every Core-inclusion capacity project consumed explicit capacity, CAPEX and ramp bridge paths \| Bridge proposals deterministically compiled and bound into generic scenarios \| every required capacity, CAPEX and ramp path compiled into the Core scenario |
| 17 | `VALUATION_METHOD_INTENT` | `pass` | NO | economic valuation-method intent resolved before Beta/WACC; exact evaluator construction remains downstream |
| 18 | `HIERARCHICAL_BETA_ESTIMATION` | `pass` | NO | live L1→L4 Economic-Twin Beta estimated and relevered with one target structure |
| 19 | `WACC_VALIDATION` | `pass` | NO | currency-consistent WACC computed from live Beta and independent marginal financing inputs |
| 20 | `DETERMINISTIC_VALUATION` | `pass` | NO | registered deterministic evaluators and SOTP aggregation completed \| driver-specific DCF economic fingerprint bound for cross-method consistency \| deterministic valuation consumed every Core capacity economic path |
| 21 | `HIERARCHICAL_WARRANTED_PER` | `skipped_not_applicable` | NO | No authorized same-as-of Economic-Twin residual PER pack is included; PER is withheld rather than approximated. \| Warranted PER is not applicable; capacity PER double-count path is closed |
| 22 | `DCF_PER_ASSUMPTION_CONSISTENCY_GATE` | `pass` | NO | DCF-PER consistency gate is not applicable \| capacity assessment, scenario, valuation and PER identities are consistent |
| 23 | `CROSS_METHOD_DOUBLE_COUNT_AUDIT` | `pass` | NO | cross-method economic paths are unique |
| 24 | `PROBABILITY_DISTRIBUTION_ANALYSIS` | `warning` | NO | scenario probabilities are not calibration-authorized; numeric expected value remains disabled |
| 25 | `AUDIT_GATE` | `pass` | NO | capacity omission, baseline and double-count audit passed \| decision-impact record and run-bound generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present |
| 26 | `INTRINSIC_VALUE_FREEZE` | `pass` | NO | audit, decision-impact record and generated doctrine coverage authorized intrinsic freeze |
| 27 | `STREET_REFERENCE_LOAD` | `pass` | NO | target-company Street references loaded after a valid same-run Freeze Token |
| 28 | `STREET_GAP_ANALYZER` | `pass` | NO | Street gap preserved as scenario envelope because probability weighting is not calibrated |
| 29 | `MARKET_PRICE_LOAD` | `pass` | NO | target-company market price loaded only after intrinsic freeze |
| 30 | `MARKET_COMPARE` | `pass` | NO | current price compared with each intrinsic scenario; no Expected Value fabricated |
| 31 | `THESIS_DELTA` | `pass` | NO | current thesis compared with the prior immutable successful state |
| 32 | `SAVE_STATE` | `pass` | NO | immutable learning/run artifacts saved and audit-passed current state promoted |
| 33 | `FINAL_REPORT` | `pass` | NO | final report emitted from the same immutable payload saved in the run state |

## Persisted Research Report

# 산일전기 주식회사 PRISM Research & Valuation Report

## LLM Insight Layer — Environment × Corporate Strength
- Boundary: 이 영역은 외부 환경 변화와 기업의 기존 강점 사이의 비자명한 연결을 발견·반증하는 사고 계층이며, 밸류에이션 공식을 직접 변경하지 않습니다.
- Status: APPLICABLE

### CSL:SANIL:POWER_BOTTLENECK_CAPACITY
- 외부 환경 변화: Grid replacement, renewable interconnection and data-center power demand are increasing the scarcity of qualified transformer delivery slots.
- 새 병목·전략적 필요: Buyers need proven manufacturers with customer qualification, backlog visibility and physically controllable expansion capacity.
- 기업의 기존 강점: Sanil already has export customer access, a high-value specialty-transformer mix, an 88.9% utilized production base, reported backlog and a controlled second-factory site with committed CAPEX.
- 비자명한 연결: The external power-equipment bottleneck specifically revalues Sanil's existing customer relationships and pre-invested site because those assets can convert scarce delivery slots into backlog conversion and FCFF.
- 시장의 인식 공백: A generic small-transformer framing can separate current earnings from the option value of land-controlled capacity and overlook that the site, customer access and production know-how already exist.
- 가치 포착 경로: land control and committed CAPEX → equipment/ramp execution → effective capacity → backlog conversion → revenue, margin and free cash flow
- 인과 경로: power-infrastructure demand and transformer-slot scarcity rise → qualified delivery capacity becomes the binding buyer constraint → Sanil's existing customer access, operating base and controlled site absorb the need → capacity, CAPEX and ramp are consumed together in the Core scenario → incremental shipments convert backlog into revenue and FCFF
- 시장 인식 트리거: official second-factory equipment or production ramp disclosure; effective-capacity growth with backlog conversion; high-value product mix and margin retention after ramp
- 반증·철회 조건: the company cancels the program or confirms it is fully included in the frozen baseline; backlog or orders decline before capacity converts to shipments; ramp costs and margin normalization offset the added production ceiling
- 다음 검증: next quarterly filing for factory ramp, CAPEX and utilization; orders-to-revenue conversion and customer concentration; cash conversion after expansion spending
- Supporting Evidence: E:SANIL:orders, E:SANIL:backlog, E:SANIL:utilization, E:SANIL:expansion_land_control, E:SANIL:expansion_site_area, E:SANIL:expansion_capex_committed
- Contradicting Evidence: 없음
- LLM confidence: 78%

## Intrinsic Value
- Down intrinsic: 83,264.96 KRW/share
- Core intrinsic: 106,758.26 KRW/share
- Bull intrinsic: 126,090.78 KRW/share
- Expected Value: 미산출 — 시나리오 확률이 CALIBRATED 상태가 아니므로 숫자 가중을 보류했습니다.

## Street Gap
- 리포트 수: 1
- 평균 목표가: 310,000 KRW
- Down 대비: -226,735.04 (-73.1%)
- Core 대비: -203,241.74 (-65.6%)
- Bull 대비: -183,909.22 (-59.3%)

## Current Market Compare
- 현재가: 169,300 KRW (2026-08-25)
- Down 기대수익 간격: -86,035.04 (-50.8%)
- Core 기대수익 간격: -62,541.74 (-36.9%)
- Bull 기대수익 간격: -43,209.22 (-25.5%)

## Module Impact / Research Efficiency
- 측정 완료: DETERMINISTIC_VALUATION
- 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, HIERARCHICAL_BETA_ENGINE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE 외 10개
- 비적용: 없음
- 실패: 없음
- 조사비용: source queries 0, documents 0, LLM calls 0, elapsed 0.0s
- 하향 검토 후보: 없음
- 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## Audit & Coverage
- Audit: PASS (22 checks)
- Doctrine coverage: 27/27 terminally acceptable

## Thesis Delta
- 강화·신규: Sanil is routed as contracted-backlog plus capacity-manufacturing; the declared land-controlled second-factory project must be classified by the typed Capacity Gate and, when confirmed incremental, consumed as one Core capacity, CAPEX and ramp path.
- 약화·폐기: 없음

## Run Integrity
- Valuation scope: FULL_INTRINSIC
- Ledger snapshot: 3dc77e6b69167052a84e618030d3f525c7cb37ed469d47916e6bce686d8a7ccd
- Assumption set: 2eadbb684885cbddc817fa1a0a090e0bfeadcc055c89eee39701337b67233f65
- Valuation: a417aae2e779c930ef2a8473ac2f662818fcf98766a5e8383f06e5718b4e9ce6
- Audit: 77cd47509df8a7aeda2516db44b95a81cf7662d9fb87a6cfa0a0d7514f6af1f5
- Freeze token: 53606140ec9e89e14e953aab54c1f2b8eabfb4f4e3715f1252970d9deee5a221
