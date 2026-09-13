# 대한항공 위험팩 — 2026-09-13 기준 연구 초안

`risk_pack.yaml`은 실제 loader로 검증한 파이프라인 입력 초안이다. `build_risk_draft.py`를 실행하면 보존된 원자료에서 OLS와 자본구조, YAML을 재생성한다. 대한항공 현재 주가/목표주가/컨센서스를 조회하거나 사용하지 않았다. 동종기업 가격은 베타 및 시가 자본구조 산정에만 사용했다.

## 실제 관측과 분석가 추정의 구분

|입력|값|성격|
|---|---:|---|
|국고10년, 2026-09-11|4.571%|Naver/Reuters 관측 수익률, 엄밀한 무위험금리와 동일하지 않음|
|성숙시장 ERP|4.230%|Damodaran 내려받은 원본의 실제 명시 기준일 2026-01-05|
|한국 CRP|0.6390645%|동일 원본; total ERP 4.8690645%|
|한국 법인세율|26.4%|동일 원본의 한계세율 대용|
|국가위험 lambda|1.0|분석가 가정. 국제매출 비율의 관측값이 아니다. 한국 허브·규제·자산 집중을 고려해 한국 CRP 전액 적용|
|118-2회 실제 쿠폰|4.710%|2026-06-11 발행, 2029-06-11 만기, 1,950억원|
|국고3년 6/10 → 9/11|3.890% → 4.023%|Naver/Reuters 관측값|
|한계 세전 Kd 초안|4.843%|분석가 추정 = 4.710 + (4.023 - 3.890). 6월 발행 직전 국채수익률 대비 신용스프레드 불변 가정|
|목표 D/(D+E)|41.1259016%|아래 고유 4사 관측 자본구조의 단순평균. 대한항공 주가 미사용|

Kd를 현재 거래되는 대한항공 채권 수익률로 표현해서는 안 된다. 9/10 확정 공시의 122-1/2 발행액은 1,700/1,800억원, 가산금리는 2Y 개별민평+0bp와 3Y-3bp이다. 절대금리는 9/15 결정 예정으로 기준일 이후 정보다. 9/8 보도의 -9/-8bp는 증액 전 수요예측이므로 최종 가산금리가 아니다. 보완 민감도는 신용스프레드 ±50bp가 합리적 분석가 범위이며 확률/신뢰구간이 아니다.

국고10년에서 한국 국가부도스프레드 0.4195049%를 차감하는 대안 Rf는 4.1514951%다. 초안은 국내 통화 국채 관행을 따르는 원수익률을 명시적으로 사용한다. 국가위험 중복의 경제적 우려를 보고하고 해당 차감 대안을 WACC 민감도에 보여줄 수 있다. 이는 관측치 변경이 아닌 방법론 선택이다.

## 베타: 실제 103개 주간수익률 OLS

공통 기간 2024-09-19~2026-09-11. 모든 계열이 존재하는 날짜 중 ISO 주별 마지막 날을 사용하고 첫 불완전 주를 제외했다. benchmark는 S&P 500 USD 가격지수. 동종기업은 Yahoo 수정종가를 쓰고 Lufthansa EUR 수정종가는 동일 날짜 EURUSD로 USD 환산했다. 회귀는 절편 포함 로그수익률 OLS이며 SE는 잔차분산에서 실제 계산했다. SE는 경험적 시나리오 확률이 아니다.

|레벨|기업|beta|OLS SE|부채(백만 현지통화)|시가총액(백만 현지통화)|세율|
|---|---|---:|---:|---:|---:|---:|
|L1 broad transport|UPS|0.854370|0.230180|28,673 USD|85,316.376 USD|25%|
|L2 airlines|United|1.280017|0.351369|33,668 USD|35,645.790 USD|25%|
|L3 premium/fuel/lease|Delta|1.130323|0.294439|21,733 USD|52,550.659 USD|25%|
|L4 FSC/cargo/MRO|Lufthansa|1.042825|0.282931|14,840 EUR|9,282.460 EUR|29.93%|

Beta와 SE는 반올림 전 값으로 YAML에 저장했다. 각 레벨 1개라는 얇은 표본 및 미국 벤치마크 편향을 명시해야 한다. 주간화로 유럽/미국 장 마감 시차 영향을 줄였으나 없애지는 못한다. benchmark 가격지수와 배당조정 peer수익률 차이도 잔여 한계다. 동일 기업을 여러 레벨에 복제해 독립 관측처럼 정밀도를 부풀리지 않았다. Lufthansa는 가장 가까운 경제 동인 비교기업이지만 유럽 노동·허브구조가 다르다. Delta는 한진칼 주주 관계가 있으나 대한항공 연결 자회사가 아니다. 대한항공, 아시아나, 진에어, 한진칼은 비교집합에 넣지 않았다. FedEx는 2026-06-01 Freight 분사에 따른 자본구조 변화로 최종 비교집합에서 제외했다.

## 자본구조 재현

모든 기업 금융부채는 2026-06-30 최신 재무제표, 시가는 2026-09-11 실제 종가. 발행 주식은 아래 최신 관측값을 9/11까지 고정한 대용치다. `capital.as_of`는 계산기준일이며 모든 부채/주식수가 그날 직접 측정됐다는 뜻이 아니다. 거래/신규발행 등의 이후 변화는 미반영.

- UPS: noncurrent debt/finance leases 23,850 + current debt 634 + op-lease current 729 + noncurrent 3,460 = 28,673m. July17 A 101,432,177 + B 749,349,405주. 두 종류는 경제적 권리가 같고 A가 B로 전환 가능하여 B 종가100.2799988USD를 적용. SEC 10Q 첫장 및 balance sheet.
- United: debt/financelease noncurrent24,294 + current2,170 + op-lease current818 + noncurrent6,386 =33,668m. July9 324,583,772주 ×109.8199997USD. SEC companyfacts(10Q filed July16, accession0000100517-26-000139).
- Delta: debt/financelease10,510+current3,442 + all op-leases869+5,163 + saleleaseback financing1,749 =21,733m. June30 657,623,030주 ×79.9100037USD. 회사의 'adjusted debt'18,279m은 fleet op-leases만 더한 지표이므로 다른 범위다. 모든 리스 포함 범위를 유지했다.
- Lufthansa: 금융부채14,825 + 당좌차월15 =14,840m, 그 안에 리스3,985m 포함(중복가산 금지). PDF p14. issued1,202,082,895주 - treasury3,105주 =1,202,079,790주(PDF 주석8) ×7.7220001EUR. 연금부채는 이 위험팩의 금융부채에 넣지 않았다.

**FCF와 리스 일관성:** 위험팩은 리스를 부채로 처리한다. FCFF에서 리스 원금지급을 이미 현금유출로 차감한 모델이면 EV→equity에서 해당 리스부채를 다시 전액 차감하는 오류가 생길 수 있다. 최종 DCF의 리스 CAPEX/ROU 증가/감가/리스원금 및 equity bridge를 동일 기준으로 맞춰야 한다.

## 직접 원천

- KR10Y: https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR&page=1 (`bond.json`)
- KR3Y 최신: https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR3YT%3DRR&page=1
- KR3Y 6월: https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR3YT%3DRR&page=7
- Damodaran: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html ; https://www.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xlsx . workbook ERPs by country/ Country Tax Rates. 홈페이지의 July 업데이트 일반 안내와 달리 받은 파일은 Jan5판이므로 최신 7월판이라고 표시하지 않았다.
- 실제 쿠폰: https://www.koreanair.com/content/dam/koreanair/footer/about-us/inverstor-relations/ir-report/audita_20262Q.pdf#page=33 ; DART 동일주석 https://dart.fss.or.kr/report/viewer.do?rcpNo=20260814002803&dcmNo=11534684&eleId=44&offset=1267716&length=474227&dtd=dart4.xsd
- 최신 A0/채권: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260910000442
- UPS: https://www.sec.gov/Archives/edgar/data/1090727/000162828026053249/ups-20260630.htm
- United: https://data.sec.gov/api/xbrl/companyfacts/CIK0000100517.json
- Delta: https://data.sec.gov/api/xbrl/companyfacts/CIK0000027904.json ; https://ir.delta.com/news/news-details/2026/Delta-Air-Lines-Announces-June-Quarter-2026-Financial-Results/default.aspx
- Lufthansa: https://investor-relations.lufthansagroup.com/fileadmin/downloads/en/financial-reports/interims-reports/LH-QR-2026-2-e.pdf
- Yahoo historical peer chart: `https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2y&interval=1d`; benchmark `%5EGSPC`, FX `EURUSD=X`. 실제 응답은 `yahoo_*.json` 보존.

검증: `load_declared_risk_pack(..., run_as_of='2026-09-13')` 통과. canonical 선언/코드/valuation run은 이 연구 에이전트가 수정하지 않았다.
