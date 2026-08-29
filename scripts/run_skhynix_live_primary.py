from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.skhynix_live_primary import run_skhynix_live_primary
from valuation_engine.strict_live_runtime import require_canonical_live_result


def run_and_render(output: Path | None = None) -> dict[str, object]:
    with TemporaryDirectory(prefix="skhynix-prism-") as state_root:
        authority = run_skhynix_live_primary(state_root)
        if authority.result.blocked_reasons:
            print(
                json.dumps(
                    {
                        "blocked_reasons": list(authority.result.blocked_reasons),
                        "stage_traces": [
                            {
                                "stage": item.stage,
                                "status": item.status.value,
                                "rationale": item.rationale,
                            }
                            for item in authority.result.stage_traces
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise RuntimeError("SK hynix canonical run blocked")
        result = require_canonical_live_result(authority)
        valuation = result.data["generic_valuation_result"]
        report = result.data["final_report"]
        summary = {
            "run_id": result.run_id,
            "canonical_entrypoint_id": result.data.get("canonical_entrypoint_id"),
            "blocked_reasons": list(result.blocked_reasons),
            "freeze_token_present": result.freeze_token is not None,
            "execution_attestation_hash": result.data.get("execution_attestation_hash"),
            "probability_distribution_status": result.data.get("probability_distribution_status"),
            "expected_value_per_share": (
                str(valuation.expected_value_per_share)
                if valuation.expected_value_per_share is not None
                else None
            ),
            "scenario_values": {
                item.scenario_id: str(item.value_per_share)
                for item in valuation.scenarios
            },
            "final_report_present": bool(report),
        }
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(str(report), encoding="utf-8")
            output.with_suffix(".json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_and_render(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
