# Automatic Ablation Orchestrator

Status: canonical Decision Impact runtime extension  
Scope: module/scanner/gate counterfactual execution, interaction measurement, research-efficiency history, next-run loadout recommendation

## 1. 목적

`Decision Impact`의 측정 함수만 존재해도 각 모듈을 사람이 임의로 골라 비교하면 반복성과 유지보수성이 떨어진다. 이 Orchestrator는 한 번의 기준 Run을 고정한 뒤 적용 가능한 Unit을 하나씩 제거한 통제된 counterfactual Run을 실행하여 다음을 기록한다.

```text
Baseline Run
→ Leave-One-Module-Out Runs
→ Same-Group Pair Ablation
→ Value / Decision / Timing / Guardrail Impact
→ Research Effort
→ Module History
→ Next-Run Adaptive Loadout
```

이 계층은 기존 `DECISION_IMPACT` Unit의 실행 구현이다. 새로운 가치평가 공식을 만들지 않으며, deterministic runner가 반환한 `DecisionOutcome`만 비교한다.

## 2. 권한 경계

Orchestrator가 할 수 있는 일:

- 기준 Run을 한 번 실행한다.
- 적용 가능한 모듈별 단일 제거 실험을 실행한다.
- 같은 interaction group의 모듈을 쌍으로 제거해 중복·상호작용을 측정한다.
- `ModuleImpactTrace`가 누락된 모듈을 유지보수 오류 후보로 표시한다.
- 반복 영향도와 조사비용으로 다음 Run의 조사 강도를 추천한다.

Orchestrator가 할 수 없는 일:

- 현재가나 목표가를 사용해 가정을 조정한다.
- Unit을 자동 삭제하거나 canonical registry에서 제거한다.
- Audit 실패를 우회한다.
- LLM 추정값을 Compiled Assumption으로 확정한다.
- 다른 경제세계를 사용한 Run끼리 비교한다.

## 3. 입력 계약

각 Unit은 `ModuleExperimentSpec`을 가진다.

- `module_id`
- 현재 회사/미션에 대한 `applicable`
- `mandatory_guardrail`
- 상호작용 측정용 `interaction_group`
- 조건부 출동 여부 `condition_met`
- 표본 재확인 시점 `sample_due`
- 해당 Run의 `ResearchEffort`

Runner는 동일한 `ExperimentRequest`에 대해 결정론적인 `ExperimentArtifact`를 반환해야 한다.

`ExperimentArtifact`:

- `DecisionOutcome`
- 활성 Unit의 `ModuleImpactTrace`
- 제거로 인해 발생한 `guardrail_violations`

## 4. 단일 Ablation

기준 Run에서 Unit 하나만 제거한다.

```text
Impact(Unit A)
= Baseline Outcome − Outcome without A
```

비교 대상은 가치 숫자만이 아니다.

- Run status
- Route
- 선택 Method
- Assumption hash
- Conclusion tags
- Timing
- Blocking reasons
- Guardrail violation

따라서 평상시 가치 변화가 0인 Audit/Gate도 제거했을 때 금지 상태가 허용되면 `GUARDRAIL_CRITICAL`로 유지된다.

## 5. Pair Ablation과 상호작용

같은 `interaction_group` 안에서만 기본적으로 쌍 제거 실험을 한다. 전 모듈 조합을 무제한 실행하지 않는다.

```text
Interaction Residual(A,B)
= Joint Delta(A,B)
− Individual Delta(A)
− Individual Delta(B)
```

- 양의 residual: 둘을 함께 제거할 때 추가 손실이 발생하는 보완관계 가능성
- 음의 residual: 개별 효과가 같은 경로를 중복 설명하는 대체·중첩 가능성
- material residual: leave-one-out만 보고 Unit을 제거하면 안 되는 신호

이 값은 인과효과의 학술적 확정치가 아니라, 동일 deterministic model 안에서의 구조적 유지보수 신호다.

## 6. Adaptive Loadout

반복된 applicable Run만 사용한다. `NOT_APPLICABLE`은 영향률 분모에서 제외한다.

| 추천 | 처리 |
|---|---|
| `ALWAYS` | 다음 Run에 기본 출동 |
| `CONDITIONAL` | 활성화 조건 충족 시 출동 |
| `SAMPLE_ONLY` | 정기 표본 재검증 |
| `KEEP_GUARDRAIL` | 가치 변화와 무관하게 항상 유지 |
| `RETIRE_CANDIDATE` | 자동 삭제 금지, 사용자 검토 안건 |

`RETIRE_CANDIDATE`는 다음 조건을 반복 관측했을 때만 가능하다.

- 적용 가능한 Run이 정책상 최소 표본 이상
- 최종 가치·판단·시점·방법·Guardrail에 material effect 없음
- 조사비용이 높음

사용자 승인과 joint-ablation 검토 없이 Unit을 제거하지 않는다.

## 7. Control Plane 연결

```text
Unit Contract Registry
→ Applicable Module Specs
→ Automatic Ablation Report
→ Module History
→ Adaptive Loadout Plan
→ Control Plane Scanner/Module Loadout
```

Control Plane은 추천을 실행계획으로 변환하되 다음을 강제한다.

1. Mandatory guardrail은 항상 포함한다.
2. `RETIRE_REVIEW`는 사용자 승인 전까지 canonical 삭제로 이어지지 않는다.
3. Low-impact Unit도 `sample_due` 시점에는 재검증한다.
4. 새로운 산업·Regime에서는 과거 영향도만으로 배제하지 않는다.
5. Missing baseline trace는 유지보수 경고로 Doctrine Coverage에 남긴다.

## 8. 산출물

- `AblationReport`
- Unit별 `ModuleAblationResult`
- `PairInteractionResult`
- `ModuleHistoryEntry`
- `ModuleEfficiencySummary`
- `AdaptiveLoadoutPlan`

이 산출물은 `STATE_LEARNING`에 append-only로 저장되어야 하며 과거 Run을 수정하지 않는다.

## 9. 구현

- `src/valuation_engine/impact_orchestrator.py`
- `src/valuation_engine/decision_impact.py`
- `config/decision_impact_policy.yaml`
- `tests/test_impact_orchestrator.py`

## 10. 남은 통합 경계

현재 Orchestrator는 callback 기반 deterministic 실행 계약을 제공한다. 각 live company workflow는 자신의 active modules와 counterfactual runner adapter를 연결해야 한다. Generic Assumption Compiler/Evaluator가 완전한 live 상태로 승격되기 전까지 OCI legacy fixture의 변수 민감도와 Module ablation을 같은 것으로 표시하지 않는다.
