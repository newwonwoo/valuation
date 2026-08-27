# Generic Valuation Engine Design

Status: implementation-ready design  
Scope: actual-unit model, deterministic evaluators, registry, assumption compiler, conflict preservation, company collection plan, OCI primary-evidence migration

## 1. 결정사항

범용 엔진은 다음 단방향 파이프라인을 사용한다.

```text
RawSourceSnapshot
→ EvidenceRecord + ConflictGroup
→ approved Hypothesis + BridgeProposal
→ deterministic AssumptionCompiler
→ CompiledAssumptionSet
→ EvaluatorRegistry
→ industry Evaluator
→ SegmentValuation
→ SOTP Aggregator
→ Audit Gate
→ IntrinsicValue
→ target Market Compare
```

금지되는 우회 경로:

- Evidence에서 Evaluator로 직접 숫자 전달
- LLM이 `CompiledAssumptionSet` 생성
- Evaluator가 Evidence, 기사, 현재주가 또는 source hierarchy 해석
- Registry가 알 수 없는 모델을 generic DCF로 fallback
- 단위 문자열만 비교하고 산술 수행

## 2. Actual-unit 공통 모델

### 2.1 Canonical Measure

원문 숫자는 normalization 단계에서 base unit으로 변환한다. Compiler 이후에는 `조원`, `백만주`, `kMT` 같은 표시 단위를 사용하지 않는다.

```python
class Dimension(str, Enum):
    MONEY = "money"
    MASS = "mass"
    POWER = "power"
    ENERGY = "energy"
    COUNT = "count"
    SHARES = "shares"
    TIME = "time"
    RATIO = "ratio"
    MULTIPLE = "multiple"
    PRICE_PER_UNIT = "price_per_unit"

@dataclass(frozen=True)
class UnitDef:
    code: str
    dimension: Dimension
    base_code: str
    factor_to_base: Decimal

@dataclass(frozen=True)
class Measure:
    amount: Decimal
    unit: str
    as_of: date
    period: PeriodRef | None = None
```

초기 base unit은 `kg`, `W`, `Wh`, `KRW`, `USD`, `shares`, `ratio`, `multiple`, `years`다. `kMT`, `GW`, `GWh`, `KRW_million`, `KRW_billion`은 input unit이며 registry의 고정 factor로 base unit에 변환한다. `KRW_trillion`은 출력 표시 계층에서만 사용한다.

### 2.2 변환 규칙

```python
@dataclass(frozen=True)
class ConversionTrace:
    source_measure_id: str
    source_unit: str
    target_unit: str
    factor: Decimal
    fx_assumption_key: str | None
    output_amount: Decimal
```

- 신규 evaluator의 모든 계산은 `Decimal(str(raw_value))`을 사용한다. 기존 OCI float 계산은 regression adapter에만 남긴다.
- 물리 단위 변환은 고정 registry에서만 수행한다.
- 통화 변환은 반드시 Bridge가 있는 FX assumption을 참조한다.
- `%`는 normalization 단계에서 `ratio`로 변환하고 이후에는 `0..1`만 허용한다.
- 서로 다른 시점 통화는 자동 물가조정하지 않는다.
- flow와 stock, 분기와 연간, 총량과 단위당 값을 자동 혼합하지 않는다.
- 변환 결과는 원본 Measure를 덮어쓰지 않고 trace를 남긴다.

### 2.3 시장가격 역할 분리

Source layer와 사용 목적을 분리한다.

```text
EvidenceRole.INTRINSIC_OPERATING
EvidenceRole.VALUATION_REFERENCE
EvidenceRole.POLICY_CONTEXT
EvidenceRole.TARGET_MARKET_PRICE
```

`source_layer`는 누가 무엇을 발표했는지를 말하고 `evidence_role`은 그 값이 어디에 쓰일 수 있는지를 말한다. Peer multiple, FX, 상장지분 reference는 `VALUATION_REFERENCE`가 될 수 있지만 대상 회사 현재가는 반드시 `TARGET_MARKET_PRICE`다. 마지막 역할은 Audit 전 collection plan, Compiler, Evaluator, SOTP 입력에서 모두 금지한다.

상장지분 reference는 asset ID, 평가일, ownership, haircut policy가 있는 별도 Bridge를 통과할 때만 SOTP 입력으로 허용한다. 대상 회사 현재가는 계속 intrinsic input으로 금지한다.

## 3. Compiled Assumption 계약

```python
class ValueLayer(str, Enum):
    CORE = "core"
    EXPECTED = "expected"
    VERIFIED_BULL = "verified_bull"

@dataclass(frozen=True)
class AssumptionSpec:
    key: str
    evaluator_key: str
    required: bool
    canonical_unit: str
    allowed_roles: tuple[EvidenceRole, ...]
    allowed_source_layers: tuple[EvidenceSourceLayer, ...]
    allowed_statement_kinds: tuple[str, ...]
    allowed_transforms: tuple[str, ...]
    freshness_rule_id: str
    domain: NumericDomain
    core_eligible: bool

@dataclass(frozen=True)
class CompiledAssumption:
    key: str
    scenario_id: str
    measure: Measure
    bridge_id: str
    evidence_ids: tuple[str, ...]
    hypothesis_id: str
    economic_path_id: str
    compiler_version: str
    transform_id: str
    transform_trace_id: str
    input_evidence_hash: str
    value_layer: ValueLayer
    calibration_status: str | None

@dataclass(frozen=True)
class CompiledAssumptionSet:
    target_id: str
    route_id: str
    scenario_ids: tuple[str, ...]
    assumptions: tuple[CompiledAssumption, ...]
    ledger_snapshot_hash: str
    compiler_report_id: str
```

Evaluator는 `CompiledAssumption`이 감싼 숫자만 입력받는다. raw float, Evidence value, Bridge proposal value는 evaluator 경계를 통과하지 못한다. 기존 `Scenario` 객체는 OCI legacy adapter 내부에서만 유지한다.

## 4. Evidence→Assumption Compiler

### 4.1 입력

```python
@dataclass(frozen=True)
class CompilationRequest:
    company_profile_id: str
    routing_decision: RoutingDecision
    model_specs: tuple[ModelSpec, ...]
    ledger_snapshot: EvidenceLedgerSnapshot
    conflict_groups: tuple[ConflictGroup, ...]
    approved_hypotheses: tuple[HypothesisRecord, ...]
    bridge_proposals: tuple[BridgeRecord, ...]
    prior_assumptions: CompiledAssumptionSet | None
```

출력은 둘 중 하나다.

```text
CompilationSucceeded(assumption_set, manifest)
CompilationBlocked(findings, missing_requirements, conflict_ids)
```

`CompilationManifest`는 run ID, input snapshot hash, compiled set hash, selected/conflicting/excluded Evidence IDs, transform execution trace, warnings, blocking findings, compiler version을 저장한다.

부분 성공 assumption을 저장할 수는 있지만 evaluator에 전달하면 안 된다.

Compiler state:

```text
RECEIVED → SPEC_BOUND → PROVENANCE_VALIDATED → CONFLICT_CHECKED
→ TRANSFORMED → UNIT_SCOPE_VALIDATED → PRIOR_RECONCILED
→ SCENARIO_BOUND → COMPILED
```

어느 단계든 blocking finding이 생기면 `COMPILATION_BLOCKED`로 종료하고 Run 전체를 `VALUATION_BLOCKED`로 저장한다. evaluator와 market loader는 호출하지 않는다.

Researcher는 `BridgeRecord`를 제안할 수 있지만 Compiler 결과를 직접 쓸 수 없다.

Bridge proposal의 `new_value`는 권위 있는 산출값이 아니다. Compiler는 등록된 deterministic transform으로 값을 다시 계산하고 proposal 값과 다르면 차단한다.

```python
@dataclass(frozen=True)
class AssumptionTransform:
    transform_id: str
    version: str
    input_units: tuple[str, ...]
    output_unit: str
    calculate: Callable[[tuple[Measure, ...], TransformParameters], Measure]
```

초기 transform은 `identity_observation`, `unit_conversion`, `annualization`, `ratio`, `product`, `weighted_average`, `ramp_scaled_money`, `date_math`, `scenario_policy_lookup`, `probability_event_update`로 제한한다. `ramp_scaled_money`는 검토가 끝난 기준 현금흐름 경로·정상상태 상한·기준 가동기간·현재 가동기간을 입력받아 `min(정상상태 상한, 기준 경로 × 기준 가동기간 / 현재 가동기간)`으로 연도별 증분 현금흐름을 다시 계산한다. 가동기간은 추적용 메타데이터로만 둘 수 없으며, 생산능력 반영 계약에서 이 transform을 선언하면 해당 가정 key와 transform ID가 실제 Compiler 요청에 일치하지 않을 때 차단한다. 임의 Python expression, `eval`, LLM 산술은 금지한다. Multiple·discount rate처럼 직접 관측되지 않는 값은 versioned model policy table과 Bridge rationale를 함께 요구한다.

### 4.2 결정론적 컴파일 순서

1. Ledger snapshot과 content hash를 고정한다.
2. unresolved blocking conflict가 있는 metric을 차단한다.
3. Route별 required evidence와 required assumption key를 확정한다.
4. Bridge의 Evidence/Hypothesis/kill condition/verification event를 검증한다.
5. 정책값 단독 ASP, target price leakage, company plan의 realized 승격을 차단한다.
6. 등록된 transform으로 assumption 값을 재계산하고 proposal 값과 대조한다.
7. Measure를 evaluator canonical unit으로 변환한다.
8. scenario별 필수 key 완전성과 probability 합계를 검사한다.
9. 동일 `economic_path_id`의 value contribution 계획을 검사한다.
10. prior assumption의 old value와 snapshot hash를 optimistic lock으로 검사한다.
11. prior assumption 대비 변경내역을 작성한다.
12. immutable `CompiledAssumptionSet`과 `CompilationManifest`를 출력한다.

### 4.3 Fail-closed 조건

- required evidence 또는 required assumption 누락
- unresolved blocking conflict
- Bridge 없는 assumption
- unit/period/scope 불일치
- target market price 참조
- 정책가격만으로 기업 ASP 생성
- 같은 economic path가 operating/option/SOTP에 중복 예정
- probability 변경에 신규 Evidence event 없음
- critical stale Evidence 또는 동일 tier의 unresolved critical conflict
- transform 재계산값과 Bridge proposal 값 불일치
- prior value/hash 불일치
- NaN, Infinity, 음수 capacity/shares, domain 위반
- kill condition 발생 후 probability/assumption/scenario downgrade 누락
- legacy assumption을 live evidence로 위장

Fail 시 마지막 성공 AssumptionSet을 자동 재사용하지 않는다. 해당 Run을 `VALUATION_BLOCKED`로 저장하고 사용자가 명시적으로 이전 valuation을 조회할 때만 last-good 결과를 별도 표시한다.

## 5. Conflict preservation

같은 metric의 값이 다르면 낮은 등급 값을 삭제하지 않는다. `supersedes`는 같은 source series의 공식 정정·개정에만 사용한다.

```python
@dataclass(frozen=True)
class SemanticKey:
    target_id: str
    segment_id: str
    metric: str
    statement_kind: str  # realized | plan | policy | estimate
    period: PeriodRef
    scope: str
    accounting_basis: str

@dataclass(frozen=True)
class ConflictGroup:
    id: str
    semantic_key: SemanticKey
    evidence_ids: tuple[str, ...]
    normalized_unit: str
    tolerance: Decimal
    conflict_type: str
    resolution_status: str  # open | resolved | scoped_split | corroborated
    selected_evidence_ids: tuple[str, ...]
    excluded_evidence_ids: tuple[str, ...]
    resolution_method: str  # authority | official_revision | manual | none
    rationale: str
    blocking: bool
    resolution_hash: str
```

Evidence에는 `document_id`, `locator`, `extract_hash`, `semantic_key`, `statement_kind`, `scope`, `revision_of`를 추가한다.

Resolver 순서:

1. unit·scope 정규화가 불가능하면 데이터 품질 오류로 차단한다.
2. 허용오차 내 동일값은 `corroborated`로 보존한다.
3. 공식 amended filing chain은 최신값을 선택하되 과거값을 삭제하지 않는다.
4. source hierarchy 자동 선택은 SemanticKey가 완전히 같을 때만 허용한다.
5. 동일 tier의 상충 또는 정의 불명확은 `open`; critical이면 compile을 차단한다.
6. plan/realized, 연결/별도, gross/net, nameplate/effective capacity는 `scoped_split`; 평균하지 않는다.

우선순위가 높은 출처를 선택해도 반대 Evidence는 Hypothesis의 `contradicting_evidence_ids`와 ConflictGroup에 남긴다. 입력 순서가 달라도 selected IDs와 hash가 같아야 한다.

## 6. Evaluator 공통 계약

```python
@dataclass(frozen=True)
class InputTrace:
    assumption_key: str
    bridge_id: str
    evidence_ids: tuple[str, ...]
    economic_path_id: str

@dataclass(frozen=True)
class ModelValue:
    measure: Measure
    trace: InputTrace

@dataclass(frozen=True)
class ModelKey:
    industry: IndustryModel
    method: str
    version: str

@dataclass(frozen=True)
class ValuationContext:
    company_id: str
    segment_id: str
    scenario_id: str
    valuation_date: date
    reporting_currency: str
    fx_quotes: tuple[FxQuote, ...] = ()

class DeterministicEvaluator(Protocol[InputT]):
    key: ModelKey
    input_type: type[InputT]
    output_kind: ValueKind

    def required_assumptions(self) -> tuple[AssumptionRequirement, ...]: ...
    def validate(self, inputs: InputT, context: ValuationContext) -> tuple[ModelIssue, ...]: ...
    def evaluate(self, inputs: InputT, context: ValuationContext) -> SegmentValuation: ...
    def stress_cases(self, inputs: InputT) -> tuple[StressCase, ...]: ...

@dataclass(frozen=True)
class SegmentValuation:
    contribution_id: str
    segment_id: str
    scenario_id: str
    value_kind: ValueKind  # ENTERPRISE_VALUE | EQUITY_VALUE
    value: Measure
    components: tuple[ValuationComponent, ...]
    economic_path_ids: tuple[str, ...]
    evaluator_id: str
    evaluator_version: str
```

Evaluator는 `세그먼트 1개 × 시나리오 1개`만 계산한다. scenario probability 합산, diluted shares, 주당가치는 별도 aggregator가 처리한다.

Evaluator는 순수 함수여야 하며 네트워크, 파일, Evidence 해석, 현재가 loader, LLM을 호출하지 않는다. `ValuationContext`에는 current price, target price, scenario probability 필드를 두지 않는다.

공통 blocking error:

- 필수 input·Bridge·Evidence trace 누락
- target market price 또는 금지된 Evidence role 유입
- unit dimension 불일치, FX trace 누락, non-finite 값
- 음수 capacity/shares 또는 ratio 범위 위반
- 같은 `evidence_id + economic_path_id` 중복
- output value kind 불명확
- terminal multiple 방식에서 terminal EBITDA ≤ 0
- DCF에서 `discount_rate <= terminal_growth`
- evaluator/version/input type 불일치

## 7. Evaluator Registry

```python
@dataclass(frozen=True)
class EvaluatorRegistration:
    key: ModelKey
    input_type: type
    output_kind: ValueKind
    evaluator: DeterministicEvaluator
```

Registry key는 `(industry, allowed_method, version)`이다.

규칙:

- exact match만 허용하고 generic fallback은 금지한다.
- 같은 key 중복 등록은 startup FAIL이다.
- `ModelSpec.allowed_methods`와 Registry를 시작 시 상호 검증한다.
- state/run에는 evaluator ID, version, assumption hash를 저장한다.
- evaluator version 변경은 regression fixture와 audit explanation을 요구한다.
- company name/ticker별 evaluator 등록을 금지한다.
- `generic`, `consumer_retail`, `construction_shipbuilding`은 evaluator가 생기기 전까지 `MODEL_NOT_REGISTERED`로 차단한다.

초기 canonical key:

```text
commodity_materials / throughput_exit_multiple / 1
order_equipment / backlog_conversion_exit_multiple / 1
energy_infrastructure / asset_npv_sotp / 1
software_platform / arr_fcff_dcf / 1
financials / residual_income / 1
pharma_bio / rnpv / 1
holding_company / sotp / 1
operating_multi_segment / company_sum_parts / 1
```

현재 `router.py`의 method name은 alias migration table로만 받아들이고 새 run에는 canonical key를 저장한다.

| 현재 alias | canonical method |
|---|---|
| `price_volume_margin_exit_multiple` | `throughput_exit_multiple` |
| `backlog_normalized_ebitda_dcf` | `backlog_conversion_exit_multiple` |
| `asset_npv_sotp` | `asset_npv_sotp` |
| `revenue_grossprofit_fcf` | `arr_fcff_dcf` |
| `pb_roe_residual_income` | `residual_income` |
| `rnpv` | `rnpv` |

`IndustryModel.OPERATING_MULTI_SEGMENT`를 추가한다. 이는 segment evaluator가 아니라 GE Vernova 같은 비지주 다사업 운영회사의 company aggregator route다.

## 8. 초기 deterministic evaluator

### 8.1 `throughput_exit_multiple_v1`

```text
saleable_quantity = annual_capacity × utilization × yield
unit_margin = realized_ASP − variable_cost_per_unit − other_cash_cost_per_unit
EBITDA = saleable_quantity × unit_margin − fixed_cost
EV = normalized_EBITDA × exit_multiple
PV_EV = EV / (1 + discount_rate)^terminal_years
```

필수 입력: capacity, utilization, yield, realized ASP, unit cash cost, other cash cost, fixed cost, multiple, terminal years, discount rate. 정책가격은 입력 key가 아니다. Capacity가 이미 effective/saleable 기준이면 yield를 재적용하지 않는다.

### 8.2 `backlog_conversion_exit_multiple_v1`

backlog cohort별 conversion period와 revenue-recognition method를 강제한다.

```text
recognized_revenue_t
  = opening_firm_backlog × conversion_rate_t
  + new_firm_orders_t × same_period_conversion_t
  + service_revenue_t
EBITDA = recognized_revenue × normalized_margin
terminal_EV = normalized_EBITDA × cycle_normalized_multiple
```

누적 인식액은 opening backlog + new firm orders를 넘을 수 없다. MOU/framework/IDIQ ceiling은 firm backlog와 분리한다. 계약부채는 수요 신뢰도 Evidence이지 매출에 다시 더하지 않는다. DCF는 CAPEX, NWC, 세금 schedule이 있는 별도 v2로 등록한다.

### 8.3 `energy_asset_npv_v1`

프로젝트별 capacity, COD, contract term, price/escalator, utilization, operating cost, CAPEX, funding을 사용한다. Project NPV와 제조 backlog value를 같은 path로 합산하지 않는다.

```text
energy_t = MW × 8,760 × net_capacity_factor
revenue_t = energy_t × contracted_tariff + capacity_payment
FCFF_t = revenue_t − opex − maintenance_capex − tax − delta_NWC
project_EV = Σ FCFF_t / (1+r)^t − pre_COD_construction_capex_PV
```

`net_capacity_factor`에 availability가 포함되면 다시 곱하지 않는다. Project debt는 EV가 아니라 SOTP equity bridge에서 차감한다. Levered equity cash flow와 unlevered FCFF를 혼용하면 차단한다.

### 8.4 `arr_fcff_dcf_v1`

```text
ending_ARR_t = beginning_ARR_t × NRR_t + new_ARR_t
revenue_t = average_ARR_t + non_recurring_revenue_t
EBIT_t = revenue_t − COGS_t − operating_expense_t − D&A_t
FCFF_t = EBIT_t × (1−tax_rate) + D&A_t − CAPEX_t − delta_NWC_t
EV = forecast_FCFF_PV + terminal_value_PV
```

RPO는 revenue timing evidence이고 별도 asset value가 아니다. NRR에 expansion/churn이 포함됐다면 같은 expansion을 다시 매출에 더하지 않는다. Stock compensation과 capitalized software cost policy를 명시한다.

### 8.5 `residual_income_v1`

```text
net_income_t = ROE_t × beginning_book_value_t
ending_BV_t = beginning_BV_t + net_income_t − distribution_t
residual_income_t = (ROE_t − cost_of_equity_t) × beginning_BV_t
equity_value = current_BV + PV(residual_income) + PV(terminal_residual_income)
```

출력은 처음부터 Equity다. 순차입금 차감을 금지하고 규제자본·credit cost kill condition을 요구한다. 시장 P/B는 intrinsic input이 아니다.

### 8.6 `rnpv_v1`

```text
program_rNPV
  = Σ [P(reach_stage_t) × (−development_cost_t)
     + P(approved_by_t) × commercial_FCFF_t] / (1+r)^t
```

단계별 누적확률은 시간에 따라 증가할 수 없다. 회사 전체 scenario probability와 파이프라인 기술확률을 중복 곱하지 않는다. Upfront, milestone, cash, funding gap의 중복을 차단한다.

### 8.7 `holding_sotp_v1`

SOTP는 segment evaluator가 아니라 aggregator다.

```text
if segment output = EV:
    segment_equity_100pct = EV − segment_net_debt − segment_senior_claims
else:
    segment_equity_100pct = output_equity

attributable_equity = segment_equity_100pct × economic_ownership
NAV = Σ attributable_equity + parent_non_operating_assets
parent_equity = NAV − holding_net_debt − parent_level_liabilities
```

규칙:

- subsidiary net debt는 ownership 적용 전에 차감한다.
- parent debt는 합산 후 한 번만 차감한다.
- ownership multiply와 NCI 별도 차감을 같은 segment에 동시에 적용하지 않는다.
- subsidiary debt를 consolidated parent debt에서 다시 차감하지 않는다.
- EV와 Equity output 혼합 시 각 segment의 bridge type을 명시한다.
- listed stake, operating segment, strategic option의 동일 asset ID 중복을 차단한다.
- 관행적 holding discount는 v1에서 금지한다. 실제 세금·구조비용·유동성 비용이 별도 현금흐름 Evidence와 Bridge를 가질 때만 이후 version에서 허용한다.

### 8.8 `operating_company_sum_parts_v1`

GE Vernova처럼 법적 지주사가 아닌 다사업 운영회사는 holding SOTP를 쓰지 않는다.

```text
operating_EV = Σ segment_EV + PV(corporate_costs)
parent_equity = operating_EV
              + parent_non_operating_assets
              − consolidated_net_debt
              − pension_and_other_parent_claims
```

모든 operating segment ownership은 기본 100%다. 지주사 할인과 NCI를 자동 적용하지 않는다. Segment EBITDA 합계와 consolidated cash flow를 대사하고 corporate CAPEX, tax, NWC를 한 번만 반영한다.

## 9. Company collection plan

```python
@dataclass(frozen=True)
class CompanyIdentity:
    canonical_id: str
    legal_name: str
    aliases: tuple[str, ...]
    ticker: str
    exchange: str
    jurisdiction: str
    reporting_currency: str
    fiscal_year_end: str
    dart_corp_code: str | None
    sec_cik: str | None

@dataclass(frozen=True)
class CollectionRequirement:
    requirement_id: str
    evaluator_key: str
    segment_id: str
    metric: str
    mandatory: bool
    accepted_roles: tuple[EvidenceRole, ...]
    accepted_layers: tuple[EvidenceSourceLayer, ...]
    accepted_statement_kinds: tuple[str, ...]
    accepted_units: tuple[str, ...]
    period_scope: str
    freshness_rule_id: str
    adapter_priority: tuple[str, ...]
    coverage_rule: str
    blocking_policy: str

@dataclass(frozen=True)
class CollectionTask:
    task_id: str
    requirement_ids: tuple[str, ...]
    adapter_id: str
    entity_id: str
    document_types: tuple[str, ...]
    date_range: tuple[date, date]
    extractor_profile: str
    max_attempts: int

@dataclass(frozen=True)
class CompanyCollectionPlan:
    plan_id: str
    version: str
    company: CompanyIdentity
    routing_hash: str
    requirements: tuple[CollectionRequirement, ...]
    tasks: tuple[CollectionTask, ...]
    stop_conditions: tuple[str, ...]
```

생성 순서:

1. 회사·ticker·jurisdiction·연결범위를 확정한다.
2. holding 여부와 segment 후보만 우선 판별한다.
3. 각 route의 `ModelSpec.required_evidence`로 required metric을 합친다.
4. 국가별 source hierarchy에 따라 SourceTask를 만든다.
5. raw document, fetch time, content hash를 보존한다.
6. parser가 EvidenceRecord 후보를 만들고 사람이 읽을 source locator를 남긴다.
7. critical required metric이 없으면 Researcher 전에 collection blocked 또는 targeted retry로 보낸다.

Holding company는 parent requirement(ownership, parent debt/cash, NCI, listed stakes)와 segment별 delegated subplan을 모두 가져야 한다.

Adapter 계약:

```python
class SourceAdapter(Protocol):
    adapter_id: str
    version: str
    def capabilities(self) -> AdapterCapabilities: ...
    def resolve_entity(self, identity: CompanyIdentity) -> ResolvedEntity: ...
    def collect(self, task: CollectionTask) -> CollectionBatch: ...

@dataclass(frozen=True)
class RawSourceDocument:
    document_id: str
    adapter_id: str
    publisher: str
    source_ref: str
    source_layer: EvidenceSourceLayer
    published_at: datetime
    effective_period: PeriodRef
    retrieved_at: datetime
    mime_type: str
    content_sha256: str
    immutable_storage_ref: str
```

수집기는 값을 해석하거나 Evidence/Bridge/Assumption을 만들지 않는다. 흐름은 `RawSourceDocument → Extractor → EvidenceCandidate → Normalizer → EvidenceRecord`다. Page/table/filing-section locator와 extract hash가 없는 candidate는 live ledger에 진입하지 못한다.

시장가격 loader는 intrinsic adapter registry와 별도 모듈에 둔다. Pre-audit plan에 `TARGET_MARKET_PRICE` task가 존재하면 plan validation 자체가 실패한다.

Plan 상태는 `DRAFT → ROUTED → VALIDATED → COLLECTING → COLLECTED → NORMALIZED → COVERAGE_CHECK → READY`다. Gap/conflict가 있으면 `RESEARCH_GAP → DELTA_PLAN`으로 최대 3회만 반복한다. Adapter retry count와 Research Round count는 별도다.

## 10. OCI primary-evidence replacement

Legacy key를 한 번에 삭제하지 않고 assumption별로 교체한다.

전환 모드:

```text
LEGACY_REGRESSION  # 기존 Excel 숫자로 회귀만 유지, live 사용 금지
PRIMARY_SHADOW     # primary Evidence로 새 assumption을 계산하되 legacy와 병렬 비교
LIVE_PRIMARY       # audit와 review를 통과한 primary assumption만 실제 valuation에 사용
```

`PRIMARY_SHADOW` 결과는 목표가로 출력하지 않는다. 모든 critical key가 shadow 비교와 audit를 통과한 뒤 run 단위로 `LIVE_PRIMARY`에 승격한다. key별 혼합 승격으로 같은 scenario 안에 기준일과 경제세계가 뒤섞이는 것을 금지한다.

| Legacy assumption | 필수 대체 Evidence | Source | 차단 조건 |
|---|---|---|---|
| `company.shares` | 발행주식수·자기주식·잠재희석 | DART/분기보고서 | 기준일/분모정책 불일치 |
| `poly_capacity_kmt` | 현재/목표 capacity 분리 | OCI IR + filing | 계획을 실현로 표시 |
| `wafer_capacity_gw` | 법인·단계별 capacity | OCI IR | ownership/scope 불명 |
| `poly ASP` | 실제 판매가격 또는 매출/판매량 | filing/IR | 정책가격만 존재 |
| `poly cash cost` | 공시 원가·회사 공식 범위 | filing/IR | 외부 추정만 존재 |
| `utilization` | 생산·판매·가동 데이터 | filing/IR | capacity 분모 불명 |
| `wafer EBITDA/W` | 실제 segment economics | filing/IR | 보조금 포함 여부 불명 |
| `wafer_economic_share` | 법인 ownership | DART | EBITDA 단계 적용 또는 NCI 이중차감 |
| `net debt` | scenario 시점 debt/cash/funding gap | filing + Bridge | gross CAPEX 중복 |
| `other_business_pv` | asset별 evaluator output | segment evidence | opaque lump sum 유지 |
| `multiple/discount` | peer/mid-cycle 또는 risk Bridge | external + model policy | 현재가 역산 |
| `probability` | 신규 observable event | primary evidence | 가격 맞춤 조정 |

교체 완료 기준은 모든 active assumption이 `LEGACY_REGRESSION` Evidence를 참조하지 않는 것이다. 교체 전후 OCI 회귀 차이는 자동 유지하지 않는다. 차이가 발생하면 Evidence 변화인지 formula 변화인지 분리해 설명한다.

실현되지 않은 AI/Space 등 option은 근거 부족 시 0원 확정이 아니라 `UNVALUED_NOT_ZERO`로 보고서에 분리한다. Binding contract, volume, margin, cash-flow Evidence가 없으면 Core나 Verified Bull에 넣지 않는다.

## 11. 최소 첫 milestone과 구현 순서

### M1 — OCI polysilicon primary shadow slice

가장 작은 실전 milestone은 live network 수집이나 전체 OCI 재평가가 아니다.

```text
frozen OCI primary-source fixture
→ conflict-aware ledger snapshot
→ deterministic Compiler
→ actual-unit commodity input
→ exact Registry
→ throughput evaluator
→ legacy Base-case shadow reconciliation
```

범위:

- polysilicon Base scenario 한 개만 지원한다.
- target market price, generic SOTP, live DART adapter는 포함하지 않는다.
- Shadow 결과는 목표가로 출력하지 않는다.
- Bridge proposal 값을 transform이 재계산한다.
- Critical Evidence 하나를 제거하면 valuation이 생성되지 않는다.

Merge gate:

- `kMT→kg`, `USD/kg×kg×KRW/USD`, annualization identity test
- 동일 snapshot의 byte-stable compile/result hash
- policy-only Evidence가 ASP를 만들지 못함
- unresolved critical conflict가 compile 차단
- raw float/unknown transform/Bridge 누락 차단
- legacy OCI Base polysilicon math와 설명 가능한 reconciliation
- market loader 호출 0회

### M2 — Full OCI commodity shadow

- wafer를 포함한 Bear/Base/Bull actual-unit assumptions
- Scenario completeness, probability integrity, economic-path audit
- 기존 OCI 4-scenario와 기대가치 회귀를 compatibility adapter에서 계속 유지

### M3 — Generic SOTP promotion path

- generic aggregator와 explicit EV→Equity bridge
- ownership/debt/NCI/listed-stake double-count fixtures
- OCI opaque `other_business_pv`를 asset별 contribution으로 분해
- 분해 완료 전 `LIVE_PRIMARY` 승격 금지

### M4 — Collection contracts and live adapters

- CompanyCollectionPlan, SourceAdapter, Extractor 경계
- frozen DART/IR fixtures 후 live DART/SEC/IR adapter
- content hash, locator, idempotent dedupe, delta collection

### M5 — Additional evaluators and companies

- Oracle software DCF
- operating-company segment aggregator와 GE Vernova
- order equipment + energy asset + JV guards로 Bloom
- financials와 pharma는 별도 accounting audit 이후

각 M은 unit/integrity/regression/adversarial test가 모두 통과해야 다음 단계로 간다.

## 12. 최고위험 요구사항

| 우선순위 | 요구사항 | 실패 시 결과 |
|---|---|---|
| P0 | Compiler가 Bridge proposal 숫자를 재계산 | LLM 숫자가 deterministic input으로 위장 |
| P0 | Source layer와 Evidence role 분리 | Target price leakage 또는 합법적 reference 오차단 |
| P0 | Scope/period/statement kind를 SemanticKey에 포함 | plan/realized, segment/consolidated 혼합 |
| P0 | Actual-unit + traced FX만 evaluator 허용 | 1,000/1,000,000/통화 오류 |
| P0 | EV→segment equity→ownership→parent 조정 순서 | debt/NCI/ownership 이중 차감 |
| P0 | Unsupported route/evaluator는 fallback 없이 차단 | 전혀 다른 산업 공식을 조용히 적용 |
| P0 | Shadow와 Live 상태를 run 단위로 분리 | legacy와 primary 기준일이 한 valuation에 혼합 |
| P1 | Adapter는 raw document까지만 책임 | 수집기에서 해석·가정이 섞여 감사 불가 |
| P1 | Opaque other-business value 금지 | operating/option/listed stake 중복 |
| P1 | Kill condition 발생을 Compiler가 확인 | 깨진 Thesis 확률·가정이 그대로 유지 |

## 13. 필수 contract tests

- Unit: exact `kMT→kg`, `GW→W`, `GWh→Wh`, `million/billion→ones`, traced `USD→KRW`, incompatible unit rejection.
- Compiler: unknown key/transform, missing Evidence/Hypothesis/Bridge, stale/conflict/scope/prior-hash mismatch를 모두 차단.
- Isolation: policy value 변경만으로 ASP/value 불변, target price 객체는 intrinsic 타입에 진입 불가.
- Determinism: Evidence 입력 순서와 무관하게 canonical hash/value 동일.
- Registry: exact resolve, duplicate/version mismatch 거부, unsupported industry fallback 금지.
- Evaluator sensitivity: price/volume/utilization/margin 상승, cost/CAPEX/discount 상승의 모델별 기대 방향.
- SOTP: segment debt→ownership→parent debt golden test, NCI/ownership 및 listed stake/operating value 중복 차단.
- CAPEX: expansion EBITDA + full CAPEX + funding gap 동일 path 중복 차단.
- Workflow: compile/audit 실패 시 value suppression, market loader 미호출, blocked run non-promotion.
- Regression: OCI legacy 4 scenarios와 expected value ±1원 유지; primary shadow는 별도 golden fixture 유지.

## 14. 설계 완료 기준

다음 질문에 모두 YES여야 한다.

- 모든 evaluator input이 actual-unit Measure인가?
- 모든 assumption이 deterministic Compiler 산출물인가?
- Registry가 exact evaluator/version을 선택하는가?
- Route에 evaluator가 없으면 valuation을 차단하는가?
- segment output의 EV/Equity 성격이 명시되는가?
- SOTP가 debt, ownership, NCI를 각각 정확히 한 번만 반영하고 v1 holding discount를 금지하는가?
- conflict와 반대 Evidence가 삭제되지 않는가?
- target price가 Compiler와 Evaluator 타입에 존재하지 않는가?
- legacy Evidence가 live valuation에서 자동 차단되는가?
- blocked compilation이 부분 assumption을 evaluator에 넘기지 않는가?
- operating multi-segment와 legal holding aggregator가 구분되는가?
