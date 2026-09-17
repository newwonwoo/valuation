"""Reproducible analyst scenarios; amounts KRW billion, not company guidance.

Forward years are twelve-month periods ending September 2027--2031.
Source facts and all departures from those facts remain in model.json.
"""
from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'runs/korean-air-003490'
SOURCE = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814002803'
MRO = 'https://www.lufthansa-technik.com/en/financials'
TAX = .264
# Separate passenger capacity/traffic inform growth, never substitute for group sales.
# The final-year cash/lease reinvestment is normalized, not extrapolated from the
# single 2026H1 delivery schedule. Terminal ROIC/growth are checked separately.
cases = {
 'Down': {'growth':[.01,.02,.02,.02,.02], 'margin':[.035,.04,.045,.05,.055], 'synergy':[0,30,60,90,100], 'integration':[220,160,80,30,0], 'capex':[4900,4800,4300,3700,3350], 'rou':[1200,1100,1100,1000,950], 'da':[3400,3550,3700,3850,4000], 'aero_growth':[.10,.10,.08,.06,.04], 'aero_margin':[.055,.06,.065,.065,.065], 'engine_sales':[5,15,30,45,60], 'engine_margin':[.025,.035,.04,.045,.045], 'hotel_value':950, 'g':.015, 'roic':.065},
 'Base': {'growth':[.035,.04,.04,.035,.03], 'margin':[.05,.06,.07,.0775,.08], 'synergy':[30,90,150,180,200], 'integration':[180,120,60,20,0], 'capex':[5000,4800,4400,3900,3600], 'rou':[1200,1150,1100,1050,1000], 'da':[3450,3650,3850,4050,4250], 'aero_growth':[.18,.16,.12,.10,.07], 'aero_margin':[.075,.08,.085,.09,.09], 'engine_sales':[15,40,75,110,140], 'engine_margin':[.045,.055,.065,.075,.075], 'hotel_value':1250, 'g':.02, 'roic':.08},
 'Bull': {'growth':[.06,.065,.06,.05,.04], 'margin':[.065,.08,.09,.0975,.10], 'synergy':[50,130,200,230,240], 'integration':[150,90,35,0,0], 'capex':[5300,5100,4750,4250,3900], 'rou':[1250,1200,1200,1150,1100], 'da':[3500,3750,4050,4350,4650], 'aero_growth':[.25,.22,.18,.13,.10], 'aero_margin':[.08,.09,.10,.105,.105], 'engine_sales':[25,65,115,165,210], 'engine_margin':[.05,.065,.08,.085,.085], 'hotel_value':1550, 'g':.025, 'roic':.10},
}
declarations = {}
def add(key,value,unit,segment,rationale,refs=None):
 declarations[key]={'value':round(value,6) if isinstance(value,float) else value,'unit':unit,'segment':segment,'source_refs':refs or [SOURCE],'rationale':rationale}
model = {'schema':'korean-air-driver-model/v1','as_of':'2026-09-13','unit':'KRW_billion','tax_rate':TAX,'periods':[f'12 months ending {y}-09-13' for y in range(2027,2032)],'scenarios':{},'method_notes':[
 'Every forecast is analyst judgment. TTM group airline external revenue 24964.951 anchors the path; passenger yields and cargo fuel surcharges are not perpetual real growth.',
 'Asiana is already consolidated. Only incremental operating synergies enter EBIT. Company 300bn total synergy includes financing benefit, therefore Base operating run-rate is only200bn. Do not also add that interest benefit to FCFF.',
 'Existing connected-company gross EBIT is reconciled by allocating the current elimination cost to airline normalization. Modeled margins are AFTER recurring group eliminations and BEFORE incremental synergy/integration.',
 'Lease-inclusive net debt requires depreciation including ROU and new ROU additions in economic CAPEX. Lease principal is not subtracted again. Aircraft order headline through late2030s is not wholly spent within five years.',
 'Aerospace includes existing airframe contracts and maintenance. New engine external sales enter other only; internal engine savings remain within airline margin recovery, with no second sales contribution.',
 'Engine comparison is Lufthansa Technik actual2025 adjustedEBIT/revenue 7.5%, not net margin. Base ramp margins 4.5/5.5/6.5/7.5/7.5% reflect scale/ramp discounts. Customer mix and adjusted accounting limit comparability.',
 'Engine plant 578bn committed construction predates forecast; residual 80/40bn commissioning spend in first two periods is explicitly assigned to other, with group airline fleet capex excluding this residual.',
 'Hotel value is gross operating asset NAV based on 1738.126bn reported assets, reduced for cash/intercompany/other nonoperating items already in the group bridge and operating underperformance. 950/1250/1550bn is an uncertain analyst range, not an appraisal. All group debt is deducted once through airline bridge.',
 'Forecast periods end each September to match valuation date. June balance sheet rolls forward to September with explicit700bn funding assumption; this stub is not forecast-year1 cash flow.',
 'Remaining post-merger NCI is a350bn proxy, anchored to252.744bn group book NCI less negative79.203bn Asiana NCI, allowing modest uplift. It includes perpetual claims; no second perpetual subtraction. Fair value uncertainty remains material.',
 'Common and preferred shares are treated as equal economic participation for allocation, acknowledging preferred dividend rights. Merger shares are included; no extra Asiana EV is added.'
 ]}
for case,c in cases.items():
 prefix='' if case=='Base' else case.lower()+'_'
 vals={}; paths=[]
 rev=24964.951; aero_rev=944.519; other_rev=338.462
 for i in range(5):
  prev=rev; rev*=1+c['growth'][i]
  ebit=rev*c['margin'][i]+c['synergy'][i]-c['integration'][i]
  nwc=.03*(rev-prev)
  fcff=ebit*(1-TAX)+c['da'][i]-c['capex'][i]-c['rou'][i]-nwc
  olda=aero_rev; aero_rev*=1+c['aero_growth'][i]
  aero_ebit=aero_rev*c['aero_margin'][i]
  aero_da=.023*aero_rev; aero_capex=.04*aero_rev; aero_wc=.18*(aero_rev-olda)
  aero_fcff=aero_ebit*(1-TAX)+aero_da-aero_capex-aero_wc
  oldo=other_rev; other_rev*=1+({'Down':.01,'Base':.04,'Bull':.06}[case])
  om={'Down':.055,'Base':.075,'Bull':.09}[case]
  oebit=other_rev*om+c['engine_sales'][i]*c['engine_margin'][i]
  oda=.018*(other_rev+c['engine_sales'][i]); ocap=.025*(other_rev+c['engine_sales'][i])+[80,40,0,0,0][i]
  owc=.10*(other_rev-oldo+c['engine_sales'][i]-(c['engine_sales'][i-1] if i else 0))
  ofcff=oebit*(1-TAX)+oda-ocap-owc
  # Year five is the normalized terminal entry year, not a delivery trough
  # perpetuated forever. Make reinvestment consistent with declared g / ROIC.
  if i == 4:
   c['capex'][i]=ebit*(1-TAX)*c['g']/c['roic']+c['da'][i]-c['rou'][i]-nwc
   fcff=ebit*(1-TAX)*(1-c['g']/c['roic'])
   aero_capex=aero_ebit*(1-TAX)*c['g']/c['roic']+aero_da-aero_wc
   aero_fcff=aero_ebit*(1-TAX)*(1-c['g']/c['roic'])
   ocap=oebit*(1-TAX)*c['g']/c['roic']+oda-owc
   ofcff=oebit*(1-TAX)*(1-c['g']/c['roic'])
  paths.append(dict(year=i+1,airline=dict(revenue=rev,ebit=ebit,margin=c['margin'][i],synergy=c['synergy'][i],integration=c['integration'][i],depreciation=c['da'][i],cash_capex=c['capex'][i],rou_additions=c['rou'][i],delta_nwc=nwc,fcff=fcff),aerospace=dict(revenue=aero_rev,ebit=aero_ebit,depreciation=aero_da,capex=aero_capex,delta_nwc=aero_wc,fcff=aero_fcff),other=dict(existing_revenue=other_rev,engine_external_revenue=c['engine_sales'][i],engine_ebit_margin=c['engine_margin'][i],ebit=oebit,depreciation=oda,capex=ocap,delta_nwc=owc,fcff=ofcff)))
  for seg,v in [('airline',fcff),('aerospace',aero_fcff),('other',ofcff)]:
   key=f'{seg}_fcff_year_{i+1}';vals[key]=v
   add(prefix+key,v,'KRW_billion',seg,f'{case} forward period{i+1}: model.json의 실제 매출기준과 명시적 성장·이익률·세율·감가상각·현금투자·신규리스·운전자본으로 재계산한 FCFF. 추정값이며 공식 가이던스가 아닙니다. 원문확인 후 사업추론을 별도 기록했습니다.',[SOURCE,MRO] if seg=='other' else None)
 for seg in ['airline','aerospace','other']:
  for tail,v in [('terminal_growth',c['g']),('terminal_roic',c['roic'])]:
   key=f'{seg}_{tail}';vals[key]=v;add(prefix+key,v,'ratio',seg,f'{case} 장기 성장/투하자본수익률 판단입니다. 명목성장을 유한하게 제한하고 말기 FCFF에 투자소요를 포함합니다; 공시 확정치가 아닙니다.')
 vals['hotel_gross_asset_value']=c['hotel_value']
 add(prefix+'hotel_gross_asset_value',c['hotel_value'],'KRW_billion','hotel',f'{case} 호텔 총자산1738.126bn을 출발점으로 현금·관계회사 항목 및 손실사업의 실현가치 불확실성을 할인한 영업자산 NAV 추정. model.json에 정의한950~1550bn 범위. 금융부채는 연결브리지에서 한 번 차감합니다.')
 model['scenarios'][case]={'inputs':c,'paths':paths,'valuation_assumptions':vals}
for seg in ['airline','aerospace','hotel','other']:
 add(seg+'_ownership',1,'ratio',seg,'IFRS8 영업부문100%를 합산하고 연결 비지배지분은 모회사 조정으로 한 번만 차감합니다.')
 if seg!='hotel':add(seg+'_ev_adjustment',-20016.8 if seg=='airline' else 0,'KRW_billion',seg,'연결순차입금19224.687bn+사용제한예금92.1bn+6월말~9월13일 순자금소요700bn=20016.787bn(반올림). 리스포함; 전 부문 현금·부채는 airline에 한 번 배분하고 다른 부문은 중복차감하지 않습니다. 700bn은 미공시 stub 추정, 예금달러환산은 반올림 근사입니다.')
add('hotel_liabilities',0,'KRW_billion','hotel','호텔에 배분된 금융부채는 연결 airline EV브리지에서 이미 전액 차감합니다. 호텔영업자산만 NAV로 가산하므로 이 값은 부채 부존재 주장이 아닌 중복차감 방지입니다.')
add('parent_noncontrolling_interest_adjustment',-350,'KRW_billion','airline','합병후 잔여 NCI+영구채 청구권 통합 추정350bn. 반기 NCI252.744bn에서 아시아나 음수NCI-79.203bn을 제거한331.947bn을 참고. 장부가=공정가치를 단정하지 않으며 할인율과 별개로 가치오차가 있습니다.')
add('diluted_shares',389669121,'shares','airline','반기말 유통보통368220609+합병신주20337721+유통우선1110791=389669121. 우선주를 보통주와 같은 경제적 청구권으로 간주한 배분근사; 자기주식55주는 이미 제외. 합병신주는 공식원문 확인대상입니다.')
declarations['diluted_shares']['source_refs'].append('https://dart.fss.or.kr/report/viewer.do?rcpNo=20260724000006&dcmNo=11493004&eleId=1&offset=620&length=3475&dtd=dart4.xsd')
declarations['diluted_shares']['rationale']=declarations['diluted_shares']['rationale'].replace('합병신주는 공식원문 확인대상입니다.','7월24일 투자설명서로 계획신주수를 검증했습니다. 매수청구에 따라 최종 수량 변동 가능성이 있습니다.')
model['method_notes'].append('Year5 is normalized terminal entry: each business net reinvestment=NOPAT*g/ROIC; cash CAPEX=NOPAT*g/ROIC+D&A−ROU additions−delta NWC. Earlier-year investment plans are not blindly perpetuated. All three component cash flows reconcile to this identity.')
model['method_notes'].extend([
 'D&A anchor: airline grossTTM3256.225bn less group consolidation elimination351.811bn =2904.414bn net. Base year1 3450bn is a forecast increase545.586bn; 6200bn cash/lease additions at roughly11-year blended depreciable life support that scale. Not a filed D&A figure. Timing, retirements and fleet life remain uncertain.',
 'Hotel scenario NAV is net of nonfinancial operating obligations as well as duplicated cash/intercompany assets. The aggregate188.126--788.126bn reduction from reported assets is an analyst reserve for these combined claims and realization discounts, not an itemized audited bridge. Financial debt remains entirely in the group debt bridge.',
 'Year5 is a transition to terminal reinvestment intensity. Its explicit sales growth can differ from terminal g; following-year terminal NOPAT and FCFF then both grow at g. This is a stated transition approximation, not a claim of a fully steady year5 operating state.'
])
module_path=Path(__file__).parent/'module_evidence.yaml'
if module_path.exists():
 module=yaml.safe_load(module_path.read_text())['declarations']
 assert not declarations.keys() & module.keys()
 declarations.update(module)
RUN.mkdir(parents=True,exist_ok=True);(RUN/'declarations').mkdir(exist_ok=True)
(RUN/'declarations/model.json').write_text(json.dumps(model,ensure_ascii=False,indent=2))
(RUN/'declarations/underwriting.yaml').write_text(yaml.safe_dump(dict(target_id='KR:DART:00113526',as_of='2026-09-13',source_ref=SOURCE,declarations=declarations),allow_unicode=True,sort_keys=False))
config=dict(company_query='대한항공',run_id='LIVE-KOREAN-AIR-1',as_of='2026-09-13',jurisdiction='KR',scenario_ids=['Down','Base','Bull'],segments=[dict(segment_id=s,method=m) for s,m in [('airline','airline_transport/traffic_yield_dcf'),('aerospace','contracted_backlog/normalized_dcf'),('hotel','asset_yield_nav/nav'),('other','service_operations/normalized_service_dcf')]],parent_adjustments=[dict(asset_id='parent_noncontrolling_interest',assumption_key='parent_noncontrolling_interest_adjustment')],market_currency='KRW',filing=dict(business_year='2026',report_code='11012',fs_div='CFS',fiscal_period_end='2026-06-30',segment_id='airline'),public_filing_facts_path='public_filing_facts.json',extra_required_evidence=['revenue','operating_income','net_income','total_assets','total_liabilities','total_equity','cash_and_cash_equivalents']+[k for k in declarations if k.startswith(('down_','bull_'))])
(RUN/'run.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
print('Wrote model, underwriting and run declaration')
for s,v in model['scenarios'].items():print(s,[(p['year'],round(p['airline']['fcff'])) for p in v['paths']])
