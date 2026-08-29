from dataclasses import fields
from decimal import Decimal
import json
from pathlib import Path

import pytest

from scripts.run_skhynix_live_primary import _render_calibrated_probability_summary
from valuation_engine.continuous_probability_snapshot import ContinuousProbabilityCalibrationSnapshot
from valuation_engine.records import CalibrationStatus
from valuation_engine.skhynix_continuous_live_primary import (
    EXTERNAL_PROBABILITY_SOURCE,
    build_skhynix_live_primary_config,
    run_skhynix_live_primary,
)
from valuation_engine.skhynix_continuous_probability import (
    DEFAULT_PROVENANCE_PATH,
    EXPECTED_DATASET_SHA256,
    CurrentConditioning,
    build_skhynix_continuous_probability_snapshot,
)
from valuation_engine.skhynix_live_primary import load_skhynix_snapshot
from valuation_engine.street import summarize_street_reports
from valuation_engine.strict_live_runtime import CANONICAL_ENTRYPOINT_ID, require_canonical_live_result


def _current_conditioning() -> CurrentConditioning:
    snapshot = load_skhynix_snapshot()
    row = snapshot.payload["probability_conditioning"]
    return CurrentConditioning(
        revenue_growth=Decimal(str(row["revenue_growth"])),
        operating_margin=Decimal(str(row["operating_margin"])),
        cash_conversion=Decimal(str(row["cash_conversion"])),
        capex_intensity=Decimal(str(row["capex_intensity"])),
        source_ref=snapshot.sources["probability_numeric_snapshot"],
        first_seen_at=str(row["first_seen_at"]),
        source_hash=str(row["source_hash"]),
    )


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
    assert config.scenario_binding_spec.external_probability_source == EXTERNAL_PROBABILITY_SOURCE
    assert config.providers.calibration_loader is not None
    snapshot = config.providers.calibration_loader(None)
    field_names = {item.name for item in fields(ContinuousProbabilityCalibrationSnapshot)}
    forbidden_tokens = {
        "market_price",
        "target_price",
        "intrinsic_value",
        "expected_value",
        "valuation_gap",
        "return_target",
        "entry_price",
    }
    assert not field_names.intersection(forbidden_tokens)
    assert snapshot.dataset_hash == EXPECTED_DATASET_SHA256
    assert not snapshot.integrity_findings


def test_skhynix_continuous_probability_snapshot_replaces_legacy_boolean_mapping(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    snapshot = config.providers.calibration_loader(None)
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.probability_source == "continuous_financial_path_monte_carlo"
    assert len(snapshot.estimates) == 3
    assert sum((item.probability for item in snapshot.estimates), Decimal("0")) == Decimal("1")
    assert all(len(item.skill_windows) == 3 for item in snapshot.oos_diagnostics)
    rounded = tuple(round(float(item.probability), 3) for item in snapshot.estimates)
    assert rounded != (0.710, 0.286, 0.004)


def test_skhynix_report_artifact_renders_frozen_calibrated_probabilities(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    snapshot = config.providers.calibration_loader(None)
    rendered = _render_calibrated_probability_summary(
        "| **시나리오 가능성** | 미산출 |",
        snapshot,
        "CALIBRATED",
    )
    assert "미산출" not in rendered
    assert "하방 15.7%" in rendered
    assert "기준 43.9%" in rendered
    assert "상방 40.4%" in rendered
    assert "보정 완료·수치 가중 적용" in rendered


def test_skhynix_continuous_probability_rejects_lookahead_replay():
    with pytest.raises(PermissionError, match="after the requested snapshot cutoff"):
        build_skhynix_continuous_probability_snapshot(
            current=_current_conditioning(),
            as_of_date="2026-08-01",
        )


def test_skhynix_continuous_probability_freezes_conditioning_provenance(tmp_path: Path):
    payload = json.loads(DEFAULT_PROVENANCE_PATH.read_text(encoding="utf-8"))
    payload["current_conditioning_source_ref"] = "https://example.com/different-source"
    mutated = tmp_path / "provenance.json"
    mutated.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provenance artifact hash mismatch"):
        build_skhynix_continuous_probability_snapshot(
            current=_current_conditioning(),
            as_of_date="2026-08-29",
            provenance_path=mutated,
        )


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


def test_skhynix_strict_live_run_freezes_continuous_probability_weighting(tmp_path: Path):
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
    assert valuation.expected_value_per_share is not None
    assert result.data["bound_scenario_set"].numeric_weighting_allowed is True
    assert result.data["bound_scenario_set"].calibration_status is CalibrationStatus.CALIBRATED
    assert result.data["probability_distribution_status"] == "CALIBRATED"
    assert result.data.get("probability_calibration_snapshot_hash")
    assert result.data.get("probability_calibration_dataset_hash") == EXPECTED_DATASET_SHA256
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
