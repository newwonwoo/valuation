from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_live_runtime() -> None:
    path = ROOT / "src" / "valuation_engine" / "live_runtime.py"
    text = path.read_text(encoding="utf-8")
    if "broker_aware_rocket_insight_adapter" not in text:
        replace_once(
            path,
            "from .broker_runtime import (\n    BrokerResearchLoader,\n    broker_aware_module_requirement_plan_adapter,\n    broker_research_audit_adapter,\n)\n",
            "from .broker_runtime import (\n    BrokerResearchLoader,\n    broker_aware_module_requirement_plan_adapter,\n    broker_aware_rocket_insight_adapter,\n    broker_research_audit_adapter,\n)\n",
        )
    text = path.read_text(encoding="utf-8")
    old = '''        "ROCKET_INSIGHT_SCAN": live_rocket_insight_dispatch_adapter(\n            runners=providers.scanner_runners\n        ),\n'''
    new = '''        "ROCKET_INSIGHT_SCAN": broker_aware_rocket_insight_adapter(\n            live_rocket_insight_dispatch_adapter(\n                runners=providers.scanner_runners\n            ),\n            required=bool(config.require_broker_research),\n        ),\n'''
    if old in text:
        replace_once(path, old, new)
    elif "broker_aware_rocket_insight_adapter(" not in text:
        raise RuntimeError("LIVE_PRIMARY Rocket Insight wiring marker not found")


def patch_sanil() -> None:
    path = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
    text = path.read_text(encoding="utf-8")
    start_marker = '''                BrokerResearchObservation(\n                    claim=BrokerClaim(\n                        claim_id="B:SANIL:MIRAE:FORWARD_FORECAST",\n'''
    end_marker = '''            ),\n            source_refs=(\n'''
    if start_marker in text:
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        text = text[:start] + end_marker + text[end + len(end_marker):]
        path.write_text(text, encoding="utf-8")
    elif "B:SANIL:MIRAE:TARGET_PRICE" in text or "B:SANIL:IBK:TARGET_PRICE" in text:
        raise RuntimeError("Sanil locked Street fields remain in pre-freeze loader")


def patch_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "sanil-live-primary.yml"
    text = path.read_text(encoding="utf-8")
    needle = "      - 'src/valuation_engine/broker_runtime.py'\n"
    addition = needle + "      - 'src/valuation_engine/broker_runtime_v2.py'\n"
    if "broker_runtime_v2.py" not in text:
        count = text.count(needle)
        if count != 2:
            raise RuntimeError(f"expected two Broker workflow path markers, got {count}")
        text = text.replace(needle, addition)
        path.write_text(text, encoding="utf-8")


def patch_docs() -> None:
    path = ROOT / "docs" / "BROKER_RESEARCH_LAYER_V1.md"
    text = path.read_text(encoding="utf-8")
    marker = "## V2 runtime hardening"
    if marker in text:
        return
    text += '''\n\n## V2 runtime hardening\n\nCanonical LIVE_PRIMARY broker use is fail-closed:\n\n- Target-company forecasts, target prices, ratings, target multiples and consensus are not merely hidden from LLM context; they must not exist in pre-Freeze orchestrator state. They enter only through `STREET_REFERENCE_LOAD` after `INTRINSIC_VALUE_FREEZE`.\n- Every target-company factual broker lead requires at least one typed `(segment, metric)` primary-verification row. Free-text verification requests alone are insufficient.\n- A broker-discovered metric is considered verified only by active company-primary Evidence (`REALIZED_OR_FILING` or `COMPANY_OFFICIAL_PLAN`), with the satisfying Evidence IDs bound into the Broker Research audit. Analyst underwriting and market-data rows cannot satisfy this gate.\n- `checked_at` and every broker `report_date` must be on or before the frozen run `data_cutoff`; later reports hard-fail to prevent look-ahead bias.\n- Broker mechanism context and verification leads are merged into `ROCKET_INSIGHT_SCAN`, while broker claim IDs and report URLs remain forbidden as direct intrinsic-assumption Evidence.\n'''
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_live_runtime()
    patch_sanil()
    patch_workflow()
    patch_docs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
