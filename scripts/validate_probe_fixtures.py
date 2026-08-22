from pathlib import Path
from datetime import date
import yaml

from valuation_engine.source_index import parse_kiet_release_listing, parse_iea_data_product_metadata, parse_kisdi_report_metadata
from valuation_engine.source_watch import EndpointObservation, EndpointRole, reconcile_endpoint_observations

ROOT=Path(__file__).resolve().parents[1]
probes=yaml.safe_load((ROOT/'data/source_probe_fixtures.yaml').read_text(encoding='utf-8'))['probes']
errors=[]
iea=[]
for p in probes:
    expected=date.fromisoformat(p['expected_latest_published_at'])
    if p['series_id']=='KIET_PSI':
        rows=parse_kiet_release_listing(p['observed_text'])
        got=max(r.published_at for r in rows) if rows else None
    elif p['series_id']=='IEA_MONTHLY_ELECTRICITY':
        meta=parse_iea_data_product_metadata(p['observed_text'])
        got=meta.latest_file_updated or meta.last_updated
        role=EndpointRole.DATA_EXPLORER if p['endpoint_id'].endswith('tool') else EndpointRole.PRIMARY_INDEX
        iea.append(EndpointObservation(p['endpoint_id'],role,True,got,p['endpoint_id']))
    elif p['series_id']=='KISDI_ICT_MEDIUM_TERM':
        got=parse_kisdi_report_metadata(p['observed_text'],url='https://www.kisdi.re.kr/').published_at
    else:
        continue
    if got != expected: errors.append(f"{p['endpoint_id']}: got {got} expected {expected}")
if iea:
    r=reconcile_endpoint_observations(tuple(iea))
    if r.resolved_latest_published_at != date(2026,8,17): errors.append('IEA endpoint reconciliation failed')
    if not r.divergent: errors.append('IEA endpoint divergence not detected')
if errors: raise SystemExit('\n'.join(errors))
print(f'PASS probe_fixtures={len(probes)} iea_divergence_detected=True')
