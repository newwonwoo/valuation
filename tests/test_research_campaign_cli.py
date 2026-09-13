"""File handoff and strict-run integration; all financial numbers are fixtures."""
from copy import deepcopy
import json
import re
from pathlib import Path
import shutil
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_research_campaign import execute_campaign, main
from tests.test_research_campaign import answer, campaign_plan


def test_cli_file_handoff_resume_and_changed_code_context(tmp_path, monkeypatch):
    import run_research_campaign as cli
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(campaign_plan()))
    workspace = tmp_path / "work"
    assert main([str(plan_path), "--workspace", str(workspace)]) == 2
    order = json.loads(next((workspace / "requests").glob("*.json")).read_text())
    response_path = workspace / "responses" / "capacity.json"
    response_path.write_text(json.dumps(answer(order)))
    ready, merged = execute_campaign(plan_path, workspace)
    assert ready["status"] == "READY_FOR_COMPILATION"
    assert merged is None
    reused, _ = execute_campaign(plan_path, workspace)
    assert reused["executed_task_ids"] == []
    monkeypatch.setattr(cli, "_code_identity", lambda: "changed-engine")
    stale, _ = execute_campaign(plan_path, workspace)
    assert stale["status"] == "WORK_REQUIRED"
    assert stale["reused_task_ids"] == []


def test_research_underwriting_and_repaired_staff_pass_full_kisco_pipeline(tmp_path):
    from run_kr_live import execute_run
    original = ROOT / "runs/kisco-104700"
    run_copy = tmp_path / "run"
    shutil.copytree(original, run_copy, ignore=shutil.ignore_patterns("out"))
    config_path = run_copy / "run.yaml"
    config = yaml.safe_load(config_path.read_text())
    for key in ("artifact", "provenance", "conditioning"):
        config["calibration"][key] = str((original / config["calibration"][key]).resolve())
    config_path.write_text(yaml.safe_dump(config))
    underwriting_path = run_copy / "declarations/underwriting.yaml"
    prior = yaml.safe_load(underwriting_path.read_text())
    plan = campaign_plan()
    plan["target_id"], plan["as_of"] = prior["target_id"], prior["as_of"]
    plan["requests"][0].update(metric="normalized_ebitda", unit="KRW_billion")
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))

    def provider(order):
        row = answer(order, "65")
        row["sources"][0]["url"] = prior["declarations"]["normalized_ebitda"]["source_refs"][0]
        return row

    campaign, merged = execute_campaign(plan_path, tmp_path / "work", underwriting_path=underwriting_path,
                                        provider=provider)
    assert campaign["status"] == "READY_FOR_COMPILATION"
    assert merged and merged.is_file()
    reused, _ = execute_campaign(plan_path, tmp_path / "work", underwriting_path=underwriting_path,
                                 provider=provider)
    assert reused["executed_task_ids"] == []
    assert reused["reused_task_ids"] == ["capacity"]
    # A fresh model response must accompany changed financial assumptions.
    bridge_path = run_copy / "declarations/staff/bridge_analyst.json"
    bridge = json.loads(bridge_path.read_text())
    for draft in bridge["drafts"]:
        if draft["assumption_key"] == "normalized_ebitda" and draft["scenario_id"] == "Base":
            draft["value"] = 65
    bridge_path.write_text(json.dumps(bridge))
    hypothesis_path = run_copy / "declarations/staff/intelligence_officer.json"
    hypothesis_path.write_text(hypothesis_path.read_text().replace("EBITDA of 60", "EBITDA of 65"))
    reached, stopped, reason, result = execute_run(run_copy, underwriting_path=merged, staff_mode="replay")
    assert stopped is None, reason
    assert len(reached) == len(result.stage_traces)
    assert len(reached) >= 33
    assert result.data["probability_weighting_allowed"] is True
    compiled = result.data["compiled_assumption_set"]
    assert str(compiled.get("normalized_ebitda", "Base").measure.amount) == "65"
    price = re.search(r"기준 시나리오:\*\* 내재가치 주당 ([\d,]+)원", result.data["final_report"])
    assert price and int(price.group(1).replace(",", "")) > 17339
