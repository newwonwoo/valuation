from decimal import Decimal

from valuation_engine.actual_units import measure_from_raw
from valuation_engine.assumption_compiler import (
    AssumptionSpec,
    CompilationStatus,
    compile_assumptions,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    CalibrationStatus,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def evidence(
    evidence_id: str,
    value,
    unit: str,
    *,
    metric: str = "metric",
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric=metric,
        value=value,
        unit=unit,
        source_layer=layer,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref=f"source#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def hypothesis(status: CalibrationStatus = CalibrationStatus.UNCALIBRATED) -> HypothesisRecord:
    return HypothesisRecord(
        id="H1",
        statement="evidence maps to an economic driver",
        causal_chain=("evidence", "driver", "value"),
        supporting_evidence_ids=("E1",),
        probability=0.6,
        calibration_status=status,
        kill_conditions=("driver reverses",),
    )


def bridge(
    *,
    bridge_id: str = "B1",
    evidence_ids=("E1",),
    new_value: float = 0.82,
    unit: str = "ratio",
    affected: AffectedVariable = AffectedVariable.UTILIZATION,
) -> BridgeRecord:
    return BridgeRecord(
        id=bridge_id,
        evidence_ids=tuple(evidence_ids),
        hypothesis_id="H1",
        affected_variable=affected,
        direction=Direction.UP,
        old_value=0.0,
        new_value=new_value,
        unit=unit,
        rationale="deterministic bridge proposal",
        confidence=0.8,
        kill_condition="driver reverses",
        verification_event="next filing",
        economic_path_id="PATH1",
    )


def test_actual_units_convert_without_float_math():
    assert measure_from_raw("1.5", "GW", "2026-06-30").convert_to("W").amount == Decimal("1500000000.0")
    assert measure_from_raw("25", "%", "2026-06-30").convert_to("ratio").amount == Decimal("0.25")


def test_identity_observation_compiles_and_hashes():
    ledger = EvidenceLedger((evidence("E1", 0.82, "ratio", metric="utilization"),))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(),),
        bridges=(bridge(),),
        specs=(AssumptionSpec("utilization", "Base", "B1", "ratio", "identity_observation", min_value=Decimal("0"), max_value=Decimal("1")),),
        bridge_input_map={},
    )
    assert result.status is CompilationStatus.COMPILED
    assert result.assumption_set is not None
    assert result.assumption_set.get("utilization", "Base").measure.amount == Decimal("0.82")
    assert result.assumption_set.assumption_set_hash


def test_bridge_proposal_is_recomputed_not_trusted():
    ledger = EvidenceLedger((evidence("E1", 0.82, "ratio"),))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(),),
        bridges=(bridge(new_value=0.95),),
        specs=(AssumptionSpec("utilization", "Base", "B1", "ratio", "identity_observation"),),
        bridge_input_map={},
    )
    assert not result.passed
    assert any(item.code == "PROPOSAL_RECALC_MISMATCH" for item in result.findings)


def test_ratio_transform_uses_registered_evidence_inputs():
    ledger = EvidenceLedger((
        evidence("E1", 80, "count", metric="output"),
        evidence("E2", 100, "count", metric="capacity"),
    ))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(),),
        bridges=(bridge(evidence_ids=("E1", "E2"), new_value=0.8),),
        specs=(AssumptionSpec("utilization", "Base", "B1", "ratio", "ratio"),),
        bridge_input_map={"B1": ("E1", "E2")},
    )
    assert result.passed
    assert result.assumption_set.get("utilization", "Base").measure.amount == Decimal("0.8")


def test_market_comparison_evidence_is_blocked_pre_freeze():
    ledger = EvidenceLedger((
        evidence("E1", 100000, "KRW", layer=EvidenceSourceLayer.MARKET_COMPARISON),
    ))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(),),
        bridges=(bridge(new_value=100000, unit="KRW", affected=AffectedVariable.PRICE),),
        specs=(AssumptionSpec("price", "Base", "B1", "KRW", "identity_observation"),),
        bridge_input_map={},
    )
    assert not result.passed
    assert any(item.code == "INVALID_EVIDENCE_INPUT" for item in result.findings)


def test_policy_price_alone_cannot_become_enterprise_price():
    ledger = EvidenceLedger((
        evidence("E1", 21, "USD", layer=EvidenceSourceLayer.POLICY_PRIMARY_SOURCE),
    ))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(),),
        bridges=(bridge(new_value=21, unit="USD", affected=AffectedVariable.PRICE),),
        specs=(AssumptionSpec("asp", "Base", "B1", "USD", "identity_observation"),),
        bridge_input_map={},
    )
    assert not result.passed
    assert any(item.code == "POLICY_PRICE_AS_ENTERPRISE_PRICE" for item in result.findings)


def test_probability_requires_calibrated_hypothesis_when_spec_demands_it():
    ledger = EvidenceLedger((evidence("E1", 0.6, "ratio"),))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(CalibrationStatus.UNCALIBRATED),),
        bridges=(bridge(new_value=0.6, affected=AffectedVariable.PROBABILITY),),
        specs=(AssumptionSpec("event_probability", "Base", "B1", "ratio", "identity_observation", probability_only_if_calibrated=True),),
        bridge_input_map={},
    )
    assert not result.passed
    assert any(item.code == "UNCALIBRATED_PROBABILITY" for item in result.findings)


def test_calibrated_probability_can_compile():
    ledger = EvidenceLedger((evidence("E1", 0.6, "ratio"),))
    result = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis(CalibrationStatus.CALIBRATED),),
        bridges=(bridge(new_value=0.6, affected=AffectedVariable.PROBABILITY),),
        specs=(AssumptionSpec("event_probability", "Base", "B1", "ratio", "identity_observation", probability_only_if_calibrated=True, min_value=Decimal("0"), max_value=Decimal("1")),),
        bridge_input_map={},
    )
    assert result.passed
