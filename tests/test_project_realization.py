import pytest

from valuation_engine.broker_research import ProjectRealizationRecord, ProjectRealizationStage
from valuation_engine.signal_intelligence import (
    ProjectGate,
    ProjectGateEvidence,
    ProjectGateSet,
    ProjectState,
    legacy_state_gate_evidence,
)


def test_project_gate_set_is_order_independent_and_reports_unresolved_gates():
    gates = ProjectGateSet(
        project_id="P1",
        required_gates=(
            ProjectGate.FINANCING,
            ProjectGate.PERMIT_APPROVAL,
            ProjectGate.GRID_UTILITIES,
        ),
        observations=(
            ProjectGateEvidence(ProjectGate.GRID_UTILITIES, True, ("E-GRID",)),
            ProjectGateEvidence(ProjectGate.FINANCING, True, ("E-FUND",)),
        ),
    )

    assert set(gates.verified_gates) == {ProjectGate.GRID_UTILITIES, ProjectGate.FINANCING}
    assert gates.unresolved_gates == (ProjectGate.PERMIT_APPROVAL,)
    assert gates.realization_maturity == pytest.approx(2 / 3)
    assert not gates.revenue_verified


def test_verified_gate_requires_evidence():
    gates = ProjectGateSet(
        project_id="P2",
        required_gates=(ProjectGate.PERMIT_APPROVAL,),
        observations=(ProjectGateEvidence(ProjectGate.PERMIT_APPROVAL, True),),
    )
    with pytest.raises(ValueError):
        gates.validate()


def test_legacy_state_translation_does_not_imply_unrelated_prior_gates():
    translated = legacy_state_gate_evidence(
        ProjectState.UNDER_CONSTRUCTION,
        evidence_ids=("E-CONSTRUCTION",),
    )
    assert tuple(item.gate for item in translated) == (ProjectGate.CONSTRUCTION,)


def test_broker_project_stage_maps_to_canonical_gate():
    record = ProjectRealizationRecord(
        project_id="P3",
        stage=ProjectRealizationStage.UTILITIES_SECURED,
        evidence_ids=("E-UTILITY",),
    )
    gate = record.to_gate_evidence()
    assert gate.gate is ProjectGate.GRID_UTILITIES
    assert gate.verified
    assert gate.evidence_ids == ("E-UTILITY",)
