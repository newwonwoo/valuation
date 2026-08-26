from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.live_runtime import run_prism
from valuation_engine.report_form import attest_controlled_run, write_verified_report


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "test_full_live_primary_runtime.py"
DEFAULT_OUTPUT = ROOT / "examples" / "report_forms" / "PRISM_ACTUAL_RUNTIME_REPORT.md"


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "prism_full_live_fixture",
        FIXTURE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load full LIVE_PRIMARY fixture module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render() -> str:
    fixture = _load_fixture_module()
    with TemporaryDirectory(prefix="prism-report-") as temporary:
        result = run_prism(fixture.runtime_config(Path(temporary)))
        attestation = attest_controlled_run(result)
        if not attestation.passed:
            failed = tuple(
                item.check_id for item in attestation.checks if not item.passed
            )
            raise RuntimeError(
                "actual LIVE_PRIMARY fixture failed report attestation: "
                + ", ".join(failed)
            )
        target = Path(temporary) / "verified.md"
        write_verified_report(result, target)
        return target.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = _render()
    target = args.output
    if args.check:
        if not target.exists():
            raise SystemExit(f"actual runtime report is missing: {target}")
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"actual runtime report is stale: {target}")
        print(f"actual runtime report synchronized: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(f"actual runtime report written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
