from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.test_compare_runs import _fake_executor, _write_run, compare_runs


def _set_calibration(run_dir: Path, artifact_body: str) -> None:
    config_path = run_dir / "run.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["calibration"] = {
        "cohort_key": "kr.test.cohort",
        "forecast_class": "5y_path",
        "external_probability_source": "continuous_v1",
        "artifact": "calibration.json",
        "provenance": "provenance.json",
        "conditioning": "conditioning.json",
        "credible_level": "0.90",
        "constants": {
            "expected_artifact_sha256": "artifact-hash",
            "expected_provenance_artifact_sha256": "provenance-artifact-hash",
            "expected_dataset_sha256": "dataset-hash",
            "expected_provenance_hash": "provenance-hash",
            "expected_source_row_count": 10,
            "expected_source_company_count": 5,
            "excluded_ticker": "010130",
        },
    }
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    (run_dir / "calibration.json").write_text(artifact_body, encoding="utf-8")
    (run_dir / "provenance.json").write_text(
        '{"same": true}', encoding="utf-8"
    )
    (run_dir / "conditioning.json").write_text(
        '{"same": true}', encoding="utf-8"
    )


def test_complete_calibration_file_identity_is_structural(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)
    _set_calibration(run_a, '{"version": 1}')
    _set_calibration(run_b, '{"version": 2}')

    calls = 0

    def should_not_run(_path):
        nonlocal calls
        calls += 1
        raise AssertionError("calibration mismatch must stop before execution")

    result = compare_runs.compare_run_directories(
        run_a, run_b, executor=should_not_run
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert calls == 0
    assert any(
        item["code"] == "CALIBRATION_CONTRACT_MISMATCH"
        for item in result["structural_findings"]
    )


def test_complete_segment_declaration_is_structural(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)
    base = {
        "target_id": "KR:DART:00000000",
        "as_of": "2026-09-01",
        "source_ref": "https://example.com/filing",
        "segments": [
            {
                "segment_id": "core",
                "disclosed_name": "철강부문",
                "ksic_code": "241",
                "rationale": "classification rationale long enough A",
            },
            {
                "segment_id": "other",
                "disclosed_name": "기타부문",
                "ksic_code": "521",
                "rationale": "classification rationale long enough B",
            },
        ],
    }
    (run_a / "declarations" / "segments.yaml").write_text(
        yaml.safe_dump(base, sort_keys=False), encoding="utf-8"
    )
    changed = dict(base)
    changed["source_ref"] = "https://example.com/other-filing"
    (run_b / "declarations" / "segments.yaml").write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8"
    )

    def should_not_run(_path):
        raise AssertionError("segment mismatch must stop before execution")

    result = compare_runs.compare_run_directories(
        run_a, run_b, executor=should_not_run
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert any(
        item["code"] == "SEGMENT_CONTRACT_MISMATCH"
        for item in result["structural_findings"]
    )


def test_no_underwriting_difference_checks_non_base_residuals(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)

    def scenario_shift_executor(run_dir: Path):
        response = _fake_executor(run_dir)
        result = response[3]
        if run_dir.name == "b":
            scenarios = result.data["generic_valuation_result"].scenarios
            scenarios[0].value_per_share += Decimal("0.02")
            probabilities = result.data["bound_scenario_set"].scenarios
            values = {
                row.scenario_id: row.value_per_share for row in scenarios
            }
            result.data["generic_valuation_result"].expected_value_per_share = sum(
                values[row.scenario_id] * row.probability
                for row in probabilities
            )
        return response

    result = compare_runs.compare_run_directories(
        run_a,
        run_b,
        executor=scenario_shift_executor,
        residual_tolerance=Decimal("0.01"),
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert result["residual"]["base_value_per_share"] == "0"
    assert result["residual"]["scenario_values"]["Down"] == "0.02"
    assert any(
        item["code"] == "UNATTRIBUTED_VALUATION_RESIDUAL"
        for item in result["threshold_findings"]
    )


def test_text_report_preserves_residual_precision_and_scenarios():
    report = compare_runs.render_text_report(
        {
            "status": compare_runs.STATUS_RECONCILIATION_REQUIRED,
            "structural_findings": [],
            "threshold_findings": [],
            "attribution": [],
            "residual": {
                "base_value_per_share": "0.02",
                "expected_value_per_share": "0.03",
                "scenario_values": {
                    "Down": "0.02",
                    "Base": "0",
                    "Bull": "-0.04",
                },
            },
        }
    )

    assert "Base 0.02 / Expected 0.03" in report
    assert "Down=0.02" in report
    assert "Bull=-0.04" in report


def test_cli_threshold_flags_are_human_units_and_exit_is_distinct(monkeypatch):
    captured = {}

    def fake_compare(_a, _b, **kwargs):
        captured.update(kwargs)
        return {"status": compare_runs.STATUS_RECONCILIATION_REQUIRED}

    monkeypatch.setattr(compare_runs, "compare_run_directories", fake_compare)
    exit_code = compare_runs.main(
        [
            "a",
            "b",
            "--base-threshold-pct",
            "20",
            "--probability-threshold-pp",
            "10",
            "--wacc-threshold-pp",
            "1",
            "--json",
        ]
    )

    assert captured["base_threshold"] == Decimal("0.20")
    assert captured["probability_threshold"] == Decimal("0.10")
    assert captured["wacc_threshold"] == Decimal("0.01")
    assert exit_code == compare_runs.EXIT_RECONCILIATION_REQUIRED == 3

    with pytest.raises(SystemExit) as exc:
        compare_runs._parser().parse_args([])
    assert exc.value.code == 2
