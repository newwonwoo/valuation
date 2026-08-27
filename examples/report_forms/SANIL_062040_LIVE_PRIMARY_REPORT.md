# 산일전기(062040) PRISM LIVE_PRIMARY 보고서

- 데이터 기준일: **2026-08-26**
- 검증 상태: **VERIFIED_FROZEN**
- 투자검토 상태: **Preliminary source-backed underwrite**
- 현재가(Freeze 후 로드): **176,900원**
- Street 참고 목표가(Freeze 후 로드): **260,000원**
- Down / Core / Bull: **119,833원 / 168,223원 / 217,104원**
- Hierarchical Beta: **0.793**
- WACC: **7.764%**
- Core 반영 Capacity 프로젝트: **SANIL_SECOND_FACTORY_RAMP, SANIL_UHV_PROPERTY_ACQUISITION_20260826**

## PM 결론

산일전기는 수요 검증 단계를 넘어 생산능력과 ramp가 가치의 핵심 병목이 된 회사입니다. 이번 run은 기존 제2공장뿐 아니라 2026년 8월 26일 체결된 초고압 변압기 생산용 부동산 양수계약을 별도 Core 프로젝트로 분리했습니다. 두 프로젝트의 Capacity·CAPEX·ramp 경로를 Scenario와 DCF가 실제 소비한 뒤 Beta·WACC, Audit, Freeze를 통과했습니다.

현재가는 확률가중 기대값이 아니라 개별 Down/Core/Bull 세계관과 비교해야 합니다. 역사적 calibration cohort가 아직 충분하지 않아 Expected Value는 의도적으로 산출하지 않았습니다. 이 보고서의 FCFF 경로는 회사 가이던스가 아니라 2025 사업보고서와 2026년 2분기 IR을 기반으로 한 **PRISM analyst underwrite**입니다.

## Evidence Confidence / Underwriting Status

- 회사 실적·수주·Capacity·부지·CAPEX: 회사 공시·IR 기반, **높은 증거 신뢰도**
- Beta peer 관측: 동일 KOSPI benchmark·동일 기간·주간 수익률 OLS 기반이며 회귀 표준오차와 시계열 hash를 보존, **중간~높은 증거 신뢰도**
- 일간 OLS는 비동시거래·빈도 민감도 진단값으로 별도 보존하며 주간 Beta와 임의 평균하지 않습니다.
- WACC 거시입력과 country-risk lambda: 출처가 명시된 외부 시장자료 및 PRISM 판단값, **중간 신뢰도**
- Down/Core/Bull FCFF: 공시 사실에서 파생한 분석가 가정이며 회사 가이던스가 아닙니다.
- 초고압 부동산 계약은 LAND_CONTROL과 692.5억원 현금유출을 공식 확정하지만, 정확한 생산 CAPA는 미공시이므로 증분 FCFF는 보수적 bounded underwrite입니다.

## Source Register

- 2025 사업보고서: https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm
- 2026년 2분기 IR: https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002&mode=view&page=1&s_cate=&s_keyword=&s_type=
- 2026년 8월 26일 초고압 생산용 부동산 양수결정: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660
- 실제 peer Beta·WACC 원장: https://github.com/newwonwoo/valuation/blob/main/docs/SANIL_RISK_SOURCE_REGISTER.md
- PRISM underwriting assumptions: https://github.com/newwonwoo/valuation/blob/main/config/sanil_live_snapshot.yaml#scenarios
- Street 참고자료(미래에셋증권): https://securities.miraeasset.com/bbs/board/message/view.do?categoryId=1800&messageId=2341906
- Street 참고자료(IBK투자증권): https://www.yna.co.kr/view/AKR20260810017900008
- Street 참고자료(신한투자증권): https://www.yna.co.kr/amp/view/AKR20260811028700008
- 현재가: https://finance.naver.com/item/main.naver?code=062040

---

# PRISM Verified Controlled-Run Report

- Run ID: `SANIL-062040-20260826`
- Execution mode: `live_primary`
- Run status: **VERIFIED_FROZEN**
- Attestation hash: `86b4e41abd188f9b9b9b1c90c06e89410d4400f48e31aad4a5dc853c507ecd3c`

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
| `broker_research_primary_verification_chain` | **PASS** | pre-freeze Broker Research was partitioned, primary-verified and audit-bound |
| `capacity_assessment` | **PASS** | the typed Capacity Commitment assessment and hash are present |
| `capacity_audit` | **PASS** | the Capacity omission/double-count audit passed |
| `capacity_core_consumption_chain` | **PASS** | Core Capacity, CAPEX and ramp paths are bound through valuation |
| `freeze_hash_binding` | **PASS** | Freeze is bound to the same Evidence, assumptions, valuation and audit |

## Immutable Run Identities

| Artifact | Hash |
|---|---|
| Evidence Ledger | `b97bc8f5ed1722ae45ec174d1ba36c55b9bcc7f023ac375d27314651194b3be0` |
| Assumption set | `6d58f3ab92c3784a4c25ae932051c20e5c444427220da2ed5b1a0b2ec8e718ed` |
| Scenario set | `8e75f10a05a561d7fb1c98d4ee431287c27a7cf0232aa6db52e8b50a8f34f974` |
| Beta | `567700b4b5094f7aaa61cc030d0c8758ed146e9a38a3be4f4d56c5999f0a121e` |
| WACC | `2beebb32d08c7f6e354a0771edba24e0f108aea78e9ba175b0b59de16257325b` |
| Capacity assessment | `30990a2dd5985f766892ce206687c4aa4368e6a59dfef0da6f0f777939cb4543` |
| Capacity consumption | `cc91fcc67ce9685c60520f028185caac90360ed900e1acaf0c0dc3cf60ea11b7` |
| Capacity scenario | `d497a0b33d96c14fbef1bc813fa8a417bb56cf02fcf1df72173a50ba1530cb5d` |
| Capacity valuation | `c4014c11bd060ce675b9eb0d5505e7571e5df6bd596b3eb5530591dae73aed52` |
| Capacity PER | `3f41768bb057dfa6c56282eccb2d9c8c8327d7ed61b1834c2772d76ef4e49884` |
| Capacity consistency | `bbe422353f84d98bdfe7662b34ff4e5b32f0dd7ba09a4f66b216c389433282fe` |
| Capacity audit | `c209afaec588228ea553f88ae0f0d6cc281f8757b7dd3d23b2799a43f8f7c172` |
| Broker pre-freeze | `480a3a2ca4b960a5b69e7c88c49515942e9e5d73b12ce6a03f160ceedbce368e` |
| Broker audit | `cde5e8a6d1871c5b8ac75c278bd6db27e66d2a9e6024ba11990464c890413cc8` |
| Valuation | `923c3eeeb6ab9a1431c01b08099fe2cf46ea4189d85e0242ea7df53ea7481413` |
| Audit | `4fc733474f2a83eb0bc803a34c687a985bf4598d7db89af309c04057595a87f3` |
| Intrinsic Freeze | `ebaa5714184a6769e84162a612483b957eccfbf466f336a31c4920039eeab8b8` |

## Stage Trace

| # | Stage | Status | Blocking | Rationale |
|---:|---|---|---:|---|
| 1 | `COMPANY_RESOLUTION` | `pass` | NO | company identity resolved from a declared live resolver contract |
| 2 | `LOAD_COMPANY_STATE` | `pass` | NO | no prior company state; first-run empty state is valid \| loaded 0 immutable module-impact learning record(s) |
| 3 | `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT` | `pass` | NO | versioned Industry Knowledge snapshot loaded and hash-verified |
| 4 | `SOURCE_FRESHNESS_PRECHECK` | `pass` | NO | live source-watch precheck passed |
| 5 | `SEGMENT_DECOMPOSITION` | `pass` | NO | authoritative-lineage-backed segment decomposition completed |
| 6 | `INDUSTRY_DNA_ROUTE` | `pass` | NO | all decomposed segments routed to evidence-backed multi-label Industry DNA profiles |
| 7 | `MODULE_REQUIREMENT_PLAN` | `pass` | NO | Broker Research discovery partitioned context, primary-verification-only and quarantined claims; primary verification metrics were compiled into the Module Requirement Plan \| compiled canonical Module Requirement Plan and non-destructive learned research loadout |
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
| 25 | `AUDIT_GATE` | `pass` | NO | Broker Research pre-freeze placement, primary verification and quarantine audit passed \| capacity omission, baseline and double-count audit passed \| decision-impact record and run-bound generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present |
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
- 기업의 기존 강점: Sanil already has export customer access, a high-value specialty-transformer mix, an 88.9% utilized production base, reported backlog and a controlled second-factory site with committed CAPEX and a separate signed UHV property-acquisition contract.
- 비자명한 연결: The external power-equipment bottleneck specifically revalues Sanil's existing customer relationships and pre-invested site because those assets can convert scarce delivery slots into backlog conversion and FCFF.
- 시장의 인식 공백: A generic small-transformer framing can separate current earnings from the option value of land-controlled capacity and overlook that the site, customer access and production know-how already exist.
- 가치 포착 경로: land control and committed CAPEX → equipment/ramp execution → effective capacity → backlog conversion → revenue, margin and free cash flow
- 인과 경로: power-infrastructure demand and transformer-slot scarcity rise → qualified delivery capacity becomes the binding buyer constraint → Sanil's existing customer access, operating base and controlled site absorb the need → capacity, CAPEX and ramp are consumed together in the Core scenario → incremental shipments convert backlog into revenue and FCFF
- 시장 인식 트리거: official second-factory equipment or production ramp disclosure; effective-capacity growth with backlog conversion; high-value product mix and margin retention after ramp
- 반증·철회 조건: the company cancels the program or confirms it is fully included in the frozen baseline; backlog or orders decline before capacity converts to shipments; ramp costs and margin normalization offset the added production ceiling
- 다음 검증: next quarterly filing for factory ramp, CAPEX and utilization; orders-to-revenue conversion and customer concentration; cash conversion after expansion spending
- Supporting Evidence: E:SANIL:orders, E:SANIL:backlog, E:SANIL:utilization, E:SANIL:expansion_land_control, E:SANIL:expansion_site_area, E:SANIL:expansion_capex_committed, E:SANIL:UHV:land_control, E:SANIL:UHV:capex_committed
- Contradicting Evidence: 없음
- LLM confidence: 78%

## Intrinsic Value
- Down intrinsic: 119,832.77 KRW/share
- Core intrinsic: 168,223.31 KRW/share
- Bull intrinsic: 217,104.3 KRW/share
- Expected Value: 미산출 — 시나리오 확률이 CALIBRATED 상태가 아니므로 숫자 가중을 보류했습니다.

## Street Gap
- 리포트 수: 3
- 평균 목표가: 260,000 KRW
- Down 대비: -140,167.23 (-53.9%)
- Core 대비: -91,776.69 (-35.3%)
- Bull 대비: -42,895.7 (-16.5%)

## Current Market Compare
- 현재가: 176,900 KRW (2026-08-26)
- Down 기대수익 간격: -57,067.23 (-32.3%)
- Core 기대수익 간격: -8,676.69 (-4.9%)
- Bull 기대수익 간격: 40,204.3 (+22.7%)

## Module Impact / Research Efficiency
- 측정 완료: DETERMINISTIC_VALUATION
- 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, HIERARCHICAL_BETA_ENGINE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE 외 10개
- 비적용: 없음
- 실패: 없음
- 조사비용: source queries 0, documents 0, LLM calls 0, elapsed 0.0s
- 하향 검토 후보: 없음
- 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## Audit & Coverage
- Audit: PASS (29 checks)
- Doctrine coverage: 27/27 terminally acceptable

## Thesis Delta
- 강화·신규: Broker Research factual leads were converted to primary-source verification and target forecasts/targets were quarantined before intrinsic valuation. Sanil is routed as contracted-backlog plus capacity-manufacturing; the declared land-controlled second-factory project must be classified by the typed Capacity Gate and, when confirmed incremental, consumed as one Core capacity, CAPEX and ramp path.
- 약화·폐기: 없음

## Run Integrity
- Valuation scope: FULL_INTRINSIC
- Ledger snapshot: b97bc8f5ed1722ae45ec174d1ba36c55b9bcc7f023ac375d27314651194b3be0
- Assumption set: 6d58f3ab92c3784a4c25ae932051c20e5c444427220da2ed5b1a0b2ec8e718ed
- Valuation: 923c3eeeb6ab9a1431c01b08099fe2cf46ea4189d85e0242ea7df53ea7481413
- Audit: 4fc733474f2a83eb0bc803a34c687a985bf4598d7db89af309c04057595a87f3
- Freeze token: ebaa5714184a6769e84162a612483b957eccfbf466f336a31c4920039eeab8b8
