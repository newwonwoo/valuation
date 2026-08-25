from pathlib import Path


def test_installed_wheel_validator_is_repository_only_bootstrap():
    text = (
        Path(__file__).resolve().parents[1] / "scripts" / "validate_installed_wheel.py"
    ).read_text(encoding="utf-8")
    assert "PYTHONPATH" not in text
    assert "valuation_engine._registry_data" in text
    assert "runtime_registry_path" in text
    assert "41" in text
