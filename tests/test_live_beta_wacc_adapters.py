from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext, run_controlled_workflow
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.risk import BetaLevelName
from valuation_engine.risk_adapters import (
    AdditionalRiskBasis,
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
    live_hierarchical_beta_adapter,
    live_wacc_validation_adapter,
)
from valuation_engine.wacc import CustomerAdvanceCreditEvidence


def evidence(evidence_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="peer_selection",
        value=1,
        unit="dimensionless",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref=f"source#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def ledger() -> EvidenceLedger:
    return EvidenceLedger(
        tuple(evidence(item) for item in ("E1", "E2", "E3", "E4", "ERISK", "EFUND"))
    )


def structure(*, debt_weight=0.25, source_refs=("CAPITAL:1",)) -> LiveCapitalStructureObservation:
    return LiveCapitalStructureObservation(
        equity_weight=1.0 - debt_weight,
        debt_weight=debt_weight,
        tax_rate=0.22,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of="2026-08-22",
        source_refs=source_refs,
        rationale="normalized peer market-value structure across the cycle",
    )


def peer(peer_id: str, *, source_ref: str) -> LivePeerBetaObservation:
    return LivePeerBetaObservation(
        peer_id=peer_id,
        levered_beta=1.1,
        debt=20.0,
        equity=80.0,
        tax_rate=0.22,
        benchmark_id="KOSPI_TOTAL_RETURN",
        return_frequency="weekly",
        estimation_window_months=60,
        as_of="2026-08-22",
        source_ref=source_ref,
        beta_standard_error=0.12,
        estimation_method="weekly_market_model_blume_adjusted",
    )


def universe(*, capital=None, source_refs=("BETA:UNIVERSE",)) -> LiveBetaUniverse:
    capital = capital or structure()
    levels = (
        LiveBetaLevelObservation(
            BetaLevelName.L1_BROAD_SECTOR,
            (peer("L1", source_ref="BETA:L1"),),
            "broad industrial risk prior",
            ("E1",),
            ("industrial cyclicality",),
        ),
        LiveBetaLevelObservation(
            BetaLevelName.L2_INDUSTRY,
            (peer("L2", source_ref="BETA:L2"),),
            "electrical-equipment industry",
            ("E2",),
            ("grid capex",),
        ),
        LiveBetaLevelObservation(
            BetaLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
            (peer("L3", source_ref="BETA:L3"),),
            "long-cycle backlog equipment",
            ("E3",),
            ("backlog duration", "operating leverage"),
        ),
        LiveBetaLevelObservation(
            BetaLevelName.L4_ECONOMIC_TWINS,
            (peer("L4", source_ref="BETA:L4"),),
            "closest transformer economic twin",
            ("E4",),
            ("lead time", "customer concentration", "capacity intensity"),
        ),
    )
    return LiveBetaUniverse(
        levels=levels,
        target_capital_structure=capital,
        universe_rationale="four-level systematic-risk hierarchy",
        source_refs=source_refs,
    )


def rate(value: float, name: str, *, currency="KRW") -> RateObservation:
    return RateObservation(
        value=value,
        currency=currency,
        as_of="2026-08-22",
        source_ref=f"RATE:{name}",
        methodology=name,
    )


def wacc_inputs(*, capital=None, currency="KRW", funding=False, additional=0.0):
    capital = capital or structure()
    credit = (
        CustomerAdvanceCreditEvidence(True, True, True, True, True, True)
        if funding
        else None
    )
    return LiveWACCInputs(
        cash_flow_currency=currency,
        risk_free_rate=rate(0.035, "currency_matched_government_curve", currency=currency),
        equity_risk_premium=rate(0.055, "market_erp", currency=currency),
        marginal_pre_tax_cost_of_debt=rate(0.052, "marginal_bond_or_loan_cost", currency=currency),
        target_capital_structure=capital,
        country_risk_premium=rate(0.01, "country_risk", currency=currency),
        country_risk_lambda=0.30,
        country_risk_source_ref="EXPOSURE:1",
        additional_risk_premium=additional,
        additional_risk_basis=(
            AdditionalRiskBasis.EVIDENCED_LIQUIDITY
            if additional
            else AdditionalRiskBasis.NONE
        ),
        additional_risk_evidence_ids=(("ERISK",) if additional else ()),
        funding_credit_evidence_ids=(("EFUND",) if funding else ()),
        customer_advance_credit_evidence=credit,
        terminal_growth=0.025,
        terminal_roic=0.10,
    )


def test_live_beta_to_wacc_pipeline_passes_with_same_target_structure():
    result = run_controlled_workflow(
        run_id="RISK",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        adapters={
            "HIERARCHICAL_BETA_ESTIMATION": live_hierarchical_beta_adapter(
                loader=lambda context: universe()
            ),
            "WACC_VALIDATION": live_wacc_validation_adapter(
                loader=lambda context: wacc_inputs()
            ),
        },
        required_stages=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        initial_data={"evidence_ledger": ledger()},
    )
    assert result.blocked_reasons == ()
    assert [item.status for item in result.stage_traces] == [StageStatus.PASS, StageStatus.PASS]
    assert result.data["target_levered_beta"] > 0
    assert 0 < result.data["wacc"] < 1
    assert result.data["beta_snapshot_hash"]
    assert result.data["wacc_snapshot_hash"]


def test_target_current_market_fields_are_blocked_before_beta():
    result = run_controlled_workflow(
        run_id="LEAK",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("HIERARCHICAL_BETA_ESTIMATION",),
        adapters={
            "HIERARCHICAL_BETA_ESTIMATION": live_hierarchical_beta_adapter(
                loader=lambda context: universe()
            )
        },
        required_stages=("HIERARCHICAL_BETA_ESTIMATION",),
        initial_data={"evidence_ledger": ledger(), "current_market_price": 100000},
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.BLOCKED
    assert "target Street/market" in result.stage_traces[0].rationale


def test_peer_selection_must_reference_active_evidence():
    bad = universe()
    bad_levels = list(bad.levels)
    bad_levels[-1] = LiveBetaLevelObservation(
        BetaLevelName.L4_ECONOMIC_TWINS,
        bad_levels[-1].peers,
        bad_levels[-1].selection_rationale,
        ("INVENTED",),
        bad_levels[-1].risk_driver_features,
    )
    result = live_hierarchical_beta_adapter(
        loader=lambda context: LiveBetaUniverse(
            tuple(bad_levels),
            bad.target_capital_structure,
            bad.universe_rationale,
            bad.source_refs,
        )
    )(OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()}))
    assert result.status is StageStatus.BLOCKED
    assert "unknown Evidence IDs" in result.rationale


def test_beta_peers_must_use_one_normalized_return_convention():
    bad = universe()
    levels = list(bad.levels)
    l4_peer = levels[-1].peers[0]
    levels[-1] = LiveBetaLevelObservation(
        levels[-1].level,
        (
            LivePeerBetaObservation(
                peer_id=l4_peer.peer_id,
                levered_beta=l4_peer.levered_beta,
                debt=l4_peer.debt,
                equity=l4_peer.equity,
                tax_rate=l4_peer.tax_rate,
                benchmark_id="DIFFERENT_INDEX",
                return_frequency=l4_peer.return_frequency,
                estimation_window_months=l4_peer.estimation_window_months,
                as_of=l4_peer.as_of,
                source_ref=l4_peer.source_ref,
                beta_standard_error=l4_peer.beta_standard_error,
                estimation_method=l4_peer.estimation_method,
            ),
        ),
        levels[-1].selection_rationale,
        levels[-1].selection_evidence_ids,
        levels[-1].risk_driver_features,
    )
    result = live_hierarchical_beta_adapter(
        loader=lambda context: LiveBetaUniverse(
            tuple(levels), bad.target_capital_structure, bad.universe_rationale, bad.source_refs
        )
    )(OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()}))
    assert result.status is StageStatus.BLOCKED
    assert "normalized benchmark" in result.rationale


def test_beta_and_wacc_structure_mismatch_is_blocked():
    beta_stage = live_hierarchical_beta_adapter(loader=lambda context: universe())(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()})
    )
    assert beta_stage.status is StageStatus.PASS
    context = OrchestratorContext(
        "R",
        ExecutionMode.LIVE_PRIMARY,
        {"evidence_ledger": ledger(), **beta_stage.outputs},
    )
    result = live_wacc_validation_adapter(
        loader=lambda context: wacc_inputs(capital=structure(debt_weight=0.40))
    )(context)
    assert result.status is StageStatus.BLOCKED
    assert "same target capital structure" in result.rationale


def test_currency_mismatch_is_blocked():
    beta_stage = live_hierarchical_beta_adapter(loader=lambda context: universe())(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()})
    )
    context = OrchestratorContext(
        "R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger(), **beta_stage.outputs}
    )

    def bad_loader(context):
        good = wacc_inputs()
        return LiveWACCInputs(
            cash_flow_currency="KRW",
            risk_free_rate=good.risk_free_rate,
            equity_risk_premium=rate(0.055, "market_erp", currency="USD"),
            marginal_pre_tax_cost_of_debt=good.marginal_pre_tax_cost_of_debt,
            target_capital_structure=good.target_capital_structure,
        )

    result = live_wacc_validation_adapter(loader=bad_loader)(context)
    assert result.status is StageStatus.BLOCKED
    assert "cash-flow currency" in result.rationale


def test_additional_risk_requires_explicit_evidence():
    beta_stage = live_hierarchical_beta_adapter(loader=lambda context: universe())(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()})
    )
    context = OrchestratorContext(
        "R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger(), **beta_stage.outputs}
    )

    def bad_loader(context):
        good = wacc_inputs()
        return LiveWACCInputs(
            cash_flow_currency=good.cash_flow_currency,
            risk_free_rate=good.risk_free_rate,
            equity_risk_premium=good.equity_risk_premium,
            marginal_pre_tax_cost_of_debt=good.marginal_pre_tax_cost_of_debt,
            target_capital_structure=good.target_capital_structure,
            additional_risk_premium=0.01,
            additional_risk_basis=AdditionalRiskBasis.EVIDENCED_LIQUIDITY,
            additional_risk_evidence_ids=(),
        )

    result = live_wacc_validation_adapter(loader=bad_loader)(context)
    assert result.status is StageStatus.BLOCKED
    assert "requires Evidence IDs" in result.rationale


def test_customer_advance_credit_is_candidate_not_automatic_wacc_cut():
    beta_stage = live_hierarchical_beta_adapter(loader=lambda context: universe())(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()})
    )
    base_context = {
        "evidence_ledger": ledger(),
        **beta_stage.outputs,
    }
    without = live_wacc_validation_adapter(loader=lambda context: wacc_inputs(funding=False))(
        OrchestratorContext("R1", ExecutionMode.LIVE_PRIMARY, dict(base_context))
    )
    with_credit = live_wacc_validation_adapter(loader=lambda context: wacc_inputs(funding=True))(
        OrchestratorContext("R2", ExecutionMode.LIVE_PRIMARY, dict(base_context))
    )
    assert without.status is StageStatus.PASS
    assert with_credit.status is StageStatus.PASS
    assert without.outputs["wacc"] == with_credit.outputs["wacc"]
    assert not without.outputs["customer_advance_credit_supports_reduction_candidate"]
    assert with_credit.outputs["customer_advance_credit_supports_reduction_candidate"]


def test_beta_snapshot_hash_ignores_source_reference_order():
    one = live_hierarchical_beta_adapter(
        loader=lambda context: universe(
            capital=structure(source_refs=("CAPITAL:B", "CAPITAL:A")),
            source_refs=("BETA:B", "BETA:A"),
        )
    )(OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()}))
    two = live_hierarchical_beta_adapter(
        loader=lambda context: universe(
            capital=structure(source_refs=("CAPITAL:A", "CAPITAL:B")),
            source_refs=("BETA:A", "BETA:B"),
        )
    )(OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, {"evidence_ledger": ledger()}))
    assert one.status is StageStatus.PASS
    assert two.status is StageStatus.PASS
    assert one.outputs["beta_snapshot_hash"] == two.outputs["beta_snapshot_hash"]
