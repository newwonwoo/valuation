# Auto Investment Research OS

`분석시작 <기업>`을 Evidence-first 연구 흐름과 결정론적 밸류에이션으로 연결하는 저장소입니다.

## 현재 구현 수준

실행 CLI의 범용 live engine은 아직 v0.3-alpha OCI홀딩스 수직 슬라이스를 보존합니다. 방법론/순수 계산 계약은 **RocketSLA v0.4**까지 확장되었습니다. 구현되지 않은 live adapter를 실행된 것처럼 표시하면 안 됩니다.

### v0.3 legacy/live-workflow baseline
- Evidence → Hypothesis → Bridge → Assumption 추적성
- current-price/market-comparison intrinsic leakage 차단
- industry routing, Blind Red Team, 최대 3회 Research Loop
- Audit 실패 시 `VALUATION BLOCKED`
- immutable run history / atomic last-good state
- OCI v1.1 회귀값 보존

### v0.4 methodology + pure-function contracts
- **Funded Demand / Upstream Funding & Constraint Ladder**
- **4-Level Hierarchical Bottom-up Beta**: L1 Broad Sector → L2 Industry → L3 Risk-Driver Subindustry → L4 Economic Twins; unlever → partial pool → target relever
- **WACC Validation Engine**: 통화 일치 Risk-free Rate, market ERP, exposure-adjusted country risk, marginal Cost of Debt, market-value Target D/E, Terminal consistency
- **Customer Advances Gate**: 선수금은 FCFF/ROIC에 먼저 반영하고 실제 신용위험이 개선될 때만 WACC에 2차 반영
- **Hierarchical Warranted PER Engine v1.0**: Core Fundamental / Expansion-Adjusted / Market-Realization PER; DCF–PER Assumption Consistency; residual-not-raw-PER pooling
- **Cross-Method Double-Count Gate**: 동일 economic path를 Beta/WACC/FCF/PER에 중복 자본화하지 않음
- **Street Gap Analyzer / Consensus Reconciliation**: Intrinsic Value Freeze 이후에만 Street 목표가/추정치 로드
- `Policy Intent ≠ Transmission Effect`

## 설치와 검증

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
```

OCI legacy deterministic core:

```bash
.venv/bin/valuation-engine examples/oci/company.yaml
```

Research OS vertical slice:

```bash
.venv/bin/valuation-engine "분석시작 OCI홀딩스" \
  --config examples/oci/company.yaml \
  --state-root ../valuation-vault-local
```

실제 Thesis/Evidence/Position/API key/유료 증권사 원문은 public repo에 커밋하지 않습니다.

## 핵심 구조

```text
.agents/skills/valuation-analysis/SKILL.md  # canonical runtime contract
SKILL.md                                    # byte-identical compatibility copy
AGENTS.md                                   # project/coding gates
01_Rocketesla_Insight_Valuation_Framework.md
03_valuation_engine_schema.yaml

docs/
  V04_ROCKETSLA_EXTENSION.md                # v0.4 methodology + rationale/limits
  GENERIC_ENGINE_DESIGN.md
  LIVE_VALIDATION_AND_CALIBRATION.md

src/valuation_engine/
  risk.py       # Hierarchical Beta + Blume/Vasicek helpers
  wacc.py       # WACC / customer-funded-growth / terminal gates
  per.py        # Hierarchical Warranted PER + DCF-PER consistency
  funding.py    # Upstream funding ladder contracts
  street.py     # Street consensus/gap arithmetic
  ...           # existing v0.3 engine/workflow modules

tests/
  test_v04_contracts.py
  ...
```

## 실행 불변조건

1. 현재주가와 Street 목표/추정은 Intrinsic Value Freeze 전 intrinsic input이 아닙니다.
2. Street에서 새 사실을 발견하면 primary/독립 Evidence로 재검증한 **새 run**을 시작합니다.
3. 모든 valuation assumption은 Bridge/economic path를 가져야 합니다.
4. 정책가격은 실제 ASP/물량/원가/자금조달 전달경로 없이 가치로 직접 들어가지 않습니다.
5. Beta는 fixed peer/level 평균이 아니라 risk-driver hierarchy와 partial pooling을 사용합니다.
6. WACC는 통화·자본구조·한계조달비용·Terminal 가정이 일관되어야 합니다.
7. 선수금은 FCFF/ROIC에 먼저 반영하며, WACC 인하는 별도 credit evidence가 필요합니다.
8. PER는 positive normalized forward EPS를 사용하고 Core DCF와 경제가정을 공유합니다.
9. Expansion PER는 committed/pre-invested evidence가 있을 때만 성장기간을 늘립니다.
10. Market-Realization PER는 raw peer P/E가 아니라 `ln(Market PER / Fundamental PER)` residual을 계층적으로 pooling합니다.
11. Beta/WACC/FCF/PER의 동일 질적 장점은 `economic_path_id` 없이 중복 자본화하지 않습니다.
12. Audit 실패/critical unresolved issue는 valuation과 market comparison을 차단합니다.
13. 기존 OCI 회귀값은 의도적 모델 변경이 없는 한 ±1원 이내로 유지합니다.

## 방법론의 위치

v0.4는 **academically grounded engineering synthesis**로 정의합니다. Blume/Vasicek, non-synchronous beta correction, unlever/relever, standard WACC consistency, forward/fundamental multiple literature 등 기존 기반과 RocketSLA 고유의 L1→L4 Economic-Twin taxonomy, customer-advance WACC transmission, three-layer Warranted PER, residual pooling 및 fail-closed audit orchestration을 구분합니다.

상세 근거·실무적 가치·한계는 [docs/V04_ROCKETSLA_EXTENSION.md](docs/V04_ROCKETSLA_EXTENSION.md)를 기준으로 합니다.
