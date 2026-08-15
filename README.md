# Insight Valuation Engine

채팅에서 `분석시작 <회사>`라고 말하면 **산업 구조 → 증거 → 인사이트 → 가정 → 밸류에이션 → 감사 → 현재가 비교** 순서로 작동시키기 위한 프로젝트입니다.

## 핵심 원칙

- **가격이 가정을 만들지 않는다.** 현재가는 마지막 비교에만 사용합니다.
- 숫자는 `실적·공시값 / 회사 공식 IR 계획 / 정책 원문 / 외부 참고치 / 모델 가정 / 모델 산출값 / 시장 비교값`으로 분리합니다.
- LLM은 인과·가설·증거 품질을 판단하고, 계산은 결정론적 코드가 담당합니다.
- 미확정 미래는 0/100이 아니라 근거가 있는 확률로 반영합니다.
- 산업 판별을 먼저 하고, 산업마다 가치 드라이버와 모델을 바꿉니다.

## 현재 상태

v0.1은 OCI홀딩스에서 만든 엑셀 엔진을 Python 회귀테스트로 옮긴 최소 코어입니다.

```text
Bear      ≈ 122,709원
Base      ≈ 243,344원
Bull      ≈ 406,697원
AI/Space  ≈ 500,501원
확률가중   ≈ 291,803원
```

이 숫자는 예시 회귀테스트이며, 시장가격으로 역산하지 않습니다.

## 구조

```text
AGENTS.md
skills/valuation-analysis/
  SKILL.md
  references/
src/valuation_engine/
  engine.py
  audit.py
  router.py
  config.py
examples/oci/company.yaml
tests/
configs/
```

## 실행

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src python -m valuation_engine.cli examples/oci/company.yaml
```

## 다음 개발 순서

1. Evidence Ledger 스키마 구현
2. 한국 DART / 미국 SEC 등 primary-source adapter 분리
3. 산업 라우터 확장
4. 수주형·소재형·에너지형·소프트웨어형·금융·바이오 valuation adapter 구현
5. Evidence → Assumption Bridge 및 probability update rule 구현
6. Excel export / dashboard export
7. 회귀테스트 종목 확대

## ChatGPT / Codex

이 저장소의 `SKILL.md`는 `분석시작`과 기업가치 평가 요청을 반복 가능한 workflow로 정의합니다. `AGENTS.md`는 Codex가 코드 수정 시 지켜야 할 프로젝트 규칙입니다.
