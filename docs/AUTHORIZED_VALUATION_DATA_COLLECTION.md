# Authorized Valuation Data Collection

`newwonwoo/valuation`의 LIVE_PRIMARY 계산 코어는 이미 Beta/WACC/PER/Street 입력을 받을 수 있지만, 범용 데이터 공급원은 호출자별 계약·권한 차이 때문에 비워 두고 있다. 이 문서는 비상업적 연구/내부 분석에서 사용할 수 있는 **공식 또는 명시적으로 허용된 데이터원만** 수집하는 경로를 정의한다.

## 원칙

1. 로그인·CAPTCHA·paywall·robots.txt를 우회하지 않는다.
2. API 키는 환경변수에서만 읽고 결과 JSON, source_ref, 오류 메시지에 남기지 않는다.
3. KRX/OpenDART/ECOS는 각 서비스의 발급·승인 절차를 거친 키만 사용한다.
4. Damodaran 데이터는 사이트의 비상업/연구 사용 허용 조건에 따라 출처를 `Aswath Damodaran, NYU Stern`으로 유지한다.
5. 목표기업 Street 목표가·컨센서스 EPS는 **Intrinsic Freeze 이후 비교 레이어에서만** 읽는다. Intrinsic 가정 생성에 역주입하지 않는다.
6. 네이버금융/FnGuide를 기본 크롤러로 만들지 않는다. 네이버금융은 robots.txt가 일반 crawler 접근을 차단하고, FnGuide는 무단 이용·DB화를 제한한다.

## CLI

모든 명령은 표준 라이브러리만 사용한다.

```bash
PYTHONPATH=src python scripts/collect_valuation_inputs.py --help
```

### 1. KRX Open API → 회귀 Beta 원자료

사전조건: KRX Data Marketplace에서 API 키 및 필요한 서비스 사용 승인을 받은 뒤 `KRX_AUTH_KEY` 환경변수를 설정한다.

```bash
export KRX_AUTH_KEY='...'
PYTHONPATH=src python scripts/collect_valuation_inputs.py krx-beta \
  --market KOSPI \
  --code 005930 \
  --code 000660 \
  --benchmark '코스피' \
  --start 2025-01-01 \
  --end 2026-08-25 \
  --output runtime_data/krx_beta.json
```

- 한 날짜의 KRX 주식 응답에서 요청한 여러 peer를 동시에 필터링하므로 peer별 중복 호출을 하지 않는다.
- 거래소 휴장일은 자동으로 관측치가 생기지 않는다.
- Beta는 공통 거래일의 단순 일수익률 OLS 기울기다.
- 이 값은 `LivePeerBetaObservation`의 raw regression input 후보이며, debt/equity/tax 조정과 L1→L4 peer hierarchy는 기존 `risk_adapters.py`에서 계속 수행해야 한다.

### 2. 한국은행 ECOS → 원화 Risk-free 후보

사전조건: ECOS Open API 키를 발급받고 `ECOS_API_KEY`에 설정한다.

```bash
export ECOS_API_KEY='...'
PYTHONPATH=src python scripts/collect_valuation_inputs.py ecos \
  --stat-code 817Y002 \
  --item-code 010210000 \
  --cycle D \
  --start 20260801 \
  --end 20260825 \
  --output runtime_data/kr10y.json
```

위 코드는 일별 시장금리의 국고채 10년물 예시다. ECOS 통계표/항목 개편 가능성이 있으므로 실제 실행 전 ECOS 카탈로그에서 코드와 단위를 재확인한다.

### 3. Damodaran → ERP / Country Risk Premium

```bash
PYTHONPATH=src python scripts/collect_valuation_inputs.py damodaran-risk \
  --country Korea \
  --output runtime_data/korea_erp.json
```

출력은 다음을 분리한다.

- `mature_market_erp`
- `country_risk_premium`
- `total_equity_risk_premium`
- `adjusted_default_spread`
- `corporate_tax_rate`

중요: Damodaran의 국가별 `Equity Risk Premium`은 이미 국가위험을 포함한다. 따라서 WACC adapter에 `total_equity_risk_premium + country_risk_premium`으로 넣으면 이중계상이다. 수집기는 `mature_market_erp = total ERP - CRP`를 별도로 계산해 이를 방지한다.

### 4. OpenDART → 공시 Basic EPS

```bash
export DART_API_KEY='...'
PYTHONPATH=src python scripts/collect_valuation_inputs.py dart-eps \
  --corp-code 00126380 \
  --business-year 2026 \
  --report-code 11012 \
  --fs-div CFS \
  --output runtime_data/dart_eps.json
```

- `11013`: 1분기
- `11012`: 반기
- `11014`: 3분기
- `11011`: 사업보고서

반기/3분기 EPS는 누적 `thstrm_add_amount`가 없으면 차단한다. 1분기/사업보고서는 공식 응답 구조에 따라 `thstrm_amount` fallback을 허용한다.

이 EPS는 **공시 실적 EPS**다. `normalized_forward_eps`나 목표기업 Street consensus EPS로 자동 승격하지 않는다.

### 5. Street / Consensus → 승인된 export만 post-freeze import

포털 크롤링 대신 이용권한이 있는 벤더·증권사·사용자 export를 아래 형태의 JSON으로 저장한다.

```json
{
  "authorization_basis": "licensed_export",
  "source_ref": "licensed://vendor/export-20260825",
  "reports": [
    {
      "broker": "Broker A",
      "analyst": "Analyst A",
      "published_date": "2026-08-24",
      "target_price": 50000,
      "target_price_currency": "KRW",
      "valuation_method": "DCF",
      "base_year": "2027E",
      "source_ref": "licensed://vendor/report-1",
      "estimates": [
        {"metric": "EPS", "period": "2027E", "value": 3100, "unit": "KRW/share"}
      ]
    }
  ]
}
```

`authorization_basis`는 `licensed_export` 또는 `explicit_permission`만 허용한다.

```bash
PYTHONPATH=src python scripts/collect_valuation_inputs.py street-import \
  --input authorized_street.json \
  --output runtime_data/street_snapshot.json
```

런타임에서 직접 사용할 때는 다음 loader를 `LivePrimaryProviders.street_loader`에 연결할 수 있다.

```python
from valuation_engine.official_market_data import street_loader_from_authorized_export

providers = LivePrimaryProviders(
    # ...
    street_loader=street_loader_from_authorized_export("authorized_street.json"),
)
```

기존 `street_reference_load_adapter`가 Intrinsic Freeze Token을 요구하므로 이 loader 자체가 pre-freeze 누출 경로를 만들지는 않는다.

## 현재 해소 범위

| 항목 | 이번 경로 | 상태 |
|---|---|---|
| Beta 원수익률/회귀 beta | KRX Open API | 자동수집 가능(승인키 필요) |
| KRW risk-free | BOK ECOS | 자동수집 가능(API 키 필요) |
| ERP / CRP | Damodaran 공개 데이터 | 자동수집 가능, 출처표기 |
| 실적 EPS | OpenDART | 자동수집 가능(API 키 필요) |
| Forward/Normalized EPS | 공시 EPS를 그대로 대체하지 않음 | 기존 Bridge/Compiler 유지 |
| Street consensus/target | 승인된 export | post-freeze 자동 로드 가능 |
| 무단 포털 크롤링 | 사용하지 않음 | 의도적으로 차단 |

이번 추가는 데이터 수집 경로를 제공하는 것이며 `live_primary_readiness.yaml`을 성급하게 `LIVE_READY`로 올리지 않는다. 특히 WACC의 marginal cost of debt와 target capital structure, PER의 normalized forward EPS/peer residual hierarchy는 기존 증거·방법론 계약을 계속 충족해야 한다.
