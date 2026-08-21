import pytest

from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile, compose_modules
from valuation_engine.industry_knowledge import (
    AccessMode,
    AuthorityClass,
    ClaimKind,
    MechanismEvidence,
    PromotionStatus,
    SourceRole,
    SourceSpec,
    StructuredClaim,
    assess_mechanism,
    can_publish_raw_content,
    validate_claim_role,
)


def src(source_id="s1", family="fam1", roles=(SourceRole.OBSERVED_STATE,), access=AccessMode.API, public=True):
    return SourceSpec(source_id, family, AuthorityClass.OFFICIAL_STATISTICS, roles, access, ("semiconductor",), "monthly", public, "https://example.com")


def claim(cid, family, kind, period="2026Q1"):
    return StructuredClaim(cid, cid, family, "semiconductor.memory", kind, "x", period)


def test_forecast_cannot_be_observed_state():
    s = src(roles=(SourceRole.OBSERVED_STATE, SourceRole.FORWARD_HYPOTHESIS))
    c = claim("c1", "fam1", ClaimKind.FORECAST)
    with pytest.raises(ValueError):
        validate_claim_role(c, s, SourceRole.OBSERVED_STATE)


def test_policy_intent_is_not_realized_fact():
    s = src(roles=(SourceRole.OBSERVED_STATE, SourceRole.REGULATION_POLICY))
    c = claim("c1", "fam1", ClaimKind.POLICY_INTENT)
    with pytest.raises(ValueError):
        validate_claim_role(c, s, SourceRole.OBSERVED_STATE)


def test_same_family_repetition_does_not_corroborate():
    evidence = [
        MechanismEvidence(claim("c1", "fam1", ClaimKind.FACT, "2026Q1"), SourceRole.OBSERVED_STATE),
        MechanismEvidence(claim("c2", "fam1", ClaimKind.MECHANISM, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
    ]
    assert assess_mechanism(evidence).status is PromotionStatus.SINGLE_SOURCE_CANDIDATE


def test_independent_families_can_corroborate():
    evidence = [
        MechanismEvidence(claim("c1", "fam1", ClaimKind.FACT), SourceRole.OBSERVED_STATE),
        MechanismEvidence(claim("c2", "fam2", ClaimKind.MECHANISM), SourceRole.INDUSTRY_STRUCTURE),
    ]
    assert assess_mechanism(evidence).status is PromotionStatus.CORROBORATED


def test_module_rule_never_auto_approved():
    evidence = [
        MechanismEvidence(claim("c1", "fam1", ClaimKind.FACT, "2026Q1"), SourceRole.OBSERVED_STATE),
        MechanismEvidence(claim("c2", "fam2", ClaimKind.MECHANISM, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c3", "fam3", ClaimKind.LEADING_INDICATOR, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c4", "fam4", ClaimKind.VALUATION_LINK, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c5", "fam5", ClaimKind.KILL_CONDITION, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
    ]
    a = assess_mechanism(evidence)
    assert a.status is PromotionStatus.MANUAL_APPROVAL_REQUIRED


def test_definition_conflict_blocks_promotion():
    evidence = [
        MechanismEvidence(claim("c1", "fam1", ClaimKind.FACT), SourceRole.OBSERVED_STATE, unresolved_definition_conflict=True),
        MechanismEvidence(claim("c2", "fam2", ClaimKind.MECHANISM), SourceRole.INDUSTRY_STRUCTURE),
    ]
    a = assess_mechanism(evidence)
    assert a.blocking_reason == "unresolved definition conflict"


def test_licensed_fulltext_public_storage_forbidden():
    s = src(access=AccessMode.LICENSED, public=True)
    with pytest.raises(ValueError):
        can_publish_raw_content(s)


def test_industry_dna_composes_multilabel_modules():
    profile = IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power.transformer_switchgear",
        archetypes=(EconomicArchetype.CONTRACTED_BACKLOG, EconomicArchetype.CAPACITY_MANUFACTURING),
        revenue_recognition="point_in_time",
        price_formation="negotiated_contract",
        asset_ownership="manufacturer",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="concentrated_utility_hyperscaler",
        reinvestment_model="capacity_expansion",
        cashflow_duration="long_cycle",
        evidence_keys=("EV-ROUTE-1",),
    )
    result = compose_modules(profile, ("customer_advance_financing",))
    assert "contracted_backlog" in result.archetype_modules
    assert "capacity_manufacturing" in result.archetype_modules
    assert "warranted_per" in result.allowed_valuation_methods
    assert result.company_overlays == ("customer_advance_financing",)


def test_module_promotion_requires_manual_approval_red_team_and_regression():
    from valuation_engine.module_promotion import ApprovalDecision, ModuleApprovalRecord, compile_approved_rule
    evidence = [
        MechanismEvidence(claim("c1", "fam1", ClaimKind.FACT, "2026Q1"), SourceRole.OBSERVED_STATE),
        MechanismEvidence(claim("c2", "fam2", ClaimKind.MECHANISM, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c3", "fam3", ClaimKind.LEADING_INDICATOR, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c4", "fam4", ClaimKind.VALUATION_LINK, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
        MechanismEvidence(claim("c5", "fam5", ClaimKind.KILL_CONDITION, "2026Q2"), SourceRole.INDUSTRY_STRUCTURE),
    ]
    assessment = assess_mechanism(evidence)
    approval = ModuleApprovalRecord("m1", ApprovalDecision.APPROVE, "reviewer", "validated", True, True)
    compiled = compile_approved_rule("m1", assessment, approval, version="1.0")
    assert compiled.canonical
