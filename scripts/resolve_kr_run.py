#!/usr/bin/env python3
"""Write a run directory's run.yaml from public metadata, with its reasons.

    PYTHONPATH=src python scripts/resolve_kr_run.py runs/koreazinc-010130 \
        --method commodity_price_taker/midcycle_price_volume_dcf --as-of 2026-08-29

The resolver reads three files a collector has already fetched into
``<run_dir>/raw`` — ``corp_search.json``, ``company.json`` and ``list.json`` —
and writes two:

* ``out/resolver.json``: every decision with the evidence that made it, and
  every gap it refused to guess. Written always, including when the run
  cannot be resolved, because the gaps are the work order. It lives under
  ``out/`` because everything else in a run directory is hashed into the
  run's identity, and a receipt is a result rather than an input.
* ``run.yaml``: written only when no gap stands.

Whether the company files consolidated statements is not in the profile, so it
is read from the collected ``fnltt_*_CFS.json`` when one is present and must
otherwise be declared with ``--separate-statements``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

from valuation_engine.run_resolver import resolve_run  # noqa: E402


def _load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"missing {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}; "
            "collect the run's metadata before resolving it"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _declared_segment_ids(run_dir: Path) -> tuple[str, ...]:
    """Read the segments the run already declares, from either place they live.

    A prepared sum-of-the-parts run says so twice: the operator's
    declarations/segments.yaml types each reportable segment, and run.yaml
    lists one method per segment. Either is enough to know that a
    company-level single-method declaration would be the wrong shape.
    """
    ids: list[str] = []
    declaration = run_dir / "declarations" / "segments.yaml"
    if declaration.is_file():
        payload = yaml.safe_load(declaration.read_text(encoding="utf-8")) or {}
        ids += [
            str(row.get("segment_id"))
            for row in payload.get("segments") or ()
            if isinstance(row, dict) and row.get("segment_id")
        ]
    existing = run_dir / "run.yaml"
    if existing.is_file():
        payload = yaml.safe_load(existing.read_text(encoding="utf-8")) or {}
        ids += [
            str(row.get("segment_id"))
            for row in payload.get("segments") or ()
            if isinstance(row, dict) and row.get("segment_id")
        ]
    return tuple(dict.fromkeys(ids))


def _consolidated_from_raw(raw: Path) -> bool | None:
    """Read the statement scope off the collected payloads rather than guess.

    OpenDART answers a request for statements a company does not file with
    status 013 ("조회된 데이타가 없습니다"), so a committed CFS payload that
    carries a list is the evidence that the company reports consolidated.
    """
    consolidated = sorted(raw.glob("fnltt_*_CFS.json"))
    separate = sorted(raw.glob("fnltt_*_OFS.json"))
    for path in consolidated:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("list"):
            return True
    if separate and not consolidated:
        return False
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="run directory holding raw/ metadata")
    parser.add_argument("--as-of", required=True, help="valuation date, YYYY-MM-DD")
    parser.add_argument("--query", help="company query to record in run.yaml")
    parser.add_argument("--ticker", help="six-digit code, to disambiguate namesakes")
    parser.add_argument("--method", help="archetype/method the operator chose")
    parser.add_argument(
        "--scenarios", default="Down,Base,Bull", help="comma-separated scenario ids"
    )
    parser.add_argument("--forecast-years", type=int, default=5)
    parser.add_argument(
        "--separate-statements",
        action="store_true",
        help="declare that the company files separate (OFS) statements only",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing run.yaml (the judgment layer is untouched)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    raw = run_dir / "raw"
    consolidated = False if args.separate_statements else _consolidated_from_raw(raw)
    segment_ids = _declared_segment_ids(run_dir)

    resolved = resolve_run(
        corp_search=_load(raw / "corp_search.json"),
        company=_load(raw / "company.json"),
        filing_index=_load(raw / "list.json"),
        as_of=args.as_of,
        company_query=args.query,
        stock_code=args.ticker,
        method=args.method,
        scenario_ids=tuple(item.strip() for item in args.scenarios.split(",") if item.strip()),
        forecast_years=args.forecast_years,
        consolidated=consolidated,
        declared_segment_ids=segment_ids,
        classification_map_path=ROOT / "config" / "kr_industry_classification_map.yaml",
        archetype_registry_path=ROOT / "config" / "archetype_module_registry.yaml",
    )

    # The receipt is a result, not a run input: everything under a run
    # directory but out/ is hashed into the run's identity, so a receipt
    # written beside run.yaml would make the tool's own byproduct part of what
    # the run attests to.
    output_root = run_dir / "out"
    output_root.mkdir(parents=True, exist_ok=True)
    receipt = output_root / "resolver.json"
    receipt.write_text(
        json.dumps(resolved.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for decision in resolved.decisions:
        print(f"  {decision.field}: {decision.value}\n      {decision.basis}")

    if resolved.blocking:
        print("\nunresolved — each line names what to settle before running:")
        for gap in resolved.gaps:
            print(f"  {gap.reason}: {gap.detail}")
        print(f"\nwrote {receipt}")
        return 2

    target = run_dir / "run.yaml"
    if len(segment_ids) > 1:
        # Unreachable while the gap above stands; kept as the second lock so a
        # future caller cannot force a multi-segment run into a single-segment
        # declaration and silently lose the per-segment methods.
        print(
            "\nrefusing to write a single-segment run.yaml over a declared "
            "sum-of-the-parts run: " + ", ".join(segment_ids)
        )
        return 4
    if target.exists() and not args.force:
        print(f"\n{target} exists; pass --force to overwrite it")
        return 3
    target.write_text(resolved.to_run_yaml(), encoding="utf-8")
    print(f"\nwrote {target} and {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
