from __future__ import annotations

import argparse
from pathlib import Path

from valuation_engine.report_form import render_report_form_template


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "examples" / "report_forms" / "PRISM_VERIFIED_REPORT_FORM.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = render_report_form_template()
    target = args.output
    if args.check:
        if not target.exists():
            raise SystemExit(f"verified report form is missing: {target}")
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"verified report form is stale: {target}")
        print(f"verified report form synchronized: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(f"verified report form written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
