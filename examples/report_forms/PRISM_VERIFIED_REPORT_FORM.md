# PRISM 검증·통제 실행 보고서

- 실행 ID: `{{ run_id }}`
- 실행 모드: `LIVE_PRIMARY`
- 실행 상태: **{{ 검증·고정 완료 | 검증 미완료 | 차단 }}**
- 검증증명 해시: `{{ attestation_hash }}`

## 실행 검증

- 점검 결과: **{{ passed_checks }}/{{ total_checks }} 통과**
- 표준 단계: **{{ terminal_stage_count }}/33개 최종 추적 완료**
- 실패 점검만 표시: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | major_gate_reporting_contract | major_gate_delivery | direct_source_links | 없음 }} — {{ detail }}`

## 고정된 식별정보 사슬

- 증거: `{{ ledger_snapshot_hash }}`
- 가정: `{{ assumption_set_hash }}`
- 시나리오: `{{ scenario_set_hash }}`
- 가치평가: `{{ valuation_hash }}`
- 감사: `{{ audit_hash }}`
- 내재가치 고정: `{{ freeze_token_hash }}`
- 보조 결속정보: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | 해당 없음 }}`

## 대형 게이트 완료 요약

### {{ ordinal }}. {{ title }} — {{ 상태 }} ({{ completed/expected }})

- 결과: `{{ decisive_result }}`
- 잔여위험: `{{ residual_risk }}` · 다음 단계: `{{ next_action }}`

## 최종보고서 편집 계약

- 본문 목표: 3–4쪽
- 감사 부록 목표: 1–2쪽
- 전체 상한: 6쪽
- 이미지 2장은 별도 가산하지 않고 본문 3–4쪽 안에 포함
- 활자: 본문 ≥ 13pt, 주 제목 ≥ 22pt, 절 제목 ≥ 18pt. 조밀한 대형 표는 금지합니다.
- 필수: 모든 주장의 출처를 `정보 출처 — 원문 직접 검증`의 HTTP(S) 원문 링크에 연결합니다.
- 필수 이미지: `회사 강점·투자 결론·가치평가` 1장 + `가치평가 가정·위험·출처` 1장
- 인공지능 관여 내용: 결정론적 결과와 분리한 독립 구역으로 표시하며 1,000자 이하

## 압축 감사 부록 — 33단계 추적

- **{{ gate_id }}:** `{{ stage_number }} {{ stage }}={{ status }}` · …
- 단계별 정확한 사유와 출력 키는 불변 `control_plane_trace.json` 산출물에 보존됩니다.

## 영구 저장된 리서치 보고서

{{ 원문 검증 링크와 한국어 요약 이미지 2장을 포함한 불변 최종보고서 }}
