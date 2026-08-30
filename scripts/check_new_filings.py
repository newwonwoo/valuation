"""Keyless new-filing check for every committed run directory.

The F1 watch loop (docs/RUNBOOK_KR_LIVE.md §8) needs one question answered
with no API key and no MCP tooling: "has this ticker filed anything since the
run's as_of?" DART's public search endpoint (dsab007/detailSearch.ax) answers
it — the same public surface the runbook's raw collection already relies on.

Usage:
    python scripts/check_new_filings.py [runs_root] [--until YYYYMMDD]

For each runs/<dir>/run.yaml the script posts one search bounded
(as_of, until], prints every filing row, and marks the actionable ones
(periodic reports and 주요사항보고). Exit code 1 when anything actionable
exists — so a shell can branch on it — else 0.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import html as html_lib
import json
from pathlib import Path
import re
import ssl
import sys
import urllib.parse
import urllib.request

import yaml

SEARCH_URL = "https://dart.fss.or.kr/dsab007/detailSearch.ax"
CA_BUNDLE = Path("/root/.ccr/ca-bundle.crt")

#: A filing whose name carries one of these tokens re-opens the run: periodic
#: reports change the underwriting base, 주요사항보고 can change the thesis.
ACTIONABLE_TOKENS = ("사업보고서", "반기보고서", "분기보고서", "주요사항보고")

_ROW = re.compile(
    r"main\.do\?rcpNo=(?P<rcept>\d{14})[^>]*>\s*(?P<name>[^<]+?)\s*<"
    r".*?<td>(?P<date>\d{4}\.\d{2}\.\d{2})</td>",
    re.S,
)


def _fetch_rows(stock_code: str, start: str, end: str) -> list[tuple[str, str, str]]:
    payload = urllib.parse.urlencode(
        {
            "currentPage": "1",
            "maxResults": "100",
            "textCrpNm": stock_code,
            "startDate": start,
            "endDate": end,
        }
    ).encode()
    context = (
        ssl.create_default_context(cafile=str(CA_BUNDLE))
        if CA_BUNDLE.exists()
        else ssl.create_default_context()
    )
    request = urllib.request.Request(
        SEARCH_URL, data=payload, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        body = response.read().decode("utf-8", errors="replace")
    return [
        (
            m.group("rcept"),
            re.sub(r"\s+", " ", html_lib.unescape(m.group("name"))).strip(),
            m.group("date"),
        )
        for m in _ROW.finditer(body)
    ]


def check_run(run_dir: Path, until: str) -> tuple[list[str], list[str]]:
    config = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    as_of = str(config["as_of"])
    search = json.loads(
        (run_dir / "raw" / "corp_search.json").read_text(encoding="utf-8")
    )
    stock_code = search["companies"][0]["stock_code"]
    start = (date.fromisoformat(as_of) + timedelta(days=1)).strftime("%Y%m%d")
    actionable: list[str] = []
    informational: list[str] = []
    for rcept, name, filed in _fetch_rows(stock_code, start, until):
        line = f"{filed}  {rcept}  {name}"
        if any(token in name for token in ACTIONABLE_TOKENS):
            actionable.append(line)
        else:
            informational.append(line)
    return actionable, informational


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs_root", nargs="?", default="runs")
    parser.add_argument(
        "--until",
        default=date.today().strftime("%Y%m%d"),
        help="search window end, YYYYMMDD (default: today)",
    )
    args = parser.parse_args()
    any_actionable = False
    for run_dir in sorted(Path(args.runs_root).iterdir()):
        if not (run_dir / "run.yaml").exists():
            continue
        actionable, informational = check_run(run_dir, args.until)
        print(f"== {run_dir.name}")
        for line in actionable:
            print(f"  ACTIONABLE  {line}")
        for line in informational:
            print(f"  info        {line}")
        if not actionable and not informational:
            print("  (no new filings)")
        any_actionable = any_actionable or bool(actionable)
    return 1 if any_actionable else 0


if __name__ == "__main__":
    sys.exit(main())
