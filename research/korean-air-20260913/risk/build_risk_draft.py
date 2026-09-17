"""Rebuild research-only draft from immutable downloaded peer/rate snapshots."""
import datetime as dt
import json
import hashlib
import re
from pathlib import Path
import sys
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from compute_peer_betas import regression_beta

def chart(ticker, adjusted=True):
    x = json.loads((HERE / f'yahoo_{ticker}.json').read_text())['chart']['result'][0]
    prices = x['indicators']['adjclose'][0]['adjclose'] if adjusted else x['indicators']['quote'][0]['close']
    return {dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y%m%d'): price
            for ts, price in zip(x['timestamp'], prices)
            if price and dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y%m%d') <= '20260911'}

data = {t: chart(t) for t in ['UPS', 'UAL', 'DAL', 'LHA.DE', 'GSPC', 'EURUSD_X']}
common = sorted(set.intersection(*(set(v) for v in data.values())))
weeks = {}
for day in common:
    weeks[dt.datetime.strptime(day, '%Y%m%d').isocalendar()[:2]] = day
days = list(weeks.values())[1:]  # omit initial partial week
benchmark = {d: data['GSPC'][d] for d in days}
regressions = {}
for ticker in ['UPS', 'UAL', 'DAL', 'LHA.DE']:
    prices = {d: data[ticker][d] * (data['EURUSD_X'][d] if ticker == 'LHA.DE' else 1) for d in days}
    b, se, n, start, end = regression_beta(prices, benchmark, '20260911')
    regressions[ticker] = dict(beta=b, beta_standard_error=se, observations=n, start_date=start, end_date=end, prices=prices)
(HERE / 'weekly_regressions.json').write_text(json.dumps(regressions, indent=2))
(HERE / 'weekly_benchmark.json').write_text(json.dumps(benchmark, indent=2))

# Exact SEC records and source snippets make the small evidence bundle self-contained.
# Multi-megabyte full downloads remain local; their hashes and URLs accompany the subsets.
sec = json.loads((HERE / 'sec_facts_subset.json').read_text())['companies']
snippets = json.loads((HERE / 'capital_source_snippets.json').read_text())['snippets']
for snippet in snippets.values():
    assert hashlib.sha256(snippet['raw_text'].encode()).hexdigest() == snippet['raw_utf8_sha256']

def sec_value(ticker, tag, namespace='us-gaap', unit='USD'):
    return sec[ticker]['facts'][namespace][tag]['units'][unit][0]['val']

def debt_millions(ticker):
    tags = ['LongTermDebtAndCapitalLeaseObligations', 'OperatingLeaseLiabilityCurrent',
            'OperatingLeaseLiabilityNoncurrent',
            'DebtCurrent' if ticker == 'UPS' else 'LongTermDebtAndCapitalLeaseObligationsCurrent']
    total = sum(sec_value(ticker, tag) for tag in tags)
    assert total % 1_000_000 == 0
    return total // 1_000_000

ups_shares = sum(int(x.replace(',', '')) for x in re.findall(
    r'name="dei:EntityCommonStockSharesOutstanding"[^>]*>([0-9,]+)</ix:nonFraction>',
    snippets['UPS_shares']['raw_text']))
assert ups_shares > 0
dal_saleleaseback = int(re.search(
    r'Plus: sale-leaseback financing liabilities.*?class="prnews_span">([0-9,]+)',
    snippets['DAL_saleleaseback']['raw_text'], re.S).group(1).replace(',', ''))
lha_debt = int(re.search(r'Group indebtedness\s+-([0-9,]+)',
    snippets['LHA_debt']['raw_text']).group(1).replace(',', ''))
lha_issued = int(re.search(r'([0-9,]+) registered shares',
    snippets['LHA_issued']['raw_text']).group(1).replace(',', ''))
lha_treasury = int(re.search(r'held ([0-9,]+) treasury shares',
    snippets['LHA_treasury']['raw_text']).group(1).replace(',', ''))
facts = {
    'UPS': (debt_millions('UPS'), ups_shares, .25, 'https://www.sec.gov/Archives/edgar/data/1090727/000162828026053249/ups-20260630.htm'),
    'UAL': (debt_millions('UAL'), sec_value('UAL', 'EntityCommonStockSharesOutstanding', 'dei', 'shares'), .25, 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000100517.json'),
    'DAL': (debt_millions('DAL') + dal_saleleaseback, sec_value('DAL', 'EntityCommonStockSharesOutstanding', 'dei', 'shares'), .25, 'https://ir.delta.com/news/news-details/2026/Delta-Air-Lines-Announces-June-Quarter-2026-Financial-Results/default.aspx'),
    'LHA.DE': (lha_debt, lha_issued - lha_treasury, .2993, 'https://investor-relations.lufthansagroup.com/fileadmin/downloads/en/financial-reports/interims-reports/LH-QR-2026-2-e.pdf'),
}
peers = {}
audit = {}
for ticker, (debt_m, shares, tax, source) in facts.items():
    reg = regressions[ticker]
    close = chart(ticker, False)['20260911']
    equity_m = shares * close / 1e6
    peers[ticker] = dict(peer_id=ticker,
        beta={k: reg[k] for k in ['beta','observations','start_date','end_date']} | {'benchmark':'S&P 500 USD price index; weekly adjusted peer USD log returns', 'method':'OLS with intercept, weekly log returns, 103 observations; Lufthansa adjusted EUR close converted to USD at common-date EURUSD'},
        beta_standard_error=reg['beta_standard_error'],
        capital=dict(debt=debt_m, equity_market_value=equity_m, tax_rate=tax, as_of='2026-09-11', source_ref=source),
        beta_source_ref=f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2y&interval=1d')
    audit[ticker] = dict(debt_m=debt_m, shares=shares, observed_close=close, equity_m=equity_m, debt_weight=debt_m/(debt_m+equity_m), statement_date='2026-06-30', capital_source=source)

bond_ref = 'https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR&page=1'
filing = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260910000442'
coupon_ref = 'https://www.koreanair.com/content/dam/koreanair/footer/about-us/inverstor-relations/ir-report/audita_20262Q.pdf#page=33'
pack = dict(target_id='KR:DART:00113526', as_of='2026-09-13', source_ref=filing, cash_flow_currency='KRW',
    risk_free_rate=dict(time='20260911',value=4.571,unit='연%',name='국고채 10년 관측수익률(국가부도스프레드 차감 전 대용치)',source_ref=bond_ref),
    country_risk=dict(country='Korea',as_of='2026-01-05',mature_market_erp=.0423,country_risk_premium=.006390645163649599,total_equity_risk_premium=.0486906451636496,adjusted_default_spread=.004195048511608233,corporate_tax_rate=.264,rating='Aa2',source_ref='https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html'),
    country_risk_lambda=1.0, country_risk_exposure_source_ref=filing,
    marginal_debt=dict(series=dict(time='20260911',value=4.843,unit='연%',name='분석가 추정 Kd: 118-2 실제쿠폰4.710% + KR3Y(9/11 4.023%-6/10 3.890%); 신용스프레드불변 가정',source_ref=coupon_ref),credit_rating='A0',maturity='3Y',rating_source_ref=filing),
    beta_levels={})
levels = [
 ('L1_BROAD_SECTOR','UPS','항공·육상 운송망, 연료비와 경기민감 물량을 공유하는 광의 운송업 관측치. 여객 항공사와의 차이는 상위 레벨에만 배치하며 FedEx는 2026년 분사로 제외.', ['transport demand','fuel costs','network fixed costs']),
 ('L2_INDUSTRY','UAL','United Airlines는 장거리 국제선과 네트워크 운항, 항공기 투자 및 리스 부담을 공유하는 독립 상장 항공사로 산업 레벨에 배치.', ['international passenger demand','aircraft capex','lease financing']),
 ('L3_RISK_DRIVER_SUBINDUSTRY','DAL','Delta는 프리미엄 여객 수요, 항공유 비용, 보유·리스 혼합 항공기와 정비 사업 동인을 공유한다. 대한항공 지분 관계는 있으나 연결 종속회사가 아닌 독립 상장사.', ['premium passenger yield','fuel costs','lease financing','maintenance']),
 ('L4_ECONOMIC_TWINS','LHA.DE','Lufthansa Group은 네트워크 FSC 여객, 독립 화물 운항 및 대형 외부 MRO를 병행해 대한항공 경제적 동인에 가장 가깝다. 유럽 노무·허브 구조 차이는 남으며 완전 동일 기업이라는 뜻은 아니다.', ['network FSC','dedicated cargo','external MRO','fleet investment']),
]
for level, ticker, rationale, features in levels:
    pack['beta_levels'][level] = dict(selection_rationale=rationale,risk_driver_features=features,peers=[peers[ticker]])
(HERE/'risk_pack.yaml').write_text(yaml.safe_dump(pack,allow_unicode=True,sort_keys=False))
(HERE/'peer_capital_audit.json').write_text(json.dumps(audit,indent=2))
print('Draft written; unique-peer mean debt weight:',sum(x['debt_weight'] for x in audit.values())/len(audit))
