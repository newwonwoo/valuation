from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class KnowledgeLayer(str, Enum):
    CLASSIFICATION_STANDARD = "classification_standard"
    METRIC_STANDARD = "metric_standard"
    PROVENANCE_STANDARD = "provenance_standard"
    STRUCTURAL_SUPPLY_CHAIN_PRIOR = "structural_supply_chain_prior"
    PRIMARY_OBSERVED = "primary_observed"
    PUBLIC_INDUSTRY_RESEARCH = "public_industry_research"
    BROKER_RESEARCH = "broker_research"
    ALTERNATIVE_DATA = "alternative_data"
    COMPANY_PRIMARY = "company_primary"
    CALIBRATION_REFERENCE = "calibration_reference"
    MARKET_REFERENCE = "market_reference"


class WorkflowStage(str, Enum):
    FOUNDATION_LOAD = "foundation_load"
    SOURCE_FRESHNESS_PRECHECK = "source_freshness_precheck"
    INDUSTRY_DNA_ROUTE = "industry_dna_route"
    MODULE_REQUIREMENT_PLAN = "module_requirement_plan"
    PRIMARY_EVIDENCE_COLLECTION = "primary_evidence_collection"
    EVIDENCE_NORMALIZATION = "evidence_normalization"
    MECHANISM_VALIDATION = "mechanism_validation"
    UPSTREAM_FUNDING_SCAN = "upstream_funding_scan"
    EVIDENCE_TO_ASSUMPTION_BRIDGE = "evidence_to_assumption_bridge"
    SCENARIO_BUILD = "scenario_build"
    HIERARCHICAL_BETA_ESTIMATION = "hierarchical_beta_estimation"
    WACC_VALIDATION = "wacc_validation"
    HIERARCHICAL_WARRANTED_PER = "hierarchical_warranted_per"
    AUDIT_GATE = "audit_gate"
    INTRINSIC_VALUE_FREEZE = "intrinsic_value_freeze"
    STREET_GAP = "street_gap"
    MARKET_COMPARE = "market_compare"
    MONITORING = "monitoring"


class PlacementDisposition(str, Enum):
    CANONICAL_DEFINITION = "canonical_definition"
    GOVERNANCE_ONLY = "governance_only"
    STRUCTURAL_PRIOR = "structural_prior"
    OBSERVED_EVIDENCE = "observed_evidence"
    COMPANY_EVIDENCE = "company_evidence"
    CANDIDATE_ONLY = "candidate_only"
    VERIFICATION_REQUEST = "verification_request"
    REFERENCE_ONLY = "reference_only"
    POST_FREEZE_ONLY = "post_freeze_only"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PlacementDecision:
    allowed: bool
    disposition: PlacementDisposition
    bridge_required: bool
    reason: str


_PRE_FREEZE_STREET_FIELDS = {
    "target_company_forecast",
    "target_price",
    "rating",
    "target_multiple",
    "consensus",
    "current_market_price",
}


def decide_placement(
    layer: KnowledgeLayer,
    stage: WorkflowStage,
    *,
    field_class: str = "",
    target_company_specific: bool = False,
) -> PlacementDecision:
    """Fail-closed routing of knowledge to the valuation workflow.

    This answers *where a source may be used*, not whether the source is factually correct.
    Evidence quality/provenance gates still apply downstream.
    """
    field = field_class.strip().lower()

    if layer is KnowledgeLayer.CLASSIFICATION_STANDARD:
        allowed = stage in {WorkflowStage.FOUNDATION_LOAD, WorkflowStage.INDUSTRY_DNA_ROUTE}
        return PlacementDecision(allowed, PlacementDisposition.CANONICAL_DEFINITION if allowed else PlacementDisposition.BLOCKED, False,
                                 "classification standards label/crosswalk industries; they do not set valuation assumptions")

    if layer is KnowledgeLayer.METRIC_STANDARD:
        allowed = stage in {
            WorkflowStage.FOUNDATION_LOAD,
            WorkflowStage.MODULE_REQUIREMENT_PLAN,
            WorkflowStage.EVIDENCE_NORMALIZATION,
        }
        return PlacementDecision(allowed, PlacementDisposition.CANONICAL_DEFINITION if allowed else PlacementDisposition.BLOCKED, False,
                                 "metric standards define what to measure, not the realized value")

    if layer is KnowledgeLayer.PROVENANCE_STANDARD:
        allowed = stage in {WorkflowStage.FOUNDATION_LOAD, WorkflowStage.SOURCE_FRESHNESS_PRECHECK, WorkflowStage.AUDIT_GATE}
        return PlacementDecision(allowed, PlacementDisposition.GOVERNANCE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                 "provenance/quality standards govern lineage and freshness only")

    if layer is KnowledgeLayer.STRUCTURAL_SUPPLY_CHAIN_PRIOR:
        allowed = stage in {
            WorkflowStage.INDUSTRY_DNA_ROUTE,
            WorkflowStage.MECHANISM_VALIDATION,
            WorkflowStage.UPSTREAM_FUNDING_SCAN,
        }
        return PlacementDecision(allowed, PlacementDisposition.STRUCTURAL_PRIOR if allowed else PlacementDisposition.BLOCKED, False,
                                 "input-output/topology data are structural priors, not current company demand")

    if layer is KnowledgeLayer.PRIMARY_OBSERVED:
        allowed = stage in {
            WorkflowStage.PRIMARY_EVIDENCE_COLLECTION,
            WorkflowStage.EVIDENCE_NORMALIZATION,
            WorkflowStage.MECHANISM_VALIDATION,
            WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE,
        }
        return PlacementDecision(allowed, PlacementDisposition.OBSERVED_EVIDENCE if allowed else PlacementDisposition.BLOCKED,
                                 stage is WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE,
                                 "official/primary observed data may support assumptions only through the evidence-to-assumption bridge")

    if layer is KnowledgeLayer.COMPANY_PRIMARY:
        allowed = stage in {
            WorkflowStage.PRIMARY_EVIDENCE_COLLECTION,
            WorkflowStage.EVIDENCE_NORMALIZATION,
            WorkflowStage.MECHANISM_VALIDATION,
            WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE,
        }
        return PlacementDecision(allowed, PlacementDisposition.COMPANY_EVIDENCE if allowed else PlacementDisposition.BLOCKED,
                                 stage is WorkflowStage.EVIDENCE_TO_ASSUMPTION_BRIDGE,
                                 "company filings/contracts are primary company evidence; plans remain plans")

    if layer is KnowledgeLayer.PUBLIC_INDUSTRY_RESEARCH:
        allowed = stage in {
            WorkflowStage.INDUSTRY_DNA_ROUTE,
            WorkflowStage.MODULE_REQUIREMENT_PLAN,
            WorkflowStage.MECHANISM_VALIDATION,
            WorkflowStage.SCENARIO_BUILD,
        }
        return PlacementDecision(allowed, PlacementDisposition.CANDIDATE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                 "public research provides structure/forecast/mechanism candidates and requires corroboration")

    if layer is KnowledgeLayer.BROKER_RESEARCH:
        if target_company_specific or field in _PRE_FREEZE_STREET_FIELDS:
            allowed = stage in {WorkflowStage.STREET_GAP, WorkflowStage.MARKET_COMPARE}
            return PlacementDecision(allowed, PlacementDisposition.POST_FREEZE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                     "target-company Street fields are quarantined until intrinsic value is frozen")
        allowed = stage in {
            WorkflowStage.INDUSTRY_DNA_ROUTE,
            WorkflowStage.MODULE_REQUIREMENT_PLAN,
            WorkflowStage.MECHANISM_VALIDATION,
            WorkflowStage.STREET_GAP,
        }
        return PlacementDecision(allowed, PlacementDisposition.CANDIDATE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                 "broker research discovers questions/KPIs/mechanisms; independent verification is required")

    if layer is KnowledgeLayer.ALTERNATIVE_DATA:
        allowed = stage in {WorkflowStage.MECHANISM_VALIDATION, WorkflowStage.MONITORING}
        return PlacementDecision(allowed, PlacementDisposition.VERIFICATION_REQUEST if allowed else PlacementDisposition.BLOCKED, False,
                                 "alternative data is a leading-signal candidate until methodology and representativeness are validated")

    if layer is KnowledgeLayer.CALIBRATION_REFERENCE:
        allowed = stage in {
            WorkflowStage.HIERARCHICAL_BETA_ESTIMATION,
            WorkflowStage.WACC_VALIDATION,
            WorkflowStage.HIERARCHICAL_WARRANTED_PER,
            WorkflowStage.AUDIT_GATE,
        }
        return PlacementDecision(allowed, PlacementDisposition.REFERENCE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                 "sector calibration datasets are sanity checks/priors, never plug values for the target")

    if layer is KnowledgeLayer.MARKET_REFERENCE:
        allowed = stage in {WorkflowStage.INTRINSIC_VALUE_FREEZE, WorkflowStage.STREET_GAP, WorkflowStage.MARKET_COMPARE}
        return PlacementDecision(allowed, PlacementDisposition.POST_FREEZE_ONLY if allowed else PlacementDisposition.BLOCKED, False,
                                 "market references are comparison objects after the blind intrinsic-value boundary")

    return PlacementDecision(False, PlacementDisposition.BLOCKED, False, "unrecognized knowledge layer")
