from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    policy = yaml.safe_load((ROOT / "config/knowledge_placement_policy.yaml").read_text(encoding="utf-8"))
    injection = yaml.safe_load((ROOT / "config/workflow_source_injection_map.yaml").read_text(encoding="utf-8"))
    valid_layers = set(policy["layers"])
    for stage, spec in injection["stages"].items():
        for key in ("required_layers", "support_layers", "prohibited_layers"):
            for layer in spec.get(key, []):
                if layer not in valid_layers:
                    raise SystemExit(f"{stage}: unknown layer {layer} in {key}")
    freeze = injection["stages"]["intrinsic_value_freeze"]
    if "market_reference" not in freeze.get("prohibited_layers", []):
        raise SystemExit("intrinsic_value_freeze must prohibit market_reference inputs")
    bridge = injection["stages"]["evidence_to_assumption_bridge"]
    prohibited = set(bridge.get("prohibited_layers", []))
    for layer in ("broker_research", "alternative_data", "calibration_reference", "market_reference"):
        if layer not in prohibited:
            raise SystemExit(f"bridge must prohibit direct {layer}")
    print(f"PASS workflow_stages={len(injection['stages'])} policy_layers={len(valid_layers)}")


if __name__ == "__main__":
    main()
