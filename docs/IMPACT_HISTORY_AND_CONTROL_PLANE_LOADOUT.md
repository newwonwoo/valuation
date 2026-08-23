# Impact History → Control Plane Loadout

Status: canonical State/Learning integration for Decision Impact

## 목적

Ablation 결과를 한 번 보고 끝내지 않고 Run별로 append-only 저장하여 다음 분석의 Module/Scanner 배치에 사용한다.

```text
AblationReport
→ ImpactHistoryRecord
→ ModuleImpactHistoryLedger
→ AdaptiveLoadoutPlan
→ ControlPlaneImpactLoadout
→ next mission deployment
```

## 기록 원칙

`ImpactHistoryRecord`는 다음을 고정한다.

- `record_id = run_id:module_id`
- Run ID
- Module ID
- `ModuleImpactAssessment`
- 조사비용 `ResearchEffort`
- 적용 가능 여부
- 실제 조사 수행 여부
- mandatory guardrail 여부
- timezone-aware 관측시각

동일 Run/Module 레코드를 덮어쓰지 않는다. 새 판단은 새 Run으로 append한다.

## 다음 Run 배치

`build_control_plane_impact_loadout()`은 impact history와 현재 회사의 `ModuleExperimentSpec`을 결합한다.

| Impact recommendation | Control Plane order |
|---|---|
| ALWAYS | DEPLOYED |
| CONDITIONAL + condition met | DEPLOYED |
| CONDITIONAL + condition unmet | DEFERRED_CONDITION |
| SAMPLE_ONLY + sample due | DEPLOYED |
| SAMPLE_ONLY + not due | DEFERRED_SAMPLE |
| KEEP_GUARDRAIL | DEPLOYED |
| RETIRE_CANDIDATE | RETIRE_REVIEW + user decision |
| NOT_APPLICABLE | SKIPPED_NOT_APPLICABLE |

## Mission override

현재 Mission이 특정 Module을 필수로 요구하면 조건부·표본 지연을 넘어 출동시킬 수 있다. 단:

- 존재하지 않는 Unit을 필수 지정하면 FAIL
- 현재 Industry DNA에 적용 불가능한 Unit을 필수 지정하면 FAIL
- mandatory guardrail 누락은 항상 FAIL
- RETIRE_REVIEW Unit의 Mission 투입은 가능하지만 canonical 폐기 판단은 여전히 사용자 결정 사항

## Doctrine Coverage 연결

각 배치명령은 다음 Coverage 상태를 남긴다.

- `pass`: 출동
- `warning`: 조건/표본 일정으로 지연
- `skipped_not_applicable`: 명시적 비적용
- `awaiting_user_decision`: 폐기 검토 대기

따라서 Control Plane의 silent skip 금지 원칙을 유지한다.

## 구현

- `src/valuation_engine/impact_history.py`
- `src/valuation_engine/control_plane_impact.py`
- `src/valuation_engine/impact_orchestrator.py`
- `tests/test_impact_history_control_plane.py`

## 제한

이 계층은 history와 배치명령을 제공한다. 실제 회사별 workflow adapter는 해당 Run의 Unit 활성집합과 deterministic counterfactual runner를 연결해야 한다. 과거 산업 Regime의 저영향 기록만으로 새로운 Regime에서 Unit을 영구 배제하지 않는다.
