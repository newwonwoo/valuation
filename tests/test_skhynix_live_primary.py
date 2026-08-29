from pathlib import Path

from valuation_engine.skhynix_live_primary import (
    build_skhynix_live_primary_config,
    load_skhynix_snapshot,
    run_skhynix_live_primary,
)
from valuation_engine.street import summarize_street_reports
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


def test_skhynix_wacc_inputs_use_original_public_sources(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    inputs = config.providers.wacc_loader(None)
    snapshot = load_skhynix_snapshot()
    assert inputs.risk_free_rate.source_ref == snapshot.sources["risk_free"]
    assert inputs.equity_risk_premium.source_ref == snapshot.sources["equity_risk_premium"]
    assert inputs.marginal_pre_tax_cost_of_debt.source_ref == snapshot.sources["debt_cost"]
    assert inputs.risk_free_rate.source_ref != snapshot.sources["underwriting"]
    assert inputs.equity_risk_premium.source_ref != snapshot.sources["underwriting"]
    assert inputs.marginal_pre_tax_cost_of_debt.source_ref != snapshot.sources["underwriting"]


def test_skhynix_street_loader_preserves_aggregate_consensus(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    consensus = summarize_street_reports(config.providers.street_loader())
    assert consensus.report_count == 39
    assert consensus.mean_target_price == 3164332
    assert consensus.median_target_price == 3150000
    assert consensus.min_target_price == 1200000
    assert consensus.max_target_price == 5300000


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
    assert valuation.reporting_unit == "KRW"
    assert {item.scenario_id for item in valuation.scenarios} == {"Down", "Core", "Bull"}
    assert valuation.expected_value_per_share is None
    assert result.data["bound_scenario_set"].numeric_weighting_allowed is False
    assert result.data["probability_distribution_status"] == "DESCRIPTIVE_ONLY"
    assert result.data["street_comparison"].consensus.report_count == 39
    assert result.data["market_comparison"].observation.price == 1653000
    assert result.data.get("final_report")


def test_announced_buyback_is_not_committed_as_intrinsic_input(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)
    compiled = result.data["compiled_assumption_set"]
    keys = {item.key for item in compiled.assumptions}
    assert "planned_buyback_cash" not in keys
    assert "planned_buyback_shares" not in keys
    assert "diluted_shares" in keys
