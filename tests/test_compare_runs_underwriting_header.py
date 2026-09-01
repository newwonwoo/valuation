from __future__ import annotations

from pathlib import Path

import yaml

from tests.test_compare_runs import _fake_executor, _write_run, compare_runs


def _rewrite_underwriting_header(run_dir: Path, key: str, value: str) -> None:
    path = run_dir / "declarations" / "underwriting.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload[key] = value
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_underwriting_target_header_is_structural(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)
    _rewrite_underwriting_header(run_b, "target_id", "KR:DART:99999999")

    calls = 0

    def should_not_run(_path):
        nonlocal calls
        calls += 1
        raise AssertionError("underwriting header mismatch must stop before execution")

    result = compare_runs.compare_run_directories(
        run_a, run_b, executor=should_not_run
    )

    assert result["status"] == compare_runs.STATUS_RECONCILIATION_REQUIRED
    assert calls == 0
    assert any(
        item["code"] == "UNDERWRITING_HEADER_CONTRACT_MISMATCH"
        for item in result["structural_findings"]
    )


def test_inherited_underwriting_source_ref_is_metadata_difference(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    _write_run(run_a)
    _write_run(run_b)
    _rewrite_underwriting_header(
        run_b, "source_ref", "https://example.com/revised-source"
    )

    result = compare_runs.compare_run_directories(
        run_a, run_b, executor=_fake_executor
    )

    assert result["status"] == compare_runs.STATUS_CONSISTENT
    assert result["decomposition_order"][0] == "__underwriting_header__.source_ref"
    diff = result["judgment_differences"][0]
    assert diff["metadata_only"] is True
    assert diff["a_value"] == "https://example.com/source"
    assert diff["b_value"] == "https://example.com/revised-source"
    attribution = result["attribution"][0]
    assert attribution["base_delta_per_share"] == "0"
    assert attribution["expected_delta_per_share"] == "0.0"
