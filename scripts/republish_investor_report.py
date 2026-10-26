#!/usr/bin/env python3
"""Reissue only presentation from a pinned frozen report bundle; no valuation run."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from valuation_engine.report_republication import republish_frozen_investor_report
from valuation_engine.investor_report import investor_report_brief_from_dict

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_bundle',type=Path)
    parser.add_argument('--source-manifest-sha256',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--brief',type=Path,help='Source-bound host-authored editorial brief JSON; no financial overrides')
    args=parser.parse_args()
    brief = investor_report_brief_from_dict(json.loads(args.brief.read_text())) if args.brief else None
    print(json.dumps(republish_frozen_investor_report(args.source_bundle,expected_manifest_sha256=args.source_manifest_sha256,output_dir=args.output_dir,brief=brief),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
