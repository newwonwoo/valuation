# 산일전기(062040) PRISM LIVE_PRIMARY 보고서

- 데이터 기준일: **2026-08-26**
- 검증 상태: **VERIFIED_FROZEN**
- 투자검토 상태: **Preliminary source-backed underwrite**
- 현재가(Freeze 후 로드): **176,900원**
- Street 참고 목표가(Freeze 후 로드): **280,000원**
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

---

# PRISM Verified Controlled-Run Report

- Run ID: `SANIL-062040-20260826`
- Execution mode: `live_primary`
- Run status: **VERIFIED_FROZEN**
- Attestation hash: `1df4b3ab44f750a240c50d9d5003a275a2e32b4fde83c4d606f0e6a025b3d51a`

## Verification
- Checks: **21/21 PASS**
- Canonical stages: **33/33 terminal traces**

## Frozen Identity Chain
- Evidence: `b97bc8f5ed1722ae45ec174d1ba36c55b9bcc7f023ac375d27314651194b3be0`
- Assumptions: `6d58f3ab92c3784a4c25ae932051c20e5c444427220da2ed5b1a0b2ec8e718ed`
- Scenarios: `16bdd1d42c1cef90abae55d0fe1872fd729bdc49b86d873f968bc3ba55717694`
- Valuation: `f3c586a7786ca8691ae343dd02a70d9d093b8c0853622634a48b7ca8d7c08492`
- Audit: `7526decadfa3282335509009aa9faec346655d84e13192756ab67c111542f1db`
- Intrinsic Freeze: `a1de2ff479af4f8b748d4e49fd9f87d0b9bebe7a27c89f1fe0c4d944daa38f10`
- Auxiliary bindings: Beta `567700b4b5094f7aaa61cc030d0c8758ed146e9a38a3be4f4d56c5999f0a121e` · WACC `2beebb32d08c7f6e354a0771edba24e0f108aea78e9ba175b0b59de16257325b` · Capacity assessment `30990a2dd5985f766892ce206687c4aa4368e6a59dfef0da6f0f777939cb4543` · Capacity consumption `cc91fcc67ce9685c60520f028185caac90360ed900e1acaf0c0dc3cf60ea11b7` · Capacity scenario `28e27a5c5fd1659ccd718165d846e0ef53c67022a135fe1ac84e86c9123d7683` · Capacity valuation `7356323a77f47efb13c2dc00feb918515da43180f9096aaff252ea1402f72b2d` · Capacity PER `2fde85da7644231967ea95f2d6af39a66cff72e96b70f0d3cb0dfedd3695c9e0` · Capacity consistency `e6fffcfc4c0f4b7a442ac18e72d5eec96e201be11331abd7a6ebed9b793af6fb` · Capacity audit `c57b0531cf5655cd0828cdd3a7288391b72a642a1d838678961d526711999bcd` · Broker pre-freeze `ce7d809ba2f9e2a91a5fbb3604dbb80fbe7af2b2694f3195ce549cdef95991eb` · Broker audit `a7c344017374899fdbefb684ba0facfde4b5ed7a6caaa2ac29f330f6233ab9f6`

## Major Gate Summaries

### 1. Evidence and Routing — PASS (9/9)
- Result: append-only EvidenceLedger validated and canonical runtime snapshot frozen
- Risk: NONE · Next: `G2_INSIGHT_CHALLENGE`

### 2. Insight and Challenge — WARNING (5/5)
- Result: Blind Red Team left no unresolved blocking issue
- Risk: ROCKET_INSIGHT_SCAN: live Rocket Insight scanner dispatch completed with warnings · Next: `G3_ASSUMPTIONS_METHOD_RISK`

### 3. Assumptions, Method and Risk — PASS (5/5)
- Result: currency-consistent WACC computed from live Beta and independent marginal financing inputs
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
- **G2_INSIGHT_CHALLENGE:** 10 `ROCKET_INSIGHT_SCAN`=warning · 11 `UPSTREAM_FUNDING_SCAN`=pass · 12 `RESEARCHER_A`=pass · 13 `BLIND_RED_TEAM_B`=pass · 14 `RESEARCH_LOOP`=skipped_not_applicable
- **G3_ASSUMPTIONS_METHOD_RISK:** 15 `EVIDENCE_TO_ASSUMPTION_BRIDGE`=pass · 16 `SCENARIO_BUILD`=pass · 17 `VALUATION_METHOD_INTENT`=pass · 18 `HIERARCHICAL_BETA_ESTIMATION`=pass · 19 `WACC_VALIDATION`=pass
- **G4_VALUATION_AUDIT_FREEZE:** 20 `DETERMINISTIC_VALUATION`=pass · 21 `HIERARCHICAL_WARRANTED_PER`=skipped_not_applicable · 22 `DCF_PER_ASSUMPTION_CONSISTENCY_GATE`=pass · 23 `CROSS_METHOD_DOUBLE_COUNT_AUDIT`=pass · 24 `PROBABILITY_DISTRIBUTION_ANALYSIS`=warning · 25 `AUDIT_GATE`=pass · 26 `INTRINSIC_VALUE_FREEZE`=pass
- **G5_POST_FREEZE_PERSISTENCE:** 27 `STREET_REFERENCE_LOAD`=pass · 28 `STREET_GAP_ANALYZER`=pass · 29 `MARKET_PRICE_LOAD`=pass · 30 `MARKET_COMPARE`=pass · 31 `THESIS_DELTA`=pass · 32 `SAVE_STATE`=pass · 33 `FINAL_REPORT`=pass
- Exact rationales and output keys remain in the immutable `control_plane_trace.json` artifact.

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
- Supporting Evidence: [E:SANIL:orders](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm), [E:SANIL:backlog](https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002&mode=view&page=1&s_cate=&s_keyword=&s_type=), [E:SANIL:utilization](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm), [E:SANIL:expansion_land_control](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm), [E:SANIL:expansion_site_area](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm), [E:SANIL:expansion_capex_committed](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm), [E:SANIL:UHV:land_control](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660), [E:SANIL:UHV:capex_committed](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660)
- Contradicting Evidence: 없음
- LLM confidence: 78%

## Intrinsic Value
- Down intrinsic: 119,832.77 KRW/share
- Core intrinsic: 168,223.31 KRW/share
- Bull intrinsic: 217,104.3 KRW/share
- Expected Value: 미산출 — 시나리오 확률이 CALIBRATED 상태가 아니므로 숫자 가중을 보류했습니다.

## Probability Calibration
- Status: UNCALIBRATED · Numeric weighting: WITHHELD
- Lineage: dataset `NOT_AVAILABLE` · snapshot `NOT_AVAILABLE`

## Street Gap
- 리포트 수: 2
- 평균 목표가: 280,000 KRW
- Down 대비: -160,167.23 (-57.2%)
- Core 대비: -111,776.69 (-39.9%)
- Bull 대비: -62,895.7 (-22.5%)

## Current Market Compare
- 현재가: 176,900 KRW (2026-08-26)
- Down 기대수익 간격: -57,067.23 (-32.3%)
- Core 기대수익 간격: -8,676.69 (-4.9%)
- Bull 기대수익 간격: 40,204.3 (+22.7%)

## Sources — Direct Verification
- **SANIL_UHV_PROPERTY_ACQUISITION_20260826** — Evidence 7개: uhv_property_asset_ratio, expansion_baseline_inclusion, expansion_capex_committed, uhv_property_contract_amount, expansion_land_control, expansion_ramp_date 외 1개 (effective 2026-08-26) [원문 바로 열기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660)
- **Beta inputs** — beta_source_refs [원문 바로 열기](https://finance.naver.com/)
- **Current market price** — market price as of 2026-08-26 [원문 바로 열기](https://finance.naver.com/item/main.naver?code=062040)
- **SANIL_UNDERWRITING_20260826** — Evidence 54개: model_bull_diluted_shares, model_bull_ev_adjustment, model_bull_expansion_capex, model_bull_fcff_year_1, model_bull_fcff_year_2, model_bull_fcff_year_3 외 48개 (effective 2026-08-26) [원문 바로 열기](https://github.com/newwonwoo/valuation/blob/main/config/sanil_live_snapshot.yaml)
- **Beta inputs / SANIL_RISK_SOURCE_REGISTER_20260825_REGRESSION / WACC inputs** — Evidence E:SANIL:beta_selection_L1_BROAD_SECTOR: beta_selection_L1_BROAD_SECTOR (effective 2026-08-26); Evidence E:SANIL:beta_selection_L2_INDUSTRY: beta_selection_L2_INDUSTRY (effective 2026-08-26); Evidence E:SANIL:beta_selection_L3_RISK_DRIVER_SUBINDUSTRY: beta_selection_L3_RISK_DRIVER_SUBINDUSTRY (effective 2026-08-26); Evidence E:SANIL:beta_selection_L4_ECONOMIC_TWINS: beta_selection_L4_ECONOMIC_TWINS (effective 2026-08-26); beta_source_refs; wacc_source_refs [원문 바로 열기](https://github.com/newwonwoo/valuation/blob/main/docs/SANIL_RISK_SOURCE_REGISTER.md)
- **Beta inputs / Company identity / SANIL_2025_ANNUAL_REPORT / WACC inputs** — Evidence 26개: asp, backlog_conversion, book_to_bill, cancellation_rate, cancellation_terms, cash 외 20개 (effective 2024-01-01, 2025-12-31); beta_source_refs; company resolution; wacc_source_refs [원문 바로 열기](https://kind.krx.co.kr/external/2026/03/18/000706/20260318003527/11011.htm)
- **Broker research discovery / Street: Mirae Asset Securities** — pre-freeze discovery/corroboration only; target price published 2026-08-07 [원문 바로 열기](https://securities.miraeasset.com/bbs/board/message/view.do?categoryId=1800&messageId=2341906)
- **SANIL_2026_Q2_IR** — Evidence 8개: backlog, expansion_baseline_inclusion, expansion_cancelled, expansion_ramp_date, net_income_h1_2026, no_active_capacity_expansion 외 2개 (effective 2026-06-30) [원문 바로 열기](https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002&mode=view&page=1&s_cate=&s_keyword=&s_type=)
- **Street: Shinhan Securities** — target price published 2026-08-11 [원문 바로 열기](https://www.yna.co.kr/amp/view/AKR20260811028700008)
- 전체 Evidence ID·지표·기준일 매핑은 동일 run의 immutable Evidence Ledger에 보존됩니다.

## Module Impact / Research Efficiency
- 측정 완료: DETERMINISTIC_VALUATION · 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, HIERARCHICAL_BETA_ENGINE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE 외 10개
- 비적용: 없음 · 실패: 없음
- 조사비용: source queries 0, documents 0, LLM calls 0, elapsed 0.0s
- 하향 검토 후보: 없음 · 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## Audit & Coverage
- Audit: PASS (29 checks)
- Doctrine coverage: 27/27 terminally acceptable

## Thesis Delta
- 강화·신규: Broker Research factual leads were converted to primary-source verification and target forecasts/targets were quarantined before intrinsic valuation. Sanil is routed as contracted-backlog plus capacity-manufacturing; the declared land-controlled second-factory project must be classified by the typed Capacity Gate and, when confirmed incremental, consumed as one Core capacity, CAPEX and ramp path.
- 약화·폐기: 없음

## Run Integrity
- Scope: FULL_INTRINSIC · Freeze: `a1de2ff479af4f8b748d4e49fd9f87d0b9bebe7a27c89f1fe0c4d944daa38f10`
- Chain: ledger `b97bc8f5ed1722ae45ec174d1ba36c55b9bcc7f023ac375d27314651194b3be0` · assumptions `6d58f3ab92c3784a4c25ae932051c20e5c444427220da2ed5b1a0b2ec8e718ed` · valuation `f3c586a7786ca8691ae343dd02a70d9d093b8c0853622634a48b7ca8d7c08492` · audit `7526decadfa3282335509009aa9faec346655d84e13192756ab67c111542f1db`
- Calibration: dataset `NOT_APPLIED` · snapshot `NOT_APPLIED`
