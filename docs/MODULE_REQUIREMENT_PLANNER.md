# Module Requirement Planner

Status: canonical executable contract  
Scope: `IndustryDNAProfile` → evidence, KPI, scanner, method, risk and kill-condition deployment plan

## 1. 목적

Industry DNA를 분류 결과로만 남기지 않고 실제 조사·분석 명령으로 컴파일한다.

```text
Segment Evidence
→ Industry DNA
→ Sector Adapter + Economic Archetypes
→ Module Requirement Planner
→ Required Evidence / KPIs
→ Mandatory + Optional Scanner Loadout
→ Allowed / Forbidden Valuation Methods
→ Beta/PER Twin Features
→ Scenario Variables / Funding / Terminal Policy
→ Kill Conditions / Double-count Traps
```

LLM은 새로운 Scanner 보강을 제안할 수 있지만 Planner가 지정한 mandatory scanner를 조용히 제거할 수 없다.

## 2. 입력

- `IndustryDNAProfile`
- `config/archetype_module_registry.yaml`
- `config/sector_adapter_registry.yaml`
- `config/module_requirement_scanner_map.yaml`

Sector Adapter는 산업·사업 특수 증거와 위험을 제공한다. Economic Archetype은 현금흐름 경제학, 허용 모델, 정상화, 민감도 변수와 금지사항을 제공한다. Scanner Map은 이 둘을 조사 병력으로 변환한다.

## 3. 출력

`ModuleRequirementPlan`:

- Segment / Sector Adapter / Archetypes
- Common Units
- Scanner requirements
  - `scanner_id`
  - mandatory 여부
  - interaction group
  - activation origin
- Required evidence / KPI
- Accounting and definition normalization
- Beta Economic-Twin features
- PER Economic-Twin features
- Scenario variables
- Funding scans
- Terminal policies
- Allowed and forbidden methods
- Double-count traps
- Sector special risks
- Kill conditions

## 4. 합성 규칙

1. Adapter의 default/optional Archetype 밖의 조합은 차단한다.
2. Adapter key evidence와 모든 Archetype required evidence를 합친다.
3. 동일 Scanner가 여러 Archetype에서 요구되면 한 번만 배치하되 origin을 모두 남긴다.
4. 어느 경로에서든 mandatory이면 최종 Scanner도 mandatory다.
5. Archetype 계약이 지정한 interaction group을 Sector-risk alias가 덮어쓰지 못한다.
6. Special risk가 아직 없는 Scanner를 추가할 때만 별도 `risk:<risk>` interaction group을 부여한다.
7. 허용 모델이 하나도 없거나 Evidence가 비어 있으면 Fail Closed한다.
8. 모든 Sector Adapter의 default route는 회귀 테스트에서 실제 Plan으로 컴파일돼야 한다.

## 5. Decision Impact 연결

`experiment_specs_from_plan()`은 Plan의 Scanner를 `ModuleExperimentSpec`으로 변환한다.

```text
ModuleRequirementPlan.scanners
→ ModuleExperimentSpec
→ Automatic Ablation
→ Module Impact History
→ Adaptive Loadout
→ next Control Plane mission
```

주의:

- Plan의 mandatory scanner는 **현재 Industry DNA에서 반드시 조사할 병력**이다.
- `mandatory_guardrail`은 **영구적인 통제장치**다.
- 두 개념을 합치지 않는다.
- Control Plane에는 `plan.mandatory_scanner_ids`를 `mission_required_modules`로 전달한다.
- 반복 저영향 이력은 다음 Mission의 조사 강도를 낮출 수 있지만 canonical 삭제는 사용자 승인 사항이다.

## 6. 유지보수 규칙

새 Economic Archetype 추가 시 반드시 함께 갱신한다.

1. `EconomicArchetype` enum
2. Archetype module registry
3. Sector Adapter routing
4. Module Requirement scanner map
5. Unit Contract/impact path when a new Unit is introduced
6. Planner full-coverage tests
7. Decision Impact sensitivity policy

새 Sector Adapter는 최소 하나의 default Archetype, key evidence, 그리고 컴파일 가능한 Scanner loadout을 가져야 한다.

## 7. 구현

- `src/valuation_engine/module_requirements.py`
- `config/module_requirement_scanner_map.yaml`
- `tests/test_module_requirements.py`
- `scripts/validate_module_requirement_plans.py`
