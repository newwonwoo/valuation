# PRISM 검증·통제 실행 보고서

- 실행 ID: `FULL-LIVE-1`
- 실행 모드: `live_primary`
- 실행 상태: **검증·고정 완료 (`VERIFIED_FROZEN`)**
- 검증증명 해시: `450dadee4d8cad0095f08cfd999f8fd4ef2e1f0f726b5be34b317f0cc09f65d5`

## 실행 검증
- 점검 결과: **19/19 통과**
- 표준 단계: **33/33개 최종 추적 완료**

## 고정된 식별정보 사슬
- 증거: `844702b379f405baffd8cea944854ac2c00a1b0e8141a693bfd75fd8934a786d`
- 가정: `f9a111745f4945d119f02f1708f026ff7473c9c96a6055c454370634d2a0e818`
- 시나리오: `363189a1674c763b0f3d2e60be59156f25e956d800342bf9f468dbf093c4538c`
- 가치평가: `759890294b90fb9bda449cc6b539214a0795bb59ad27d1f46e37b42b8f99da06`
- 감사: `484915ff80ef965128618a753168b38ae268ebcc4f4656bfb8a9e84270a15d5a`
- 내재가치 고정: `77990c6f5d8c2fd9b152a537b6ecf4cf6e5140640e00c9f817acad2bf0105ed1`
- 보조 결속정보: 생산능력 평가 `a3545801a2b8a62a817dc8625fd5baccc104aa9ed22e1476e89b8c440ce55462` · 생산능력 시나리오 `749eb5803378d1917242a7bbc628d9f735b5a3101a0593da19d5c3fa3a17ff24` · 생산능력 가치평가 `5e36a496bd37604aa33ffb0b4f80cd48eadf839b83218aaa9422a35649e297fe` · 생산능력 주가수익비율 `09d1f570a1c55c08e4639a4c59546ddac49c2704ed6ebdf185cba5cd4457d478` · 생산능력 정합성 `adfc3920a842875012b27720a55cf7324ede5d2ae4abf320d1c4484f3aafb1eb` · 생산능력 감사 `5405620256db2ab82529b83171ef2e5f41bc1d1fd8d1785902318c52f5b0c353`

## 대형 게이트 완료 요약

### 1. 증거 수집·산업 라우팅 — 통과 (9/9)
- 결과: 증거 수집·산업 라우팅을 완료하고 불변 Evidence Ledger를 고정했습니다
- 잔여위험: 없음 · 다음 단계: `G2_INSIGHT_CHALLENGE`

### 2. 인사이트 도출·반증 검토 — 통과 (5/5)
- 결과: 환경 변화와 기업 강점의 연결 인사이트 및 반증 검토를 완료했습니다
- 잔여위험: 없음 · 다음 단계: `G3_ASSUMPTIONS_METHOD_RISK`

### 3. 가정·평가방법·위험 — 통과 (5/5)
- 결과: 가정·평가방법·베타·가중평균자본비용의 적용 여부를 확정했습니다
- 잔여위험: 없음 · 다음 단계: `G4_VALUATION_AUDIT_FREEZE`

### 4. 가치평가·감사·내재가치 고정 — 경고 (7/7)
- 결과: 결정론적 가치평가와 감사를 통과해 내재가치를 고정했습니다
- 잔여위험: PROBABILITY_DISTRIBUTION_ANALYSIS: 실제 해결 이력 기반 확률 보정이 완료되지 않아 확률가중 기대값을 산출하지 않았습니다 · 다음 단계: `G5_POST_FREEZE_PERSISTENCE`

### 5. 고정 후 비교·영구 저장 — 통과 (7/7)
- 결과: 시장·증권사 비교 후 한국어 최종보고서와 요약 이미지 2장을 불변 저장했습니다
- 잔여위험: 없음 · 다음 단계: `FINAL_RESULT_REPORT`

## 최종보고서 편집 계약
- 본문 목표: 3–4쪽
- 감사 부록 목표: 1–2쪽
- 전체 상한: 6쪽
- 이미지: 2장을 본문 3–4쪽 안에 포함합니다.
- 활자: 본문 ≥ 13pt, 주 제목 ≥ 22pt, 절 제목 ≥ 18pt. 조밀한 대형 표는 금지합니다.
- 필수: 모든 주장의 출처를 `정보 출처 — 원문 직접 검증`의 HTTP(S) 원문 링크에 연결합니다.
- 필수 산출물: 한국어 본문과 함께 투자결론·가치평가 요약 1장, 가치평가 가정·위험·출처 요약 1장을 생성합니다.
- 인공지능 관여 내용: 결정론적 결과와 분리된 독립 구역으로 표시하고 1,000자 이하로 제한합니다.

## 압축 감사 부록 — 33단계 추적
- **G1_EVIDENCE_ROUTING:** 1 `COMPANY_RESOLUTION`=통과 · 2 `LOAD_COMPANY_STATE`=통과 · 3 `LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT`=통과 · 4 `SOURCE_FRESHNESS_PRECHECK`=통과 · 5 `SEGMENT_DECOMPOSITION`=통과 · 6 `INDUSTRY_DNA_ROUTE`=통과 · 7 `MODULE_REQUIREMENT_PLAN`=통과 · 8 `PRIMARY_EVIDENCE_COLLECTION`=통과 · 9 `EVIDENCE_LEDGER`=통과
- **G2_INSIGHT_CHALLENGE:** 10 `ROCKET_INSIGHT_SCAN`=통과 · 11 `UPSTREAM_FUNDING_SCAN`=해당 없음 · 12 `RESEARCHER_A`=통과 · 13 `BLIND_RED_TEAM_B`=통과 · 14 `RESEARCH_LOOP`=해당 없음
- **G3_ASSUMPTIONS_METHOD_RISK:** 15 `EVIDENCE_TO_ASSUMPTION_BRIDGE`=통과 · 16 `SCENARIO_BUILD`=통과 · 17 `VALUATION_METHOD_INTENT`=통과 · 18 `HIERARCHICAL_BETA_ESTIMATION`=해당 없음 · 19 `WACC_VALIDATION`=해당 없음
- **G4_VALUATION_AUDIT_FREEZE:** 20 `DETERMINISTIC_VALUATION`=통과 · 21 `HIERARCHICAL_WARRANTED_PER`=해당 없음 · 22 `DCF_PER_ASSUMPTION_CONSISTENCY_GATE`=통과 · 23 `CROSS_METHOD_DOUBLE_COUNT_AUDIT`=통과 · 24 `PROBABILITY_DISTRIBUTION_ANALYSIS`=경고 · 25 `AUDIT_GATE`=통과 · 26 `INTRINSIC_VALUE_FREEZE`=통과
- **G5_POST_FREEZE_PERSISTENCE:** 27 `STREET_REFERENCE_LOAD`=통과 · 28 `STREET_GAP_ANALYZER`=통과 · 29 `MARKET_PRICE_LOAD`=통과 · 30 `MARKET_COMPARE`=통과 · 31 `THESIS_DELTA`=통과 · 32 `SAVE_STATE`=통과 · 33 `FINAL_REPORT`=통과
- 단계별 정확한 사유와 출력 키는 불변 `control_plane_trace.json` 산출물에 보존됩니다.

## 영구 저장된 리서치 보고서

# 고정 원자재 기업 PRISM 리서치·가치평가 보고서

## 최종 요약 이미지
![고정 원자재 기업 회사 강점·투자 결론·가치평가](PRISM_000000_01_summary.svg)

![고정 원자재 기업 가치평가 가정·위험·출처](PRISM_000000_02_assumptions.svg)

## 인공지능 인사이트 — 환경 변화 × 기업 강점
- 적용범위: 인공지능은 외부 환경 변화와 기업 강점의 연결 가설·반증 조건만 제시하며 가치평가 계산이나 가정 확정에는 관여하지 않습니다.
- 상태: 해당 없음 (`NOT_APPLICABLE`)
- 사유: 이 고정 인수시험 데이터는 결정론적 실행 무결성을 검증하며 외부 환경 변화에 관한 투자논지를 주장하지 않습니다.

## 내재가치
- Base 내재가치: 주당 70,000 KRW
- 확률가중 기대값: 미산출 — 시나리오 확률이 보정 완료 상태가 아니므로 수치 가중을 보류했습니다.

## 확률 보정 상태
- 보정 상태: `UNCALIBRATED` · 수치 가중: 보류
- 계보: 데이터셋 `없음` · 스냅샷 `없음`

## 증권사 목표가 비교
- 반영 리포트: 2건
- 평균 목표가: 70,000 KRW
- Base 대비: 0 (+0.0%)

## 현재 시장가격 비교
- 현재가: 65,000 KRW (2026-08-23)
- Base 기대수익 간격: 5,000 (+7.7%)

## 정보 출처 — 원문 직접 검증
- **frozen filing fixture / 기업 식별정보 / 증권사: BrokerA / 증권사: BrokerB / 현재 시장가격** — 근거 8개: benchmark_price, capacity, cash_cost, cost_curve_position, inventory, production 외 2개 (기준일 2026-06-30); 기업 식별 확인; 목표가 발표일 2026-08-01; 목표가 발표일 2026-08-05; 시장가격 기준일 2026-08-23 [원문 바로 열기](https://github.com/newwonwoo/valuation/blob/main/tests/test_full_live_primary_runtime.py)
- 전체 근거 ID·지표·기준일 매핑은 동일 실행의 불변 Evidence Ledger에 보존됩니다.

## 모듈 영향·조사 효율성
- 측정 완료: DETERMINISTIC_VALUATION · 미측정(NOT_MEASURABLE): ASSUMPTION_COMPILER, BLIND_RED_TEAM_B, BROKER_RESEARCH, EVIDENCE_LEDGER, EVIDENCE_TO_ASSUMPTION_BRIDGE, INDUSTRY_DNA_ROUTER, INDUSTRY_KNOWLEDGE, KNOWLEDGE_PLACEMENT_GATE 외 7개
- 비적용: HIERARCHICAL_BETA_ENGINE, UPSTREAM_FUNDING_SCAN, WACC_ENGINE · 실패: 없음
- 조사비용: 출처 조회 0회, 문서 검토 0건, 대규모 언어모델 호출 0회, 소요시간 0.0초
- 하향 검토 후보: 없음 · 미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE로 유지합니다.

## 감사·준수 범위
- 감사: 통과 (22개 점검)
- 원칙 준수: 27/27개 최종 허용 상태

## 투자논지 변화
- 강화·신규: 고정된 1차 출처가 확률가중하지 않은 기준 시나리오 하나를 뒷받침합니다
- 약화·폐기: 없음

## 실행 무결성
- 평가범위: `FULL_INTRINSIC` · 내재가치 고정: `77990c6f5d8c2fd9b152a537b6ecf4cf6e5140640e00c9f817acad2bf0105ed1`
- 해시 사슬: 원장 `844702b379f405baffd8cea944854ac2c00a1b0e8141a693bfd75fd8934a786d` · 가정 `f9a111745f4945d119f02f1708f026ff7473c9c96a6055c454370634d2a0e818` · 가치평가 `759890294b90fb9bda449cc6b539214a0795bb59ad27d1f46e37b42b8f99da06` · 감사 `484915ff80ef965128618a753168b38ae268ebcc4f4656bfb8a9e84270a15d5a`
- 확률 보정: 데이터셋 `미적용` · 스냅샷 `미적용`
