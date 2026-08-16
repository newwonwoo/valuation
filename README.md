# Auto Investment Research OS

`분석시작 <기업>`을 Evidence-first 연구 흐름과 결정론적 밸류에이션으로 연결하는 저장소입니다.

## 현재 구현 수준

v0.3-alpha는 OCI홀딩스 v1.1 모델을 보존한 **오프라인 수직 슬라이스**입니다.

- 구조화 Evidence Ledger와 append-only supersession
- Evidence → Hypothesis → Bridge → Assumption 추적성
- `market_comparison` 및 정책가격의 intrinsic input 누출 차단
- holding-company 우선 routing과 segment model contract
- Bear/Base/Bull 정합성 및 `UNCALIBRATED` 확률 표시
- Blind Red Team 입출력 계약과 최대 3회 Research Loop
- Audit 실패 시 `VALUATION BLOCKED`, 가치·현재가 출력 금지
- 성공 Run만 current state로 atomic promotion
- 실패 Run을 포함한 immutable run history와 Thesis Delta
- OCI v1.1 회귀값 보존

아직 실시간 DART/SEC/IR/정책 수집과 실제 LLM Researcher/Red Team 호출은 연결되지 않았습니다. 현재 기본 실행은 구조·감사·상태 전이를 검증하는 fixture이며 최신 투자 분석으로 사용하면 안 됩니다.

## 설치와 검증

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
```

OCI 결정론적 코어:

```bash
.venv/bin/valuation-engine examples/oci/company.yaml
```

Research OS 수직 슬라이스:

```bash
.venv/bin/valuation-engine "분석시작 OCI홀딩스" \
  --config examples/oci/company.yaml \
  --state-root ../valuation-vault-local
```

`--state-root`에는 private 경로를 사용하십시오. 실제 Thesis, Evidence, Position 규칙, API key를 이 public 저장소에 커밋하지 않습니다.

## 구조

```text
.agents/skills/valuation-analysis/SKILL.md  # Codex 실행 계약
src/valuation_engine/
  records.py       # Evidence/Hypothesis/Bridge/Assumption/Run 타입
  ledger.py        # Evidence 저장·traceability gate
  provenance.py    # OCI legacy fixture migration trace
  research.py      # Researcher/Blind Red Team/3-round loop 계약
  router.py        # industry ModelSpec와 segment delegation
  scenario.py      # scenario integrity
  engine.py        # 기존 OCI deterministic math
  audit.py         # sensitivity/double-count/audit gate
  state.py         # immutable runs + atomic current state
  workflow.py      # 분석시작 orchestration
tests/
examples/oci/
docs/V03_HANDOFF.md
docs/GENERIC_ENGINE_DESIGN.md
docs/LIVE_VALIDATION_AND_CALIBRATION.md
```

## 실행 불변조건

1. Market price는 Audit PASS 이후에만 읽습니다.
2. 모든 valuation assumption은 Bridge를 가져야 합니다.
3. 정책 가격만으로 기업 ASP를 바꾸지 않습니다.
4. Audit 실패와 unresolved critical issue는 valuation을 차단합니다.
5. Blocked run은 보존하지만 last-good state를 덮지 않습니다.
6. OCI 회귀값은 의도적 모델 변경이 없는 한 ±1원 이내로 유지합니다.

다음 작업은 [v0.3 Codex Handoff](docs/V03_HANDOFF.md)의 M1부터 시작합니다. 미구현 영역의 타입·계산·차단 계약은 [Generic Valuation Engine Design](docs/GENERIC_ENGINE_DESIGN.md), 회사별 검증과 확률 보정 기준은 [Live Validation and Probability Calibration](docs/LIVE_VALIDATION_AND_CALIBRATION.md)에 고정했습니다.
