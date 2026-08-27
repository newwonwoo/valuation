from decimal import Decimal

import pytest

from valuation_engine.report_claim_sync import (
    ClaimValuationImpact,
    ClaimValuationTreatment,
    audit_claim_to_value_sync,
)


def impact(
    *,
    treatment: ClaimValuationTreatment = ClaimValuationTreatment.VALUED,
    revised: Decimal = Decimal("110"),
) -> ClaimValuationImpact:
    return ClaimValuationImpact(
        claim_id="C:POLICY",
        treatment=treatment,
        evidence_ids=("E:POLICY",),
        bridge_ids=("B:POLICY",),
        assumption_keys=(("Core", "fcff_year_3"),),
        prior_intrinsic_value_per_share=Decimal("100"),
        revised_intrinsic_value_per_share=revised,
        rationale="Policy transmission changes a compiled cash-flow assumption.",
    )


def audit(row: ClaimValuationImpact):
    return audit_claim_to_value_sync(
        impacts=(row,),
        headline_claim_ids=("C:POLICY",),
        active_evidence_ids=("E:POLICY",),
        active_bridge_ids=("B:POLICY",),
        bridge_evidence_map={"B:POLICY": ("E:POLICY",)},
        compiled_assumption_keys=(("Core", "fcff_year_3"),),
    )


def test_valued_headline_requires_active_model_chain_and_changed_value():
    result = audit(impact())

    assert result.impact("C:POLICY").value_delta_per_share == Decimal("10")


def test_reference_only_claim_cannot_lead_report():
    with pytest.raises(ValueError, match="REFERENCE_ONLY claim cannot lead"):
        audit(impact(treatment=ClaimValuationTreatment.REFERENCE_ONLY))


def test_valued_headline_cannot_leave_intrinsic_value_unchanged():
    with pytest.raises(ValueError, match="left intrinsic value unchanged"):
        audit(impact(revised=Decimal("100")))


def test_valued_headline_requires_active_bridge():
    row = impact()
    with pytest.raises(ValueError, match="mapping is not active"):
        audit_claim_to_value_sync(
            impacts=(row,),
            headline_claim_ids=(row.claim_id,),
            active_evidence_ids=row.evidence_ids,
            active_bridge_ids=(),
            bridge_evidence_map={},
            compiled_assumption_keys=row.assumption_keys,
        )


def test_valued_headline_requires_evidence_bound_to_bridge():
    row = impact()
    with pytest.raises(ValueError, match="Evidence is not bound to its Bridge"):
        audit_claim_to_value_sync(
            impacts=(row,),
            headline_claim_ids=(row.claim_id,),
            active_evidence_ids=row.evidence_ids,
            active_bridge_ids=row.bridge_ids,
            bridge_evidence_map={"B:POLICY": ()},
            compiled_assumption_keys=row.assumption_keys,
        )
