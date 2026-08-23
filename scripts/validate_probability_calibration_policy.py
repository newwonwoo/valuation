from __future__ import annotations

from pathlib import Path

import yaml

from valuation_engine.probability_calibration import load_calibration_policy


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "probability_calibration_policy.yaml"


def main() -> None:
    payload = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    if not payload.get("version"):
        raise SystemExit("probability calibration policy requires version")
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise SystemExit("probability calibration policy requires defaults mapping")
    required_defaults = {
        "min_resolved_events",
        "min_companies",
        "min_quarters",
        "min_per_displayed_band",
        "min_oos_windows",
        "max_ece",
        "max_ambiguous_censored_rate",
        "fixed_bin_edges",
    }
    missing = sorted(required_defaults - set(defaults))
    if missing:
        raise SystemExit("missing probability calibration defaults: " + ", ".join(missing))
    cohorts = payload.get("cohorts", {})
    if not isinstance(cohorts, dict):
        raise SystemExit("probability calibration cohorts must be a mapping")
    for cohort_key in sorted(cohorts):
        load_calibration_policy(POLICY, cohort_key=cohort_key)
    print(
        f"Probability calibration policy OK: version={payload['version']} cohorts={len(cohorts)}"
    )


if __name__ == "__main__":
    main()
