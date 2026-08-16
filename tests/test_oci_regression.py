from pathlib import Path
import pytest

from valuation_engine.audit import audit_model
from valuation_engine.config import load_company_config
from valuation_engine.engine import compare_to_market, run_valuation

CONFIG = Path(__file__).parents[1] / "examples" / "oci" / "company.yaml"


def test_oci_regression_matches_excel_v11():
    shares, market_price, scenarios, _ = load_company_config(CONFIG)
    result = run_valuation(scenarios, shares)
    values = {v.name: v.fair_value_per_share for v in result.scenarios}
    assert values["Bear"] == pytest.approx(122709.1691, abs=0.1)
    assert values["Base"] == pytest.approx(243343.5899, abs=0.1)
    assert values["Bull"] == pytest.approx(406697.4485, abs=0.1)
    assert values["AI/Space"] == pytest.approx(500500.7202, abs=0.1)
    assert result.expected_value_per_share == pytest.approx(291802.6044, abs=0.1)


def test_market_price_does_not_change_intrinsic_value():
    shares, _, scenarios, _ = load_company_config(CONFIG)
    result = run_valuation(scenarios, shares)
    assert result.expected_value_per_share == pytest.approx(291802.6044, abs=0.1)
    assert compare_to_market(result, 279000) != compare_to_market(result, 100000)
    assert result.expected_value_per_share == pytest.approx(291802.6044, abs=0.1)


def test_audit_passes():
    shares, _, scenarios, _ = load_company_config(CONFIG)
    assert audit_model(scenarios, shares)["pass"] is True
