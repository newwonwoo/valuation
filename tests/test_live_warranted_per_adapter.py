from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.per import EconomicAssumptionFingerprint, PERLevelName
from valuation_engine.per_adapters import (
    LiveExpansionPERConfig,
    LivePERAssumptionKeys,
    LivePERInputs,
    LivePERLevelObservation,
    LivePeerPERObservation,
    PERApplicability,
    live_hierarchical_warranted_per_adapter,
)
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.risk import HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.wacc import WACCResult


def evidence(evidence_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="per_support",
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
        tuple(evidence(item) for item in ("EEXP", "EP1", "EP2", "EP3", "EP4"))
    )


def compiled_assumption(key: str, value: str, unit: str, *, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="BASE",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=("EEXP",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def compiled_set() -> CompiledAssumptionSet:
    values = (
        compiled_assumption("eps", "1000", "KRW", path="eps"),
        compiled_assumption("g1", "0.10", "ratio", path="growth"),
        compiled_assumption("g2", "0.08", "ratio", path="growth"),
        compiled_assumption("fcfe1", "0.60", "ratio", path="fcfe"),
        compiled_assumption("fcfe2", "0.65", "ratio", path="fcfe"),
        compiled_assumption("fcfe3", "0.70", "ratio", path="fcfe"),
        compiled_assumption("tg", "0.025", "ratio", path="terminal"),
        compiled_assumption("troe", "0.12", "ratio", path="terminal"),
        compiled_assumption("m1", "0.15", "ratio", path="margin"),
        compiled_assumption("m2", "0.16", "ratio", path="margin"),
        compiled_assumption("r1", "0.40", "ratio", path="reinvestment"),
        compiled_assumption("r2", "0.35", "ratio", path="reinvestment"),
        compiled_assumption("x_eps", "1000", "KRW", path="eps"),
        compiled_assumption("x_g1", "0.12", "ratio", path="growth_expansion"),
        compiled_assumption("x_g2", "0.10", "ratio", path="growth_expansion"),
        compiled_assumption("x_g3", "0.08", "ratio", path="growth_expansion"),
        compiled_assumption("x_fcfe1", "0.55", "ratio", path="fcfe_expansion"),
        compiled_assumption("x_fcfe2", "0.60", "ratio", path="fcfe_expansion"),
        compiled_assumption("x_fcfe3", "0.65", "ratio", path="fcfe_expansion"),
        compiled_assumption("x_fcfe4", "0.70", "ratio", path="fcfe_expansion"),
        compiled_assumption("x_tg", "0.025", "ratio", path="terminal"),
        compiled_assumption("x_troe", "0.12", "ratio", path="terminal"),
        compiled_assumption("x_m1", "0.15", "ratio", path="margin_expansion"),
        compiled_assumption("x_m2", "0.16", "ratio", path="margin_expansion"),
        compiled_assumption("x_m3", "0.17", "ratio", path="margin_expansion"),
        compiled_assumption("x_r1", "0.45", "ratio", path="reinvestment_expansion"),
        compiled_assumption("x_r2", "0.40", "ratio", path="reinvestment_expansion"),
        compiled_assumption("x_r3", "0.35", "ratio", path="reinvestment_expansion"),
    )
    return CompiledAssumptionSet("T", values, "ASSUMPTION-HASH")


def structure() -> LiveCapitalStructureObservation:
    return LiveCapitalStructureObservation(
        equity_weight=0.75,
        debt_weight=0.25,
        tax_rate=0.22,
        method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        as_of="2026-08-22",
        source_refs=("CAPITAL:1",),
        rationale="normalized target structure",
    )


def live_wacc(*, cost_of_equity=0.10) -> LiveWACCStageResult:
    beta = LiveBetaStageResult(
        estimate=HierarchicalBetaEstimate(0.9, 0.01, ()),
        target_asset_beta=0.9,
        target_levered_beta=1.1,
        target_capital_structure=structure(),
        peer_ids=("P1", "P2", "P3", "P4"),
        source_refs=("BETA:1",),
        selection_evidence_ids=("EP1", "EP2", "EP3", "EP4"),
        snapshot_hash="BETA-HASH",
    )
    return LiveWACCStageResult(
        beta_result=beta,
        wacc_result=WACCResult(cost_of_equity, 0.04, 0.75, 0.25, 0.085),
        terminal_consistency=None,
        source_refs=("WACC:1",),
        funding_credit_evidence_ids=(),
        customer_advance_credit_supports_reduction_candidate=False,
        snapshot_hash=f"WACC-{cost_of_equity}",
    )


def core_keys() -> LivePERAssumptionKeys:
    return LivePERAssumptionKeys(
        scenario_id="BASE",
        normalized_forward_eps_key="eps",
        normalized_forward_eps_unit="KRW",
        explicit_growth_rate_keys=("g1", "g2"),
        fcfe_conversion_rate_keys=("fcfe1", "fcfe2", "fcfe3"),
        terminal_growth_key="tg",
        terminal_roe_key="troe",
        margin_path_keys=("m1", "m2"),
        reinvestment_path_keys=("r1", "r2"),
    )


def expansion_keys() -> LivePERAssumptionKeys:
    return LivePERAssumptionKeys(
        scenario_id="BASE",
        normalized_forward_eps_key="x_eps",
        normalized_forward_eps_unit="KRW",
        explicit_growth_rate_keys=("x_g1", "x_g2", "x_g3"),
        fcfe_conversion_rate_keys=("x_fcfe1", "x_fcfe2", "x_fcfe3", "x_fcfe4"),
        terminal_growth_key="x_tg",
        terminal_roe_key="x_troe",
        margin_path_keys=("x_m1", "x_m2", "x_m3"),
        reinvestment_path_keys=("x_r1", "x_r2", "x_r3"),
    )


def peer(peer_id: str, *, as_of="2026-08-22") -> LivePeerPERObservation:
    return LivePeerPERObservation(
        peer_id=peer_id,
        market_forward_per=18.0,
        fundamental_forward_per=15.0,
        as_of=as_of,
        market_source_ref=f"MARKET:{peer_id}",
        fundamental_model_ref=f"MODEL:{peer_id}",
        methodology="same-horizon normalized forward PER",
    )


def residual_levels(*, target_in_l4=False, repeated=False, different_date=False):
    l4_id = "T" if target_in_l4 else ("L3" if repeated else "L4")
    return (
        LivePERLevelObservation(
            PERLevelName.L1_BROAD_SECTOR,
            (peer("L1"),),
            "broad sector residual prior",
            ("EP1",),
            ("growth duration",),
        ),
        LivePERLevelObservation(
            PERLevelName.L2_INDUSTRY,
            (peer("L2"),),
            "industry residual prior",
            ("EP2",),
            ("margin stability",),
        ),
        LivePERLevelObservation(
            PERLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
            (peer("L3"),),
            "risk-driver residual prior",
            ("EP3",),
            ("reinvestment",),
        ),
        LivePERLevelObservation(
            PERLevelName.L4_ECONOMIC_TWINS,
            (peer(l4_id, as_of="2026-08-21" if different_date else "2026-08-22"),),
            "closest fundamental twin residual",
            ("EP4",),
            ("growth", "ROIC", "FCF conversion", "visibility"),
        ),
    )


def dcf_fingerprint() -> EconomicAssumptionFingerprint:
    return EconomicAssumptionFingerprint(
        growth_rates=(0.10, 0.08),
        margin_path=(0.15, 0.16),
        reinvestment_path=(0.40, 0.35),
        growth_duration_years=2,
    )


def inputs(**overrides) -> LivePERInputs:
    values = dict(
        target_id="T",
        applicability=PERApplicability.APPLICABLE,
        applicability_rationale="positive normalized EPS and PER allowed by module plan",
        core_assumption_keys=core_keys(),
        expansion=LiveExpansionPERConfig(
            expansion_keys(),
            ("EEXP",),
            "committed and pre-invested expansion capacity",
        ),
        residual_levels=residual_levels(),
        require_dcf_consistency=True,
        source_refs=("PER:INPUTS",),
    )
    values.update(overrides)
    return LivePERInputs(**values)


def context(*, cost_of_equity=0.10, extra=None) -> OrchestratorContext:
    data = {
        "compiled_assumption_set": compiled_set(),
        "live_wacc_result": live_wacc(cost_of_equity=cost_of_equity),
        "dcf_assumption_fingerprint": dcf_fingerprint(),
        "evidence_ledger": ledger(),
    }
    if extra:
        data.update(extra)
    return OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)


def test_live_per_builds_core_expansion_and_residual_layers():
    stage = live_hierarchical_warranted_per_adapter(loader=lambda ctx: inputs())(context())
    assert stage.status is StageStatus.PASS
    assert stage.outputs["core_fundamental_per"] > 0
    assert stage.outputs["expansion_adjusted_fundamental_per"] > 0
    assert stage.outputs["market_realization_per"] > 0
    assert stage.outputs["per_snapshot_hash"]


def test_per_uses_cost_of_equity_from_live_wacc():
    lower_ke = live_hierarchical_warranted_per_adapter(loader=lambda ctx: inputs())(
        context(cost_of_equity=0.09)
    )
    higher_ke = live_hierarchical_warranted_per_adapter(loader=lambda ctx: inputs())(
        context(cost_of_equity=0.12)
    )
    assert lower_ke.status is StageStatus.PASS
    assert higher_ke.status is StageStatus.PASS
    assert lower_ke.outputs["core_fundamental_per"] > higher_ke.outputs["core_fundamental_per"]


def test_dcf_per_fingerprint_mismatch_blocks():
    bad = EconomicAssumptionFingerprint(
        growth_rates=(0.20, 0.18),
        margin_path=(0.15, 0.16),
        reinvestment_path=(0.40, 0.35),
        growth_duration_years=2,
    )
    stage = live_hierarchical_warranted_per_adapter(loader=lambda ctx: inputs())(
        context(extra={"dcf_assumption_fingerprint": bad})
    )
    assert stage.status is StageStatus.BLOCKED
    assert "DCF-PER growth assumption mismatch" in stage.rationale


def test_expansion_per_requires_active_committed_evidence():
    bad_expansion = LiveExpansionPERConfig(
        expansion_keys(),
        ("INVENTED",),
        "claimed expansion",
    )
    stage = live_hierarchical_warranted_per_adapter(
        loader=lambda ctx: inputs(expansion=bad_expansion)
    )(context())
    assert stage.status is StageStatus.BLOCKED
    assert "inactive/unknown Evidence IDs" in stage.rationale


def test_target_company_cannot_enter_its_own_residual_pool():
    stage = live_hierarchical_warranted_per_adapter(
        loader=lambda ctx: inputs(residual_levels=residual_levels(target_in_l4=True))
    )(context())
    assert stage.status is StageStatus.BLOCKED
    assert "target company cannot enter" in stage.rationale


def test_peer_cannot_be_double_counted_across_per_levels():
    stage = live_hierarchical_warranted_per_adapter(
        loader=lambda ctx: inputs(residual_levels=residual_levels(repeated=True))
    )(context())
    assert stage.status is StageStatus.BLOCKED
    assert "multiple PER hierarchy levels" in stage.rationale


def test_peer_residual_observations_require_one_as_of_date():
    stage = live_hierarchical_warranted_per_adapter(
        loader=lambda ctx: inputs(residual_levels=residual_levels(different_date=True))
    )(context())
    assert stage.status is StageStatus.BLOCKED
    assert "one normalized as-of date" in stage.rationale


def test_target_street_or_market_fields_are_blocked_pre_freeze():
    stage = live_hierarchical_warranted_per_adapter(loader=lambda ctx: inputs())(
        context(extra={"target_company_consensus_eps": 1200})
    )
    assert stage.status is StageStatus.BLOCKED
    assert "target Street/market" in stage.rationale


def test_non_applicable_per_is_explicit_terminal_status():
    non_applicable = LivePERInputs(
        target_id="T",
        applicability=PERApplicability.NOT_APPLICABLE,
        applicability_rationale="normalized forward EPS is non-positive",
    )
    stage = live_hierarchical_warranted_per_adapter(loader=lambda ctx: non_applicable)(context())
    assert stage.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert not stage.outputs["warranted_per_applicable"]
