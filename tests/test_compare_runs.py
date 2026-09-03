from __future__ import annotations

from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_runs.py"
SPEC = importlib.util.spec_from_file_location("compare_runs_test_module", SCRIPT)
assert SPEC and SPEC.loader
compare_runs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compare_runs
SPEC.loader.exec_module(compare_runs)


def _write_run(
    path: Path,
    *,
    x: str = "10",
    y: str = "20",
    method: str = "commodity_price_taker/normalized_multiple",
    probabilities=("0.2", "0.6", "0.2"),
    wacc: str = "0.09",
    ticker: str = "010130",
    risk_pack: dict | None = None,
    rationale_suffix: str = "",
):
    path.mkdir(parents=True)
    (path / "declarations").mkdir()
    (path / "run.yaml").write_text(
        yaml.safe_dump(
            {
                "company_query": "Target",
                "as_of": "2026-09-01",
                "scenario_ids": ["Down", "Base", "Bull"],
                "forecast_years": 5,
                "method": method,
                "filing": {
                    "business_year": "2025",
                    "report_code": "11011",
                    "fs_div": "CFS",
                    "fiscal_period_end": "2025-12-31",
                    "segment_id": "core",
                },
                "_fake_probabilities": list(probabilities),
                "_fake_wacc": wacc,
                "_fake_ticker": ticker,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (path / "declarations" / "underwriting.yaml").write_text(
        yaml.safe_dump(
            {
                "target_id": "KR:DART:00000000",
                "as_of": "2026-09-01",
                "source_ref": "https://example.com/source",
                "declarations": {
                    "x": {
                        "value": x,
                        "unit": "KRW",
                        "segment": "core",
                        "rationale": "x normalization judgment rationale" + rationale_suffix,
                    },
                    "y": {
                        "value": y,
                        "unit": "KRW",
                        "segment": "core",
                        "rationale": "y normalization judgment rationale",
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if risk_pack is not None:
        (path / "declarations" / "risk_pack.yaml").write_text(
            yaml.safe_dump(risk_pack, sort_keys=True), encoding="utf-8"
        )


def _declaration_total(run_dir: Path) -> Decimal:
    payload = yaml.safe_load(
        (run_dir / "declarations" / "underwriting.yaml").read_text(encoding="utf-8")
    )
    total = Decimal("0")
    for entry in payload["declarations"].values():
        rows = entry if isinstance(entry, list) else [entry]
        for row in rows:
            total += Decimal(str(row["value"]))
    return total


def _fake_executor(run_dir: Path):
    config = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    base = _declaration_total(run_dir)
    scenario_values = {
        "Down": base * Decimal("0.5"),
        "Base": base,
        "Bull": base * Decimal("1.5"),
    }
    probabilities = tuple(
        Decimal(str(value)) for value in config.get("_fake_probabilities", ("0.2", "0.6", "0.2"))
    )
    scenario_rows = tuple(
        SimpleNamespace(scenario_id=key, value_per_share=value)
        for key, value in scenario_values.items()
    )
    probability_rows = tuple(
        SimpleNamespace(scenario_id=key, probability=probability)
        for key, probability in zip(("Down", "Base", "Bull"), probabilities, strict=True)
    )
    expected = sum(
        scenario_values[key] * probability
        for key, probability in zip(("Down", "Base", "Bull"), probabilities, strict=True)
    )
    result = SimpleNamespace(
        data={
            "ticker": str(config.get("_fake_ticker", "010130")),
            "generic_valuation_result": SimpleNamespace(
                scenarios=scenario_rows,
                expected_value_per_share=expected,
            ),
            "bound_scenario_set": SimpleNamespace(scenarios=probability_rows),
            "live_wacc_result": SimpleNamespace(
                wacc_result=SimpleNamespace(wacc=Decimal(str(config.get("_fake_wacc", "0.09"))))
            ),
        }
    )
    return ((), None, "", result)


def _trusted_repository_validator(run_dir: Path) -> dict:
    return {
        "repository": "TEST",
        "run_path": run_dir.name,
        "commit": "TEST",
        "input_tree_sha256": run_dir.name,
        "inputs": [],
    }


def _compare(run_a: Path, run_b: Path, **kwargs):
    with patch.object(
        compare_runs,
        "_committed_run_receipt",
        _trusted_repository_validator,
    ):
        return compare_runs.compare_run_directories(run_a, run_b, **kwargs)


def test_same_contract_gets_exact_ordered_underwriting_waterfall(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, x="10", y="20")
    _write_run(run_b, x="15", y="18")

    result = _compare(run_a, run_b, executor=_fake_executor)

    assert result["status"] == compare_runs.STATUS_CONSISTENT
    assert result["decomposition_order"] == ["x", "y"]
    assert [row["base_delta_per_share"] for row in result["attribution"]] == [
        "5",
        "-2",
    ]
    assert [row["expected_delta_per_share"] for row in result["attribution"]] == [
        "5.00",
        "-2.00",
    ]
    assert Decimal(result["residual"]["base_value_per_share"]) == 0
    assert Decimal(result["residual"]["expected_value_per_share"]) == 0
    assert result["threshold_findings"] == []


def test_method_mismatch_stops_before_any_attribution_execution(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b, method="process_spread/normalized_multiple")
    calls = 0

    def should_not_run(_path):
        nonlocal calls
        calls += 1
        raise AssertionError("structural mismatch must not execute attribution")

    result = _compare(run_a, run_b, executor=should_not_run)

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert calls == 0
    assert result["structural_findings"][0]["code"] == "METHOD_CONTRACT_MISMATCH"
    assert result["attribution"] == []


def test_risk_pack_difference_is_structural_not_judgment(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, risk_pack={"risk_free_rate": 0.03})
    _write_run(run_b, risk_pack={"risk_free_rate": 0.04})

    result = _compare(run_a, run_b, executor=_fake_executor)

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "WACC_INPUT_CONTRACT_MISMATCH"
        for item in result["structural_findings"]
    )
    assert result["attribution"] == []


def test_base_variance_threshold_triggers_reconciliation_with_attribution(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, x="10", y="20")
    _write_run(run_b, x="30", y="20")

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        base_threshold=Decimal("0.20"),
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "BASE_VALUE_VARIANCE_EXCEEDED"
        for item in result["threshold_findings"]
    )
    assert result["attribution"][0]["identity"] == "x"
    assert result["attribution"][0]["base_delta_per_share"] == "20"
    assert Decimal(result["residual"]["base_value_per_share"]) == 0


def test_base_variance_at_twenty_percent_boundary_requires_reconciliation(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, x="10", y="20")
    _write_run(run_b, x="16", y="20")

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        base_threshold=Decimal("0.20"),
    )

    assert result["base_gap_ratio"] == "0.2"
    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "BASE_VALUE_VARIANCE_EXCEEDED"
        for item in result["threshold_findings"]
    )


def test_probability_gap_over_ten_points_requires_reconciliation(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, probabilities=("0.2", "0.6", "0.2"))
    _write_run(run_b, probabilities=("0.05", "0.55", "0.40"))

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        probability_threshold=Decimal("0.10"),
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "PROBABILITY_VARIANCE_EXCEEDED"
        for item in result["threshold_findings"]
    )


def test_probability_gap_at_ten_points_keeps_existing_exclusive_boundary(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(
        run_a,
        x="0",
        y="0",
        probabilities=("0.2", "0.6", "0.2"),
    )
    _write_run(
        run_b,
        x="0",
        y="0",
        probabilities=("0.1", "0.6", "0.3"),
    )

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        probability_threshold=Decimal("0.10"),
    )

    assert result["status"] == compare_runs.STATUS_CONSISTENT
    assert not any(
        item["code"] == "PROBABILITY_VARIANCE_EXCEEDED"
        for item in result["threshold_findings"]
    )


def test_wacc_gap_over_one_point_requires_reconciliation(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, wacc="0.09")
    _write_run(run_b, wacc="0.105")

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        wacc_threshold=Decimal("0.01"),
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "WACC_VARIANCE_EXCEEDED"
        for item in result["threshold_findings"]
    )


def test_wacc_gap_at_one_point_boundary_requires_reconciliation(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, wacc="0.09")
    _write_run(run_b, wacc="0.10")

    result = _compare(
        run_a,
        run_b,
        executor=_fake_executor,
        wacc_threshold=Decimal("0.01"),
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "WACC_VARIANCE_EXCEEDED"
        and item["actual"] == "0.01"
        for item in result["threshold_findings"]
    )


def test_metadata_only_judgment_change_is_visible_but_has_zero_value_delta(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b, rationale_suffix=" with a different LLM explanation")

    result = _compare(run_a, run_b, executor=_fake_executor)

    assert result["status"] == compare_runs.STATUS_CONSISTENT
    assert len(result["attribution"]) == 1
    assert result["attribution"][0]["metadata_only"] is True
    assert Decimal(result["attribution"][0]["base_delta_per_share"]) == 0
    assert Decimal(result["residual"]["base_value_per_share"]) == 0


def test_target_mismatch_is_structural_after_canonical_execution(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a, ticker="010130")
    _write_run(run_b, ticker="000660")

    result = _compare(run_a, run_b, executor=_fake_executor)

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert result["structural_findings"][0]["code"] == "TARGET_MISMATCH"
    assert result["attribution"] == []


def test_external_runs_are_rejected_before_execution(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)
    calls = 0

    def should_not_run(_path):
        nonlocal calls
        calls += 1
        raise AssertionError("external runs must not execute")

    result = compare_runs.compare_run_directories(
        run_a, run_b, executor=should_not_run
    )

    assert result["status"] == compare_runs.STATUS_EXTERNAL_RUN_NOT_COMPARABLE
    assert calls == 0
    assert {item["run"] for item in result["comparability_findings"]} == {
        "run_a",
        "run_b",
    }
    assert all(
        item["code"] == "PRISM_COMMITTED_RUN_REQUIRED"
        for item in result["comparability_findings"]
    )


def test_committed_prism_run_receipt_binds_head_and_inputs():
    receipt = compare_runs._committed_run_receipt(ROOT / "runs" / "kisco-104700")

    assert len(receipt["commit"]) == 40
    assert len(receipt["input_tree_sha256"]) == 64
    assert any(item["path"].endswith("/run.yaml") for item in receipt["inputs"])


def test_missing_file_from_committed_run_tree_is_rejected(monkeypatch):
    original_git = compare_runs._git

    def committed_tree_with_missing_file(*args):
        result = original_git(*args)
        if args and args[0] == "ls-tree":
            result.stdout += "runs/kisco-104700/raw/deleted-from-worktree.json\0"
        return result

    monkeypatch.setattr(compare_runs, "_git", committed_tree_with_missing_file)
    with pytest.raises(
        compare_runs.RunComparisonError,
        match="committed run input is missing from worktree",
    ):
        compare_runs._committed_run_receipt(ROOT / "runs" / "kisco-104700")


def test_symlinked_run_argument_is_not_comparable(tmp_path):
    linked_run = tmp_path / "linked-run"
    linked_run.symlink_to(ROOT / "runs" / "kisco-104700", target_is_directory=True)

    def should_not_run(_path):
        raise AssertionError("symlinked run must not execute")

    result = compare_runs.compare_run_directories(
        linked_run,
        linked_run,
        executor=should_not_run,
    )

    assert result["status"] == compare_runs.STATUS_EXTERNAL_RUN_NOT_COMPARABLE
    assert all(
        "may not use symlinks" in item["detail"]
        for item in result["comparability_findings"]
    )


def test_symlinked_calibration_binding_is_rejected(tmp_path):
    artifact_link = tmp_path / "artifact-link.json"
    artifact_link.symlink_to(ROOT / "config" / "kr_steel_calibration_artifact.json")
    original_load = compare_runs._load_yaml_mapping

    def calibration_with_symlink(path: Path) -> dict:
        payload = original_load(path)
        if path.name == "run.yaml":
            payload["calibration"] = dict(payload["calibration"])
            payload["calibration"]["artifact"] = str(artifact_link)
        return payload

    with patch.object(compare_runs, "_load_yaml_mapping", calibration_with_symlink):
        with pytest.raises(compare_runs.RunComparisonError, match="may not use symlinks"):
            compare_runs._committed_run_receipt(ROOT / "runs" / "kisco-104700")


def test_uncommitted_run_inside_repository_is_not_comparable():
    with tempfile.TemporaryDirectory(prefix=".compare-runs-", dir=ROOT / "runs") as name:
        run_dir = Path(name) / "run"
        _write_run(run_dir)

        with pytest.raises(
            compare_runs.RunComparisonError,
            match="not committed at HEAD",
        ):
            compare_runs._committed_run_receipt(run_dir)
