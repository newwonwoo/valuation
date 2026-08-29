from pathlib import Path

from valuation_engine.skhynix_live_primary import (
    build_skhynix_live_primary_config,
    run_skhynix_live_primary,
)
from valuation_engine.strict_live_runtime import CANONICAL_ENTRYPOINT_ID, require_canonical_live_result


def test_skhynix_config_is_price_isolated_before_runtime(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    forbidden = {
        "current_market_price",
        "market_price",
        "target_price",
        "consensus_target",
        "target_multiple",
        "street_reference",
    }
    assert forbidden.isdisjoint(config.initial_data)
    assert config.scenario_binding_spec.probability_key is None
    assert config.providers.calibration_loader is None


def test_skhynix_strict_live_run_freezes_without_uncalibrated_weighting(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)

    assert not result.blocked_reasons
    assert result.freeze_token is not None
    assert result.data["canonical_entrypoint_id"] == CANONICAL_ENTRYPOINT_ID
    assert result.data.get("execution_attestation_hash")
    assert authority.stage_receipts
    assert authority.execution_attestation is not None

    valuation = result.data["generic_valuation_result"]
    assert {item.scenario_id for item in valuation.scenarios} == {"Down", "Core", "Bull"}
    assert valuation.expected_value_per_share is None
    assert result.data["bound_scenario_set"].numeric_weighting_allowed is False
    assert result.data["probability_distribution_status"] == "DESCRIPTIVE_ONLY"
    assert result.data.get("final_report")


def test_announced_buyback_is_not_committed_as_intrinsic_input(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)
    compiled = result.data["compiled_assumption_set"]
    keys = {item.key for item in compiled.assumptions}
    assert "planned_buyback_cash" not in keys
    assert "planned_buyback_shares" not in keys
    assert "diluted_shares" in keys
