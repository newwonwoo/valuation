from pathlib import Path


def test_runtime_hardening_contract_is_wired_into_orchestrator():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "valuation_engine" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "read_only_data_view" in source
    assert "evidence_ledgers" in source
    assert "sanitize_runtime_text" in source
    assert "stage adapter contract violation" in source
