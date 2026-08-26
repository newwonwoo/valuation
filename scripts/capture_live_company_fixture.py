from __future__ import annotations

import argparse
from pathlib import Path
import sys

from valuation_engine.cli_runtime import LiveCLIError, generate_run_id
from valuation_engine.live_company_capture import (
    LiveCompanyCaptureRequest,
    capture_live_company_fixture,
    load_source_document_lineage,
    write_live_company_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a real LIVE_PRIMARY run into the canonical company acceptance fixture contract"
    )
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--company-query", required=True)
    parser.add_argument("--jurisdiction", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--provider-factory", default=None)
    parser.add_argument("--mode", choices=("success", "blocked"), required=True)
    parser.add_argument("--source-lineage", type=Path)
    parser.add_argument("--adversarial-case-id", default="")
    parser.add_argument("--expected-reason-contains", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        documents = (
            load_source_document_lineage(args.source_lineage)
            if args.source_lineage is not None
            else ()
        )
        request = LiveCompanyCaptureRequest(
            company_id=args.company_id,
            company_query=args.company_query,
            jurisdiction=args.jurisdiction,
            state_root=args.state_root,
            run_id=args.run_id or generate_run_id(),
            mode=args.mode,
            provider_factory_spec=args.provider_factory,
            source_documents=documents,
            adversarial_case_id=args.adversarial_case_id,
            expected_reason_contains=args.expected_reason_contains,
        )
        artifact = capture_live_company_fixture(request)
        digest = write_live_company_fixture(
            artifact,
            args.output,
            overwrite=args.overwrite,
        )
    except LiveCLIError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"LIVE_COMPANY_CAPTURE_FAILED:{type(exc).__name__}", file=sys.stderr)
        return 2

    print(f"wrote {args.output} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
