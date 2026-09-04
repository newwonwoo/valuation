from dataclasses import fields
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest
import yaml

import valuation_engine.skhynix_live_primary as skhynix_live_primary_module
from valuation_engine.continuous_probability_snapshot import ContinuousProbabilityCalibrationSnapshot
from valuation_engine.records import CalibrationStatus, EvidenceSourceLayer
from valuation_engine.risk_adapters import TargetCapitalStructureMethod
from valuation_engine.skhynix_continuous_live_primary import (
    EXTERNAL_PROBABILITY_SOURCE,
    build_skhynix_live_primary_config,
    render_calibrated_probability_summary,
    run_skhynix_live_primary,
)
from valuation_engine.skhynix_continuous_probability import (
    DEFAULT_PROVENANCE_PATH,
    EXPECTED_DATASET_SHA256,
    CurrentConditioning,
    build_skhynix_continuous_probability_snapshot,
)
from valuation_engine.skhynix_beta_snapshot import (
    DEFAULT_BETA_SNAPSHOT_PATH,
    calculate_beta,
    load_skhynix_beta_snapshot,
)
from valuation_engine.skhynix_live_primary import (
    DEFAULT_POST_FREEZE_SNAPSHOT_PATH,
    DEFAULT_SNAPSHOT_PATH,
    load_skhynix_post_freeze_snapshot,
    load_skhynix_snapshot,
)
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


def test_skhynix_post_freeze_sources_are_not_loaded_during_config_build(
    tmp_path: Path,
    monkeypatch,
):
    calls = []

    def prohibited_early_load(path=None):
        calls.append(path)
        raise AssertionError("post-freeze snapshot loaded before its provider stage")

    monkeypatch.setattr(
        skhynix_live_primary_module,
        "load_skhynix_post_freeze_snapshot",
        prohibited_early_load,
    )
    config = build_skhynix_live_primary_config(tmp_path)
    assert calls == []
    with pytest.raises(AssertionError, match="post-freeze snapshot loaded"):
        config.providers.street_loader()


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
    rendered = render_calibrated_probability_summary(
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
    structure = inputs.target_capital_structure
    peer_capital = (
        (50537000000, 5044000000, 89.47),
        (66720000000, 4757580198, 368.79),
        (4962900000, 876900000, 216.62),
        (5140000000, 1129393151, 932.86),
    )
    expected_debt_weight = sum(
        debt / (debt + shares * price)
        for debt, shares, price in peer_capital
    ) / len(peer_capital)
    assert structure.method is TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE
    assert structure.as_of == "2026-08-28"
    assert snapshot.sources["half_year_filing"] in structure.source_refs
    assert sum("api.nasdaq.com/api/quote/" in ref for ref in structure.source_refs) == 4
    assert sum("sec.gov/Archives/edgar/data/" in ref for ref in structure.source_refs) == 5
    assert structure.equity_weight == pytest.approx(1 - expected_debt_weight)
    assert structure.debt_weight == pytest.approx(expected_debt_weight)
    assert structure.tax_rate == pytest.approx(
        28785762 / 122708355
    )


def test_skhynix_street_loader_uses_original_broker_report(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    reports = config.providers.street_loader()
    consensus = summarize_street_reports(reports)
    assert len(reports) == 1
    assert reports[0].broker == "Samsung Securities"
    assert "samsungpop.com" in reports[0].source_ref
    assert reports[0].published_date == "2026-07-30"
    assert consensus.report_count == 1
    assert consensus.mean_target_price == 3000000
    assert consensus.median_target_price == 3000000
    assert consensus.min_target_price == 3000000
    assert consensus.max_target_price == 3000000


def test_skhynix_market_and_beta_inputs_use_original_exchange_sources(tmp_path: Path):
    config = build_skhynix_live_primary_config(tmp_path)
    market = config.providers.market_loader()
    beta = config.providers.beta_loader(None)
    assert market.source_ref == "https://www.skhynix.com/ir/UI-FR-IR01/"
    assert market.price == 1647000
    assert market.as_of == "2026-09-04"
    assert all(
        "api.nasdaq.com/api/quote/" in peer.source_ref
        and peer.beta_standard_error is not None
        and "frozen series" in peer.estimation_method
        for level in beta.levels
        for peer in level.peers
    )
    assert any("sec.gov/Archives/edgar/data/" in ref for ref in beta.source_refs)
    expected_market_debt_to_equity = {
        "INTC": 50537000000 / (5044000000 * 89.47),
        "AVGO": 66720000000 / (4757580198 * 368.79),
        "MRVL": 4962900000 / (876900000 * 216.62),
        "MU": 5140000000 / (1129393151 * 932.86),
    }
    for level in beta.levels:
        for peer in level.peers:
            assert peer.debt / peer.equity == pytest.approx(
                expected_market_debt_to_equity[peer.peer_id]
            )


def test_skhynix_market_date_and_filed_wacc_bindings_fail_closed(
    tmp_path: Path,
    monkeypatch,
):
    payload = yaml.safe_load(
        DEFAULT_POST_FREEZE_SNAPSHOT_PATH.read_text(encoding="utf-8")
    )
    payload["market"]["as_of"] = "2026-09-03"
    relabelled_market = tmp_path / "relabelled_market.yaml"
    relabelled_market.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="market observation binding mismatch"):
        load_skhynix_post_freeze_snapshot(relabelled_market)

    copied_root = tmp_path / "copied-repository"
    copied_config = copied_root / "config"
    copied_config.mkdir(parents=True)
    market_path = DEFAULT_SNAPSHOT_PATH.parent / "skhynix_market_snapshot.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["price"] = 9999999
    mutated_market = json.dumps(market_payload, ensure_ascii=False, indent=2).encode(
        "utf-8"
    )
    (copied_config / "skhynix_market_snapshot.json").write_bytes(mutated_market)
    payload = yaml.safe_load(
        DEFAULT_POST_FREEZE_SNAPSHOT_PATH.read_text(encoding="utf-8")
    )
    payload["market"]["price"] = 9999999
    payload["market"]["snapshot_sha256"] = sha256(mutated_market).hexdigest()
    copied_snapshot = tmp_path / "copied_snapshot.yaml"
    copied_snapshot.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(skhynix_live_primary_module, "_REPO_ROOT", copied_root)
    with pytest.raises(ValueError, match="market snapshot is not independently registered"):
        load_skhynix_post_freeze_snapshot(copied_snapshot)

    monkeypatch.setattr(
        skhynix_live_primary_module,
        "_REPO_ROOT",
        DEFAULT_SNAPSHOT_PATH.parents[1],
    )

    payload = yaml.safe_load(
        DEFAULT_POST_FREEZE_SNAPSHOT_PATH.read_text(encoding="utf-8")
    )
    payload["street"].update(
        consensus_target_price=99999999,
        median_target_price=99999999,
        min_target_price=99999999,
        max_target_price=99999999,
        as_of="2026-09-04",
    )
    relabelled_street = tmp_path / "relabelled_street.yaml"
    relabelled_street.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Street record is not independently registered"):
        load_skhynix_post_freeze_snapshot(relabelled_street)

    payload = yaml.safe_load(DEFAULT_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    payload["risk"]["target_debt_weight"] = 0.1
    relabelled_wacc = tmp_path / "relabelled_wacc.yaml"
    relabelled_wacc.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="capital-structure or tax binding mismatch"):
        load_skhynix_snapshot(relabelled_wacc)

    payload = yaml.safe_load(DEFAULT_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    peer = payload["risk"]["peer_market_capital"]["MU"]
    peer["filed_share_count_text"] = (
        "The number of outstanding shares of the registrant’s common stock "
        "as of June 17, 2026 was 1."
    )
    peer["filed_share_count_text_sha256"] = sha256(
        peer["filed_share_count_text"].encode("utf-8")
    ).hexdigest()
    relabelled_shares = tmp_path / "relabelled_shares.yaml"
    relabelled_shares.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="filed share-count payload hash mismatch"):
        load_skhynix_snapshot(relabelled_shares)


def test_skhynix_active_underwriting_keeps_original_issuer_source_provenance(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)
    snapshot = load_skhynix_snapshot()
    underwriting = tuple(
        record
        for record in result.data["evidence_ledger"].active()
        if record.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
    assert underwriting
    assert {record.source_ref for record in underwriting} <= {
        snapshot.sources["q2_results"],
        snapshot.sources["half_year_filing"],
    }
    assert all("not stated by" in record.notes for record in underwriting)
    assert all(
        record.source_ref != snapshot.sources["underwriting"]
        for record in underwriting
    )


def test_skhynix_beta_snapshot_replays_frozen_nasdaq_series_and_sec_capital():
    snapshot = load_skhynix_beta_snapshot()
    expected = {
        "INTC": (1.2146946732804864, 0.4899702354982888),
        "AVGO": (1.5087543224655808, 0.7608534513233969),
        "MRVL": (1.6711366521783315, 0.2678074208379201),
        "MU": (1.514005608055587, 0.05103053889837576),
    }
    assert snapshot.as_of == "2026-08-28"
    for peer_id, (beta, debt_to_equity) in expected.items():
        estimate = snapshot.estimate(peer_id)
        assert estimate.observations == 260
        assert estimate.beta == pytest.approx(beta, abs=1e-12)
        assert estimate.book_debt_to_equity == pytest.approx(debt_to_equity, abs=1e-12)
        assert estimate.debt >= 0
        assert estimate.ending_price > 0
        assert estimate.series_hash


def test_skhynix_beta_snapshot_rejects_relabelled_or_tampered_numbers(tmp_path: Path):
    payload = json.loads(DEFAULT_BETA_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    payload["peers"]["INTC"]["weekly_close"][100][1] = "9999"
    mutated = tmp_path / "beta.json"
    mutated.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen beta does not replay"):
        load_skhynix_beta_snapshot(mutated)

    payload = json.loads(DEFAULT_BETA_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    capital = payload["peers"]["INTC"]["capital"]
    capital["debt_facts"][0]["value"] *= 10
    capital["debt"] = sum(item["value"] for item in capital["debt_facts"])
    capital["debt_to_equity"] = capital["debt"] / capital["equity"]
    mutated_capital = tmp_path / "capital.json"
    mutated_capital.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="capital fact records are not registered"):
        load_skhynix_beta_snapshot(mutated_capital)

    payload = json.loads(DEFAULT_BETA_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    payload["peers"]["INTC"]["weekly_close"][100][1] = "9999"
    stock = tuple(
        (str(date), float(close))
        for date, close in payload["peers"]["INTC"]["weekly_close"]
    )
    benchmark = tuple(
        (str(date), float(close))
        for date, close in payload["benchmark_weekly_close"]
    )
    payload["peers"]["INTC"]["ols"] = calculate_beta(stock, benchmark)
    self_recomputed = tmp_path / "self_recomputed_beta.json"
    self_recomputed.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not independently registered"):
        load_skhynix_beta_snapshot(self_recomputed)


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
    assert result.data["street_comparison"].consensus.report_count == 1
    assert result.data["market_comparison"].observation.price == 1647000
    assert result.data.get("final_report")


def test_announced_buyback_is_not_committed_as_intrinsic_input(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)
    compiled = result.data["compiled_assumption_set"]
    keys = {item.key for item in compiled.assumptions}
    assert "planned_buyback_cash" not in keys
    assert "planned_buyback_shares" not in keys
    assert "diluted_shares" in keys
