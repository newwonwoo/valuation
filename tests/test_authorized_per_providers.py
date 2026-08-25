from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.authorized_per_providers import (
    AuthorizedPERLevelSource,
    AuthorizedPERProviderError,
    AuthorizedPERProviderPack,
    AuthorizedPeerPERSource,
    EPSNormalizationAdjustment,
    EPSNormalizationMethod,
    FilingEPSObservation,
    build_normalized_forward_eps_candidate,
)
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.official_market_data import DartEPS
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.per import PERLevelName
from valuation_engine.per_adapters import LivePERAssumptionKeys


def _dart(year: str, eps: str, receipt: str) -> FilingEPSObservation:
    return FilingEPSObservation(
        DartEPS(
            corp_code="00126380",
            business_year=year,
            report_code="11011",
            fs_div="CFS",
            eps=Decimal(eps),
            amount_field="thstrm_amount",
            receipt_no=receipt,
            source_ref=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        ),
        evidence_id=f"E-EPS-{year}",
    )


def _candidate():
    return build_normalized_forward_eps_candidate(
        (_dart("2023", "800", "20240301000001"), _dart("2024", "1000", "20250301000001"), _dart("2025", "1200", "20260301000001")),
        normalization_method=EPSNormalizationMethod.THREE_YEAR_MEDIAN_ADJUSTED,
        adjustments=(
            EPSNormalizationAdjustment(
                label="one-off litigation cost",
                business_year="2024",
                per_share_amount=Decimal("100"),
                evidence_ids=("E-ADJ",),
                source_ref="filing://2024/one-off",
            ),
        ),
        forward_growth_rate=Decimal("0.10"),
        forward_growth_evidence_ids=("E-GROWTH",),
        forward_growth_source_ref="bridge://operating-growth",
    )


def _peer(peer_id: str, price: float, eps: float, fundamental: float) -> AuthorizedPeerPERSource:
    return AuthorizedPeerPERSource(
        peer_id=peer_id,
        market_price=price,
        normalized_forward_eps=eps,
        fundamental_forward_per=fundamental,
        as_of="2026-08-25",
        market_source_ref=f"krx://peer/{peer_id}/price",
        eps_source_ref=f"filing-model://peer/{peer_id}/forward-eps",
        fundamental_model_ref=f"model://peer/{peer_id}/fundamental-per",
        methodology="same-horizon peer normalized forward PER",
    )


def _pack() -> AuthorizedPERProviderPack:
    peers = (
        (PERLevelName.L1_BROAD_SECTOR, _peer("P1", 18000, 1000, 15), "sector", ("EP1",), ("duration",)),
        (PERLevelName.L2_INDUSTRY, _peer("P2", 22000, 1100, 16), "industry", ("EP2",), ("margin",)),
        (PERLevelName.L3_RISK_DRIVER_SUBINDUSTRY, _peer("P3", 25000, 1250, 17), "risk", ("EP3",), ("reinvestment",)),
        (PERLevelName.L4_ECONOMIC_TWINS, _peer("P4", 30000, 1500, 18), "twin", ("EP4",), ("growth", "ROIC")),
    )
    return AuthorizedPERProviderPack(
        target_id="T",
        normalized_forward_eps=_candidate(),
        residual_levels=tuple(
            AuthorizedPERLevelSource(level, (peer,), rationale, evidence, features)
            for level, peer, rationale, evidence, features in peers
        ),
    )


def _keys() -> LivePERAssumptionKeys:
    return LivePERAssumptionKeys(
        scenario_id="BASE",
        normalized_forward_eps_key="eps",
        normalized_forward_eps_unit="KRW",
        explicit_growth_rate_keys=("g1",),
        fcfe_conversion_rate_keys=("fcfe1", "fcfe2"),
        terminal_growth_key="tg",
        terminal_roe_key="troe",
        margin_path_keys=("m1",),
        reinvestment_path_keys=("r1",),
    )


def _compiled(eps: Decimal, evidence_ids: tuple[str, ...]) -> CompiledAssumptionSet:
    values = (
        CompiledAssumption(
            key="eps",
            scenario_id="BASE",
            measure=Measure(eps, "KRW", "2026-12-31"),
            bridge_id="B-EPS",
            evidence_ids=evidence_ids,
            hypothesis_id="H-EPS",
            economic_path_id="eps",
            transform_id="normalized_forward_eps",
            input_evidence_hash="HASH-EPS",
        ),
    )
    return CompiledAssumptionSet("T", values, "HASH")


def test_normalized_eps_uses_annual_filings_explicit_adjustments_and_nonstreet_growth():
    candidate = _candidate()
    # Adjusted history = 800, 1100, 1200 -> median base 1100; one-year growth 10%.
    assert candidate.normalized_base_eps == Decimal("1100")
    assert candidate.normalized_forward_eps == Decimal("1210.00")
    assert candidate.forward_business_year == "2026"
    assert set(candidate.evidence_ids) == {"E-EPS-2023", "E-EPS-2024", "E-EPS-2025", "E-ADJ", "E-GROWTH"}


def test_interim_filing_eps_cannot_be_silently_promoted_to_normalized_forward_eps():
    interim = FilingEPSObservation(
        DartEPS(
            corp_code="00126380",
            business_year="2026",
            report_code="11012",
            fs_div="CFS",
            eps=Decimal("700"),
            amount_field="thstrm_add_amount",
            receipt_no="20260801000001",
            source_ref="dart://interim",
        ),
        "E-INTERIM",
    )
    with pytest.raises(AuthorizedPERProviderError, match="annual OpenDART"):
        build_normalized_forward_eps_candidate(
            (interim,),
            normalization_method=EPSNormalizationMethod.LATEST_ANNUAL_ADJUSTED,
            forward_growth_rate=Decimal("0.1"),
            forward_growth_evidence_ids=("E-G",),
            forward_growth_source_ref="bridge://growth",
        )


def test_peer_residual_levels_compute_peer_only_market_forward_per():
    levels = _pack().live_residual_levels()
    assert levels[0].peers[0].market_forward_per == pytest.approx(18.0)
    assert levels[3].peers[0].fundamental_forward_per == 18
    assert "peer-only market reference" in levels[0].peers[0].methodology


def test_loader_requires_compiled_eps_to_match_candidate_and_carry_provider_evidence():
    pack = _pack()
    loader = pack.loader(
        core_assumption_keys=_keys(),
        applicability_rationale="positive Evidence-backed normalized forward EPS",
    )
    evidence_ids = pack.normalized_forward_eps.evidence_ids
    context = OrchestratorContext(
        "R",
        ExecutionMode.LIVE_PRIMARY,
        {"compiled_assumption_set": _compiled(Decimal("1210.00"), evidence_ids)},
    )
    inputs = loader(context)
    assert inputs.target_id == "T"
    assert inputs.residual_levels is not None
    assert len(inputs.residual_levels) == 4

    mismatch = OrchestratorContext(
        "R2",
        ExecutionMode.LIVE_PRIMARY,
        {"compiled_assumption_set": _compiled(Decimal("1300"), evidence_ids)},
    )
    with pytest.raises(AuthorizedPERProviderError, match="does not match"):
        loader(mismatch)

    missing = OrchestratorContext(
        "R3",
        ExecutionMode.LIVE_PRIMARY,
        {"compiled_assumption_set": _compiled(Decimal("1210.00"), ("E-GROWTH",))},
    )
    with pytest.raises(AuthorizedPERProviderError, match="missing provider Evidence"):
        loader(missing)


def test_target_company_and_duplicate_peer_are_blocked_from_residual_pool():
    pack = _pack()
    l4 = pack.residual_levels[-1]
    target_l4 = AuthorizedPERLevelSource(
        l4.level,
        (_peer("T", 30000, 1500, 18),),
        l4.selection_rationale,
        l4.selection_evidence_ids,
        l4.economic_twin_features,
    )
    with pytest.raises(AuthorizedPERProviderError, match="target company"):
        AuthorizedPERProviderPack(
            "T", pack.normalized_forward_eps, (*pack.residual_levels[:-1], target_l4)
        ).validate()
