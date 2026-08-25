from dataclasses import replace
from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import (
    CompiledAssumption,
    CompiledAssumptionSet,
    compiled_assumption_set_digest,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.run_hash import compiled_assumption_set_hash


def _assumption() -> CompiledAssumption:
    return CompiledAssumption(
        key="revenue_growth",
        scenario_id="Base",
        measure=Measure(Decimal("0.12"), "ratio", "2026-06-30"),
        bridge_id="B1",
        evidence_ids=("E1",),
        hypothesis_id="H1",
        economic_path_id="PATH1",
        transform_id="identity_observation",
        input_evidence_hash="EVIDENCE_HASH",
        calibration_status=CalibrationStatus.CALIBRATED,
    )


def _compiled() -> CompiledAssumptionSet:
    item = _assumption()
    digest = compiled_assumption_set_digest("T1", (item,))
    return CompiledAssumptionSet("T1", (item,), digest)


def test_producer_and_independent_replay_share_v2_assumption_contract():
    compiled = _compiled()
    assert compiled_assumption_set_hash(compiled) == compiled.assumption_set_hash


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("bridge_id", "B2"),
        ("evidence_ids", ("E2",)),
        ("hypothesis_id", "H2"),
        ("economic_path_id", "PATH2"),
        ("transform_id", "unit_conversion"),
        ("calibration_status", CalibrationStatus.DEGRADED),
        ("measure", Measure(Decimal("0.12"), "ratio", "2026-07-01")),
    ),
)
def test_assumption_provenance_mutation_cannot_reuse_prior_hash(field, value):
    compiled = _compiled()
    forged_item = replace(compiled.assumptions[0], **{field: value})
    forged = replace(compiled, assumptions=(forged_item,))
    assert compiled_assumption_set_hash(forged) != forged.assumption_set_hash


def test_target_identity_mutation_cannot_reuse_prior_hash():
    compiled = _compiled()
    forged = replace(compiled, target_id="T2")
    assert compiled_assumption_set_hash(forged) != forged.assumption_set_hash
