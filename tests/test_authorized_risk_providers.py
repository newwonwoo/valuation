from decimal import Decimal

import pytest

from valuation_engine.authorized_risk_providers import (
    AuthorizedBetaLevelSource,
    AuthorizedKRRiskProviderPack,
    AuthorizedPeerBetaSource,
    AuthorizedRiskProviderError,
    MarginalDebtBenchmark,
    PeerCapitalObservation,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.official_market_data import BetaEstimate, CountryRisk, SeriesObservation
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.risk import BetaLevelName
from valuation_engine.risk_adapters import (
    live_hierarchical_beta_adapter,
    live_wacc_validation_adapter,
)


def _evidence(evidence_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="KR:DART:00000000",
        metric="peer_selection",
        value=1,
        unit="dimensionless",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-20",
        source_name="primary evidence",
        source_ref=f"evidence://{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="company",
    )


def _peer(peer_id: str, beta: float, debt: float, equity: float) -> AuthorizedPeerBetaSource:
    return AuthorizedPeerBetaSource(
        peer_id=peer_id,
        beta=BetaEstimate(
            code=peer_id,
            benchmark="코스피",
            beta=beta,
            observations=250,
            start_date="2025-08-20",
            end_date="2026-08-20",
        ),
        capital=PeerCapitalObservation(
            peer_id=peer_id,
            debt=debt,
            equity_market_value=equity,
            tax_rate=0.24,
            as_of="2026-06-30",
            source_ref=f"opendart://{peer_id}/capital",
        ),
        beta_source_ref=f"krx://{peer_id}/beta",
        beta_standard_error=0.12,
    )


def _pack() -> AuthorizedKRRiskProviderPack:
    levels = (
        AuthorizedBetaLevelSource(
            BetaLevelName.L1_BROAD_SECTOR,
            (_peer("P1", 1.00, 20, 80),),
            "broad sector prior",
            ("E1",),
            ("industrial cyclicality",),
        ),
        AuthorizedBetaLevelSource(
            BetaLevelName.L2_INDUSTRY,
            (_peer("P2", 1.10, 30, 70),),
            "industry peer",
            ("E2",),
            ("order cycle",),
        ),
        AuthorizedBetaLevelSource(
            BetaLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
            (_peer("P3", 1.20, 25, 75),),
            "risk-driver peer",
            ("E3",),
            ("operating leverage",),
        ),
        AuthorizedBetaLevelSource(
            BetaLevelName.L4_ECONOMIC_TWINS,
            (_peer("P4", 1.15, 35, 65),),
            "economic twin",
            ("E4",),
            ("capacity intensity", "lead time"),
        ),
    )
    return AuthorizedKRRiskProviderPack(
        beta_levels=levels,
        risk_free_rate=SeriesObservation(
            time="20260820",
            value=3.10,
            unit="연%",
            name="국고채 10년",
            source_ref="https://ecos.bok.or.kr/api/risk-free",
        ),
        country_risk=CountryRisk(
            country="Korea",
            as_of="2026-08-01",
            mature_market_erp=0.0508,
            country_risk_premium=0.0057,
            total_equity_risk_premium=0.0565,
            adjusted_default_spread=0.0030,
            corporate_tax_rate=0.24,
            rating="AA",
        ),
        marginal_debt=MarginalDebtBenchmark(
            series=SeriesObservation(
                time="20260820",
                value=4.20,
                unit="연%",
                name="회사채 AA- 3년",
                source_ref="https://ecos.bok.or.kr/api/corp-aa-minus",
            ),
            credit_rating="AA-",
            maturity="3Y",
            rating_source_ref="opendart://issuer/credit-rating",
        ),
    )


def test_provider_builds_peer_normalized_structure_without_target_market_cap():
    structure = _pack().target_capital_structure()
    assert structure.debt_weight == pytest.approx((0.20 + 0.30 + 0.25 + 0.35) / 4)
    assert structure.equity_weight == pytest.approx(1 - structure.debt_weight)
    assert structure.tax_rate == 0.24
    assert "target current market capitalization is not used" in structure.rationale


def test_provider_converts_official_rates_without_country_risk_double_count():
    inputs = _pack().wacc_inputs()
    assert inputs.risk_free_rate.value == pytest.approx(0.031)
    assert inputs.equity_risk_premium.value == pytest.approx(0.0508)
    assert inputs.country_risk_premium is not None
    assert inputs.country_risk_premium.value == pytest.approx(0.0057)
    assert inputs.marginal_pre_tax_cost_of_debt.value == pytest.approx(0.042)


def test_provider_pipeline_runs_through_existing_beta_wacc_contracts():
    pack = _pack()
    ledger = EvidenceLedger(tuple(_evidence(item) for item in ("E1", "E2", "E3", "E4")))
    result = run_controlled_workflow(
        run_id="AUTHORIZED-RISK",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        adapters={
            "HIERARCHICAL_BETA_ESTIMATION": live_hierarchical_beta_adapter(
                loader=lambda _: pack.beta_universe()
            ),
            "WACC_VALIDATION": live_wacc_validation_adapter(
                loader=lambda _: pack.wacc_inputs()
            ),
        },
        required_stages=("HIERARCHICAL_BETA_ESTIMATION", "WACC_VALIDATION"),
        initial_data={"evidence_ledger": ledger},
    )
    assert result.blocked_reasons == ()
    assert [trace.status for trace in result.stage_traces] == [StageStatus.PASS, StageStatus.PASS]
    assert result.data["target_levered_beta"] > 0
    assert 0 < result.data["wacc"] < 1


def test_provider_rejects_implicit_or_unknown_rate_units():
    pack = _pack()
    bad = AuthorizedKRRiskProviderPack(
        beta_levels=pack.beta_levels,
        risk_free_rate=SeriesObservation(
            time="20260820",
            value=3.1,
            unit="unknown",
            name="risk free",
            source_ref="source",
        ),
        country_risk=pack.country_risk,
        marginal_debt=pack.marginal_debt,
    )
    with pytest.raises(AuthorizedRiskProviderError, match="explicit percent/ratio"):
        bad.wacc_inputs()


def test_nonzero_country_risk_lambda_requires_exposure_source():
    with pytest.raises(ValueError, match="exposure source"):
        _pack().wacc_inputs(country_risk_lambda=0.5)
    inputs = _pack().wacc_inputs(
        country_risk_lambda=0.5,
        country_risk_exposure_source_ref="filing://foreign-revenue-exposure",
    )
    assert inputs.country_risk_lambda == 0.5
