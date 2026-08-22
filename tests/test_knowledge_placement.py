from valuation_engine.knowledge_placement import (
    KnowledgeLayer,
    PlacementDisposition,
    WorkflowStage,
    decide_placement,
)


def test_sasb_like_metric_standard_defines_requirements_but_not_assumptions():
    ok = decide_placement(KnowledgeLayer.METRIC_STANDARD, WorkflowStage.MODULE_REQUIREMENT_PLAN)
    assert ok.allowed
    assert ok.disposition is PlacementDisposition.CANONICAL_DEFINITION
    blocked = decide_placement(KnowledgeLayer.METRIC_STANDARD, WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE)
    assert not blocked.allowed


def test_input_output_is_structural_prior_not_direct_company_forecast():
    ok = decide_placement(KnowledgeLayer.STRUCTURAL_SUPPLY_CHAIN_PRIOR, WorkflowStage.INDUSTRY_DNA_ROUTE)
    assert ok.allowed
    blocked = decide_placement(KnowledgeLayer.STRUCTURAL_SUPPLY_CHAIN_PRIOR, WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE)
    assert not blocked.allowed


def test_primary_observed_requires_bridge_at_assumption_boundary():
    decision = decide_placement(KnowledgeLayer.PRIMARY_OBSERVED, WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE)
    assert decision.allowed and decision.bridge_required


def test_broker_industry_kpi_can_be_discovered_pre_freeze():
    decision = decide_placement(
        KnowledgeLayer.BROKER_RESEARCH,
        WorkflowStage.MODULE_REQUIREMENT_PLAN,
        field_class="kpi_definition",
        target_company_specific=False,
    )
    assert decision.allowed
    assert decision.disposition is PlacementDisposition.CANDIDATE_ONLY


def test_broker_target_company_forecast_is_blocked_pre_freeze():
    decision = decide_placement(
        KnowledgeLayer.BROKER_RESEARCH,
        WorkflowStage.SCENARIO_BUILD,
        field_class="target_company_forecast",
        target_company_specific=True,
    )
    assert not decision.allowed


def test_broker_target_company_forecast_is_post_freeze_only():
    decision = decide_placement(
        KnowledgeLayer.BROKER_RESEARCH,
        WorkflowStage.STREET_GAP,
        field_class="target_company_forecast",
        target_company_specific=True,
    )
    assert decision.allowed
    assert decision.disposition is PlacementDisposition.POST_FREEZE_ONLY


def test_alternative_data_only_creates_verification_request():
    decision = decide_placement(KnowledgeLayer.ALTERNATIVE_DATA, WorkflowStage.MONITORING)
    assert decision.allowed
    assert decision.disposition is PlacementDisposition.VERIFICATION_REQUEST


def test_damodaran_like_reference_cannot_be_direct_assumption():
    ok = decide_placement(KnowledgeLayer.CALIBRATION_REFERENCE, WorkflowStage.HIERARCHICAL_BETA_ESTIMATION)
    assert ok.allowed and ok.disposition is PlacementDisposition.REFERENCE_ONLY
    blocked = decide_placement(KnowledgeLayer.CALIBRATION_REFERENCE, WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE)
    assert not blocked.allowed


def test_market_reference_is_quarantined_before_freeze():
    blocked = decide_placement(KnowledgeLayer.MARKET_REFERENCE, WorkflowStage.SCENARIO_BUILD)
    assert not blocked.allowed
    ok = decide_placement(KnowledgeLayer.MARKET_REFERENCE, WorkflowStage.MARKET_COMPARE)
    assert ok.allowed
