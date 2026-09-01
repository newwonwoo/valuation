from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_kr_live.py"
spec = importlib.util.spec_from_file_location("run_kr_live_cohort_gate", RUNNER)
assert spec and spec.loader
run_kr_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_kr_live)


def _copy_kisco(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(
        ROOT / "runs" / "kisco-104700",
        target,
        ignore=shutil.ignore_patterns("out"),
    )
    return target


def test_registered_production_cohort_blocks_missing_calibration(tmp_path):
    run_dir = _copy_kisco(tmp_path, "missing-calibration")
    payload = run_kr_live._load_run(run_dir)
    payload.pop("calibration", None)
    (run_dir / "run.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(run_kr_live.RunbookError) as caught:
        run_kr_live.execute_run(run_dir)
    message = str(caught.value)
    assert "CALIBRATION_REQUIRED" in message
    assert "kr-steel-long-continuous-v1" in message
    assert "kr.steel.long|5y_path|continuous_v1" in message


def test_registered_production_cohort_blocks_wrong_binding(tmp_path):
    run_dir = _copy_kisco(tmp_path, "wrong-calibration")
    payload = run_kr_live._load_run(run_dir)
    payload["calibration"]["cohort_key"] = "wrong.cohort"
    (run_dir / "run.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(run_kr_live.RunbookError) as caught:
        run_kr_live.execute_run(run_dir)
    assert "CALIBRATION_COHORT_MISMATCH" in str(caught.value)


def test_unregistered_industry_does_not_require_calibration(tmp_path):
    run_dir = _copy_kisco(tmp_path, "unregistered-industry")
    company_path = run_dir / "raw" / "company.json"
    import json
    company = json.loads(company_path.read_text(encoding="utf-8"))
    company["induty_code"] = "2611"
    company_path.write_text(json.dumps(company), encoding="utf-8")
    payload = run_kr_live._load_run(run_dir)
    payload.pop("calibration", None)
    (run_dir / "run.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assert run_kr_live._required_production_calibration(run_dir, payload) is None
