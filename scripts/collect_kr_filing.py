#!/usr/bin/env python3
"""Collect a filing's original sections by role, once, with a receipt.

    PYTHONPATH=src python scripts/collect_kr_filing.py runs/koreazinc-010130 \
        --rcept 20260814003958

For a resolved run, pass --selection-receipt <run_dir>/out/resolver.json.
This checks the run target, cutoff and selected filing before any collection.
Without it, collection is explicitly ARCHIVAL_UNBOUND: downloading an original
document does not authorize its use as valuation Evidence. Runtime filing and
target gates remain authoritative in both modes.

The viewer's own contents tree decides which element ids serve which role (see
config/kr_filing_toc_roles.yaml), so nothing is chosen by hand and a filing that
renumbers its sections is still collected correctly.

Collection is idempotent: a member already on disk is not fetched again, which
is what makes an interrupted collection resumable rather than restarted. Every
file collected is recorded in raw/manifest.json with its hash and whether a
reader will see it truncated.

Only public DART endpoints are used and no API key is involved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.filing_collection_plan import (  # noqa: E402
    build_raw_manifest,
    collection_binding,
    load_section_roles,
    parse_toc,
    parse_viewer_toc,
    plan_sections,
    render_toc,
)

_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}"
_HEADERS = {
    # DART serves its public viewer to ordinary browsers; an unusual agent
    # string gets the connection closed rather than a refusal to parse.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko,en;q=0.8",
}


def _get(url: str, *, referer: str | None = None, retries: int = 4) -> str:
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=60) as response:
                raw = response.read()
            for encoding in ("utf-8", "euc-kr", "cp949"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as error:  # network shapes vary; the retry is the point
            last = error
            time.sleep(2 ** attempt)
    raise SystemExit(f"could not fetch {url}: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--rcept", required=True, help="14-digit receipt number")
    parser.add_argument("--selection-receipt", type=Path,
                        help="resolver.json to bind collection to run target/as_of")
    parser.add_argument(
        "--roles",
        help="comma-separated subset of roles to collect (default: every role)",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="print the role plan without fetching any section",
    )
    args = parser.parse_args()

    rcept = args.rcept.strip()
    if not (rcept.isdigit() and len(rcept) == 14):
        raise SystemExit(f"rcept must be 14 digits, got {args.rcept!r}")

    binding = collection_binding(Path(args.run_dir), rcept, args.selection_receipt)
    print(f"collection scope: {binding['status']}")
    raw = Path(args.run_dir) / "raw"
    filing_dir = raw / f"filing_{rcept}"
    filing_dir.mkdir(parents=True, exist_ok=True)
    viewer = _VIEWER.format(rcept=rcept)

    toc_path = filing_dir / "toc.txt"
    if toc_path.is_file():
        entries = parse_toc(toc_path.read_text(encoding="utf-8"))
        print(f"toc: {len(entries)} sections (already collected)")
    else:
        entries = parse_viewer_toc(_get(viewer))
        toc_path.write_text(render_toc(entries), encoding="utf-8")
        print(f"toc: {len(entries)} sections -> {toc_path}")

    roles = load_section_roles()
    if args.roles:
        wanted = {item.strip() for item in args.roles.split(",") if item.strip()}
        unknown = wanted - {role.role for role in roles}
        if unknown:
            raise SystemExit(f"unknown roles: {', '.join(sorted(unknown))}")
        roles = tuple(role for role in roles if role.role in wanted)

    plan = plan_sections(entries, roles)
    for role, hits in plan.selected:
        print(f"  {role:30s} {', '.join(entry.ele_id for entry in hits)}")
    for role in plan.unmatched:
        print(f"  {role:30s} (no heading in this filing serves this role)")
    if plan.missing_required:
        print(
            "\nmissing required sections: " + ", ".join(plan.missing_required)
        )
        return 2
    if args.plan_only:
        return 0

    fetched = skipped = 0
    for entry in plan.entries:
        member = filing_dir / entry.member_name(rcept)
        if member.is_file() and member.stat().st_size:
            skipped += 1
            continue
        member.write_text(
            _get(entry.viewer_url(rcept), referer=viewer), encoding="utf-8"
        )
        fetched += 1

    manifest_path = raw / "manifest.json"
    manifest_path.write_text(
        json.dumps(build_raw_manifest(raw), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output = Path(args.run_dir) / "out" / "collection"
    output.mkdir(parents=True, exist_ok=True)
    binding["members"] = build_raw_manifest(filing_dir)
    (output / f"{rcept}.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nfetched {fetched}, already present {skipped}; wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
