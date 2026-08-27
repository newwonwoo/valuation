# {{ 기업명 }} 리서치·가치평가 보고서

## 투자 요약

- 핵심 판단: {{ 근거에 기반한 투자논지와 결정요인 }}
- 가치평가 범위: {{ 하방 }}원–{{ 상방 }}원
- 현재가 해석: {{ 기준 내재가치와 현재가의 차이 및 필요한 확인사항 }}
- 매수 판단: {{ 확률 보정 또는 진입 규칙 미충족 시 구체적 매수가 보류 }}

## 가치평가

- 하방 시나리오: 주당 {{ down_value }}원
- 기준 시나리오: 주당 {{ core_value }}원
- 상방 시나리오: 주당 {{ bull_value }}원
- 확률가중 기대값: {{ 보정 완료 시 산출 | 미보정 시 보류 }}

## 핵심 가정과 위험

- 평가방법: {{ 한국어 평가방법명 }}
- 위험 입력: {{ 계층형 베타와 가중평균자본비용 }}
- 시나리오 가정: {{ 기업잉여현금흐름·영구성장률·영구 투하자본이익률 }}
- 핵심 위험: {{ 실적·생산능력·자본적지출·확률 보정 제약 }}

## 증권사·시장 비교

{{ 내재가치 고정 후 불러온 증권사 목표가와 현재가 비교 }}

## 인공지능 인사이트 — 환경 변화 × 기업 강점

{{ 가치평가 계산과 분리한 1,000자 이하 연결 인사이트 }}

## 최종 요약 이미지

{{ 회사 강점·투자 결론·가치평가 이미지 1장 }}

{{ 가치평가 가정·위험·출처 이미지 1장 }}

## 정보 출처 — 원문 직접 검증

{{ 모든 핵심 주장과 입력값의 직접 원문 링크 }}

## 분석 범위와 유의사항

{{ 사실·분석가 가정·인공지능 인사이트의 구분 및 평가 제약 }}

---

# 감사 부록 — 검증·추적

- 검증 상태: {{ 검증·고정 완료 | 검증 미완료 | 차단 }}
- 자동 점검: {{ passed_checks }}/{{ total_checks }} 통과
- 통제 단계: {{ terminal_stage_count }}/33개 최종 추적 완료

## 대형 게이트 완료 요약

### {{ 순번 }}. {{ 한국어 게이트명 }} — {{ 상태 }}

- 결과: {{ 한국어 요약 }}
- 잔여위험: {{ 한국어 위험 요약 }} · 다음 단계: {{ 한국어 단계명 }}

<details>
<summary>기술 식별자·해시 확인</summary>

### 33단계 진행 상태

{{ 33개 단계의 한국어 이름과 상태 }}

### 실행 식별자와 해시

- 실행 식별자: `{{ run_id }}`
- 검증증명 해시: `{{ attestation_hash }}`
- 고정 해시: `{{ ledger_snapshot_hash | assumption_set_hash | scenario_set_hash | valuation_hash | audit_hash | freeze_token_hash }}`
- 보조 결속정보: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | 해당 없음 }}`
- 실패 점검 기술 식별자: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | major_gate_reporting_contract | major_gate_delivery | direct_source_links | 없음 }}`

</details>
