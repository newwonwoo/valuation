from __future__ import annotations
import argparse
from datetime import date
import json

from valuation_engine.live_indexers import (
    HttpTransport,
    SourceFetchError,
    MissingCredentialError,
    index_iea_monthly_electricity,
    index_kiet_psi,
    index_kisdi_ict,
    index_opendart_filing_list,
    snapshot_kosis_json,
)


def main():
    ap=argparse.ArgumentParser(description='Metadata-first industry source probe. Does not mutate canonical knowledge automatically.')
    ap.add_argument('--source', choices=['KIET_PSI','KISDI_ICT','IEA_MES','OPENDART','KOSIS_URL'], required=True)
    ap.add_argument('--timeout', type=float, default=20.0)
    ap.add_argument('--retries', type=int, default=1)
    ap.add_argument('--corp-code')
    ap.add_argument('--begin-date')
    ap.add_argument('--end-date')
    ap.add_argument('--url', help='Fully configured KOSIS API URL. Keep API credentials out of committed files.')
    args=ap.parse_args()
    transport=HttpTransport(timeout_seconds=args.timeout,retries=args.retries)
    fetch=lambda url: transport.get_text(url).text
    today=date.today()

    if args.source=='KIET_PSI':
        batch=index_kiet_psi(fetch,checked_at=today)
        payload={
            'source_id':batch.source_id,'checked_at':batch.checked_at.isoformat(),'transport':batch.transport,
            'schema_hash':batch.schema_hash,'warning':batch.warning,
            'records':[{'document_id':r.document_id,'title':r.title,'published_at':r.published_at.isoformat() if r.published_at else None,'url':r.url} for r in batch.records],
        }
    elif args.source=='KISDI_ICT':
        batch=index_kisdi_ict(fetch,checked_at=today)
        payload={'source_id':batch.source_id,'checked_at':batch.checked_at.isoformat(),'schema_hash':batch.schema_hash,'records':[{'document_id':r.document_id,'title':r.title,'published_at':r.published_at.isoformat() if r.published_at else None,'url':r.url} for r in batch.records]}
    elif args.source=='IEA_MES':
        result=index_iea_monthly_electricity(fetch,checked_at=today)
        payload={
            'source_id':result.batch.source_id,'checked_at':result.batch.checked_at.isoformat(),
            'resolved_latest_published_at':result.resolved_latest_published_at.isoformat() if result.resolved_latest_published_at else None,
            'next_release':result.next_release.isoformat() if result.next_release else None,
            'endpoint_warning':result.endpoint_warning,'schema_transition_note':result.schema_transition_note,
            'records':[{'document_id':r.document_id,'published_at':r.published_at.isoformat() if r.published_at else None,'url':r.url} for r in result.batch.records],
        }
    elif args.source=='OPENDART':
        if not (args.corp_code and args.begin_date and args.end_date):
            ap.error('OPENDART requires --corp-code --begin-date --end-date and DART_API_KEY in environment')
        batch=index_opendart_filing_list(fetch,checked_at=today,corp_code=args.corp_code,begin_date=args.begin_date,end_date=args.end_date)
        payload={'source_id':batch.source_id,'checked_at':batch.checked_at.isoformat(),'schema_hash':batch.schema_hash,'records':[{'document_id':r.document_id,'title':r.title,'published_at':r.published_at.isoformat() if r.published_at else None,'url':r.url} for r in batch.records]}
    else:
        if not args.url:
            ap.error('KOSIS_URL requires --url containing a caller-configured API query; do not commit credentials')
        snap=snapshot_kosis_json(fetch,url=args.url)
        payload={'source_id':'KR_KOSIS_API','checked_at':today.isoformat(),'row_count':snap.row_count,'periods':snap.periods,'fact_hash':snap.fact_hash,'schema_hash':snap.schema_hash}
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:
        main()
    except (SourceFetchError, MissingCredentialError) as exc:
        import sys
        print(json.dumps({"status":"source_failure","error":str(exc)},ensure_ascii=False,indent=2))
        sys.exit(2)
