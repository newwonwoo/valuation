from valuation_engine.assumption_compiler import AssumptionSpec
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec
from valuation_engine.shadow_adapters import scenario_build_adapter


def evidence(evidence_id: str, value: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="margin",
        value=value,
        unit="ratio",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref=f"source#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def hypothesis(hypothesis_id: str, evidence_id: str) -> HypothesisRecord:
    return HypothesisRecord(
        id=hypothesis_id,
        statement="margin evidence maps to scenario margin",
        causal_chain=("evidence", "margin", "value"),
        supporting_evidence_ids=(evidence_id,),
        kill_conditions=("margin reverses",),
    )


def bridge(bridge_id: str, hypothesis_id: str, evidence_id: str, value: float, scenario: str) -> BridgeRecord:
    return BridgeRecord(
        id=bridge_id,
        evidence_ids=(evidence_id,),
        hypothesis_id=hypothesis_id,
        affected_variable=AffectedVariable.MARGIN,
        direction=Direction.UP,
        old_value=0.0,
        new_value=value,
        unit="ratio",
        rationale="scenario margin observation",
        confidence=0.8,
        kill_condition="margin reverses",
        verification_event="next filing",
        economic_path_id=f"{scenario}:margin",
    )


def test_scenario_build_adapter_compiles_and_binds():
    scenarios = (("Bear", 0.10), ("Base", 0.20), ("Bull", 0.30))
    evidences = tuple(evidence(f"E_{name}", value) for name, value in scenarios)
    hypotheses = tuple(hypothesis(f"H_{name}", f"E_{name}") for name, _ in scenarios)
    bridges = tuple(bridge(f"B_{name}", f"H_{name}", f"E_{name}", value, name) for name, value in scenarios)
    specs = tuple(
        AssumptionSpec("margin", name, f"B_{name}", "ratio", "identity_observation")
        for name, _ in scenarios
    )

    result = run_controlled_workflow(
        run_id="SCENARIO-SHADOW",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("SCENARIO_BUILD",),
        adapters={"SCENARIO_BUILD": scenario_build_adapter()},
        required_stages=("SCENARIO_BUILD",),
        initial_data={
            "target_id": "T",
            "evidence_ledger": EvidenceLedger(evidences),
            "hypotheses": hypotheses,
            "bridges": bridges,
            "assumption_specs": specs,
            "bridge_input_map": {},
            "scenario_binding_spec": ScenarioBindingSpec(("Bear", "Base", "Bull"), ("margin",)),
        },
    )

    assert result.blocked_reasons == ()
    assert result.stage_traces[0].status is StageStatus.PASS
    assert result.data["compiled_assumption_set"].get("margin", "Base").measure.amount == 0.2
    assert result.data["bound_scenario_set"].get("Bull").get("margin").measure.amount == 0.3
    assert not result.data["probability_weighting_allowed"]


def test_scenario_build_adapter_fails_closed_on_proposal_mismatch():
    ev = evidence("E_Base", 0.20)
    hyp = hypothesis("H_Base", "E_Base")
    bad = bridge("B_Base", "H_Base", "E_Base", 0.50, "Base")
    result = run_controlled_workflow(
        run_id="SCENARIO-BLOCK",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("SCENARIO_BUILD",),
        adapters={"SCENARIO_BUILD": scenario_build_adapter()},
        required_stages=("SCENARIO_BUILD",),
        initial_data={
            "target_id": "T",
            "evidence_ledger": EvidenceLedger((ev,)),
            "hypotheses": (hyp,),
            "bridges": (bad,),
            "assumption_specs": (AssumptionSpec("margin", "Base", "B_Base", "ratio", "identity_observation"),),
            "bridge_input_map": {},
            "scenario_binding_spec": ScenarioBindingSpec(("Base",), ("margin",)),
        },
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.BLOCKED
