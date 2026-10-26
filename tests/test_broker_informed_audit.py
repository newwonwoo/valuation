"""An informed analyst assumption is not a company-primary verification row."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from tests.test_broker_research_policy import reviewed_plan, informed_answer
from tests.test_broker_runtime import batch, evidence, make_plan, runtime_context
from valuation_engine.broker_runtime_v2 import broker_research_audit_adapter
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.generic_underwriting import ResearchUnderwritingEvidenceRecord
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.research_campaign import run_campaign


BROKER_URL = "https://example.test/report"


def _fixture():
    plan = reviewed_plan()
    campaign = run_campaign(plan, responder=informed_answer)
    receipt = campaign["outputs"]["utilization"]
    item = ResearchUnderwritingEvidenceRecord(
        id="UW:T:utilization", target="T", metric="utilization", value=receipt["value"],
        unit="ratio", source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
        effective_date=plan["as_of"], observed_date=plan["as_of"],
        source_name="reviewed conditional analyst assumption", source_ref=BROKER_URL,
        source_grade="B", confidence=0.6, segment="core", source_refs=(BROKER_URL,),
        research_receipt=receipt,
    )
    old_batch = batch()
    old_batch = replace(old_batch, observations=tuple(
        replace(row, source_ref=BROKER_URL) for row in old_batch.observations),
        source_refs=(BROKER_URL,))
    plan_stage = make_plan(loader=lambda _: old_batch)
    ledger = EvidenceLedger((item, evidence("E:LAND", "expansion_land_control")))
    data = runtime_context(plan_stage, ledger)
    data.update(target_id="T", data_cutoff=plan["as_of"], research_mode="INFORMED_REVISION",
        informed_revision_receipt_hash=plan["informed_revision"]["review_receipt"]["receipt_hash"])
    return OrchestratorContext("INFORMED-1", ExecutionMode.LIVE_PRIMARY, data), item


def _with_item(context, item):
    return OrchestratorContext(context.run_id, context.execution_mode, {
        **context.data, "evidence_ledger": EvidenceLedger((item, evidence("E:LAND", "expansion_land_control"))),
    })


def _audit(context):
    return broker_research_audit_adapter(required=True)(context)


def test_exact_reviewed_new_run_candidate_is_separate_from_company_primary_verification():
    context, item = _fixture()
    before = context.data["evidence_ledger"].records()
    result = _audit(context)
    assert result.status is StageStatus.PASS, result.rationale
    assert result.outputs["broker_research_audit_passed"]
    assert ("core", "utilization", ()) in result.outputs["broker_primary_verification_bindings"]
    assert result.outputs["broker_informed_underwriting_bindings"] == (("core", "utilization", (item.id,)),)
    findings = {row.check: row for row in result.outputs["broker_research_audit_report"].findings}
    assert not findings["broker_verification_company_primary"].passed
    assert not findings["broker_verification_company_primary"].blocking
    assert findings["broker_informed_revision_receipts_valid"].passed
    assert findings["broker_verification_reviewed_informed_underwriting"].passed
    assert "not primary verification" in findings["broker_verification_reviewed_informed_underwriting"].detail
    assert context.data["evidence_ledger"].records() == before
    assert item.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING


@pytest.mark.parametrize("change", ["blind", "missing_mode", "missing_receipt_hash", "wrong_receipt_hash", "wrong_run"])
def test_mode_flag_alone_cannot_authorize_legacy_broker_url_ingress(change):
    context, _ = _fixture()
    if change == "blind":
        context.data["research_mode"] = "BLIND_BASELINE"
    elif change == "missing_mode":
        context.data.pop("research_mode")
    elif change == "missing_receipt_hash":
        context.data.pop("informed_revision_receipt_hash")
    elif change == "wrong_receipt_hash":
        context.data["informed_revision_receipt_hash"] = "f" * 64
    else:
        context = OrchestratorContext("UNRELATED-RUN", context.execution_mode, context.data)
    result = _audit(context)
    assert result.status is StageStatus.BLOCKED
    assert "broker_informed_revision_receipts_valid" in result.rationale
    assert "broker_sources_not_in_primary_ledger" in result.rationale


@pytest.mark.parametrize("field,value", [
    ("metric", "cash_cost"), ("unit", "count"), ("value", "0.9"),
    ("segment", "other"), ("target", "OTHER"), ("observed_date", "2026-10-25"),
    ("source_layer", EvidenceSourceLayer.REALIZED_OR_FILING),
])
def test_exact_reviewed_candidate_cannot_authorize_a_different_evidence_record(field, value):
    context, item = _fixture()
    result = _audit(_with_item(context, replace(item, **{field: value})))
    assert result.status is StageStatus.BLOCKED
    assert "broker_informed_revision_receipts_valid" in result.rationale


def test_source_swap_remains_blocked_even_when_the_new_url_avoids_legacy_intersection():
    context, item = _fixture()
    item = replace(item, source_ref="https://example.test/other", source_refs=("https://example.test/other",))
    result = _audit(_with_item(context, item))
    assert result.status is StageStatus.BLOCKED
    finding = next(row for row in result.outputs["broker_research_audit_report"].findings
                   if row.check == "broker_informed_revision_receipts_valid")
    assert "source URLs differ" in finding.detail


def test_missing_or_mutated_research_receipt_does_not_get_an_exception():
    for mutation in ("missing", "value", "source", "fingerprint"):
        context, item = _fixture()
        receipt = deepcopy(item.research_receipt)
        if mutation == "missing":
            receipt = None
        elif mutation == "value":
            receipt["response"]["value"] = "0.9"
        elif mutation == "source":
            receipt["response"]["sources"][0]["content_sha256"] = "f" * 64
        else:
            receipt["source_family_count"] = 100
        result = _audit(_with_item(context, replace(item, research_receipt=receipt)))
        assert result.status is StageStatus.BLOCKED, mutation
        assert "broker_sources_not_in_primary_ledger" in result.rationale, mutation


def test_blind_source_intersection_is_still_rejected_even_with_matching_primary_metric():
    context, item = _fixture()
    raw = EvidenceRecord(**{name: getattr(item, name) for name in EvidenceRecord.__dataclass_fields__})
    raw = replace(raw, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING)
    context = _with_item(context, raw)
    context.data["research_mode"] = "BLIND_BASELINE"
    context.data.pop("informed_revision_receipt_hash")
    result = _audit(context)
    assert result.status is StageStatus.BLOCKED
    assert "broker_sources_not_in_primary_ledger" in result.rationale


def test_primary_metric_coverage_does_not_claim_period_value_or_basis_verification():
    context, _ = _fixture()
    primary = replace(evidence("E:U", "utilization"), effective_date="2025-01-01", value=0.1)
    context = _with_item(context, primary)
    context.data["research_mode"] = "BLIND_BASELINE"
    context.data.pop("informed_revision_receipt_hash")
    result = _audit(context)
    assert result.status is StageStatus.PASS
    finding = next(row for row in result.outputs["broker_research_audit_report"].findings
                   if row.check == "broker_verification_company_primary")
    assert finding.passed and "segment+metric only" in finding.detail
    assert "not verification of period, value, accounting basis or support" in finding.detail
    assert result.outputs["broker_informed_underwriting_bindings"] == ()


def test_one_reviewed_metric_cannot_cover_another_missing_required_metric():
    context, item = _fixture()
    context.data["evidence_ledger"] = EvidenceLedger((item,))
    result = _audit(context)
    assert result.status is StageStatus.BLOCKED
    assert "broker_verification_company_primary" in result.rationale
    assert "broker_verification_reviewed_informed_underwriting" in result.rationale


def test_secondary_source_ref_cannot_hide_an_unreviewed_broker_url():
    context, item = _fixture()
    item = replace(item, source_ref="https://example.test/unreviewed",
                   source_refs=("https://example.test/unreviewed", BROKER_URL))
    result = _audit(_with_item(context, item))
    assert result.status is StageStatus.BLOCKED
    assert "broker_sources_not_in_primary_ledger" in result.rationale


def _derived_fixture():
    from tests.test_new_business_research import path
    from valuation_engine.new_business_research import (
        business_path_from_dict, business_path_snapshot_hash, build_cashflow_proposal,
    )
    from valuation_engine.research_campaign import fingerprint

    context, direct = _fixture()
    raw = json.loads(json.dumps(asdict(path()), default=str))
    raw["economic_path_id"] = "capacity-path"

    def normalize(value):
        if isinstance(value, dict):
            if "assumption_refs" in value:
                value["economic_path_id"] = "capacity-path"
                value["source_refs"] = ["https://z-primary.test/" + value["assumption_refs"][0]]
            else:
                for child in value.values():
                    normalize(child)
        elif isinstance(value, list):
            for child in value:
                normalize(child)
    normalize(raw)
    raw["periods"][0]["utilization"].update(
        value=direct.value, source_refs=[BROKER_URL], assumption_refs=["utilization"])
    rows = build_cashflow_proposal(business_path_from_dict(raw)).underwriting_rows(prefix="fcff_year_")
    row = rows["fcff_year_1"]
    receipt = {"schema_version": "business-cashflow-receipt/v1",
        "plan_hash": fingerprint({"synthetic_plan": "conditional operating cashflow"}),
        "path_hash": business_path_snapshot_hash(raw), "path": raw,
        "metric_prefix": "fcff_year_", "source_refs": row["source_refs"],
        "assumptions": {"utilization": direct.research_receipt}}
    item = replace(direct, id="UW:T:fcff_year_1", metric="fcff_year_1",
        value=row["value"], unit=row["unit"], source_ref=row["source_refs"][0],
        source_refs=tuple(row["source_refs"]), research_receipt=None,
        business_cashflow_receipt=receipt)
    context.data["evidence_ledger"] = EvidenceLedger((item,
        evidence("E:UTILIZATION", "utilization"), evidence("E:LAND", "expansion_land_control")))
    return context, item


def _replace_derived(context, item):
    context.data["evidence_ledger"] = EvidenceLedger((item,
        evidence("E:UTILIZATION", "utilization"), evidence("E:LAND", "expansion_land_control")))
    return context


def test_reviewed_upstream_candidate_authorizes_exact_derived_cashflow_broker_source_only():
    context, item = _derived_fixture()
    assert item.source_ref == BROKER_URL
    result = _audit(context)
    assert result.status is StageStatus.PASS, result.rationale
    assert item.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
    assert item.research_receipt is None and item.business_cashflow_receipt
    assert not any(item.id in row[2] for row in result.outputs["broker_primary_verification_bindings"])


@pytest.mark.parametrize("mutation", ["blind", "missing_receipt_hash", "wrong_run", "ancestor_value", "ancestor_receipt"])
def test_derived_cashflow_does_not_bypass_mode_or_ancestor_receipt_validation(mutation):
    context, item = _derived_fixture()
    receipt = deepcopy(item.business_cashflow_receipt)
    if mutation == "blind":
        context.data["research_mode"] = "BLIND_BASELINE"
    elif mutation == "missing_receipt_hash":
        context.data.pop("informed_revision_receipt_hash")
    elif mutation == "wrong_run":
        context = OrchestratorContext("OTHER", context.execution_mode, context.data)
    elif mutation == "ancestor_value":
        receipt["assumptions"]["utilization"]["response"]["value"] = "0.9"
    else:
        receipt["assumptions"]["utilization"]["source_family_count"] = 20
    result = _audit(_replace_derived(context, replace(item, business_cashflow_receipt=receipt)))
    assert result.status is StageStatus.BLOCKED
    assert "broker_informed_revision_receipts_valid" in result.rationale
    assert "broker_sources_not_in_primary_ledger" in result.rationale


def test_recomputed_cashflow_math_cannot_substitute_a_different_upstream_reviewed_value():
    from valuation_engine.new_business_research import (
        business_path_from_dict, business_path_snapshot_hash, build_cashflow_proposal,
    )
    context, item = _derived_fixture()
    receipt = deepcopy(item.business_cashflow_receipt)
    receipt["path"]["periods"][0]["utilization"]["value"] = "0.7"
    receipt["path_hash"] = business_path_snapshot_hash(receipt["path"])
    row = build_cashflow_proposal(business_path_from_dict(receipt["path"])).underwriting_rows(prefix="fcff_year_")[item.metric]
    result = _audit(_replace_derived(context, replace(item, value=row["value"], business_cashflow_receipt=receipt)))
    assert result.status is StageStatus.BLOCKED
    finding = next(row for row in result.outputs["broker_research_audit_report"].findings
                   if row.check == "broker_informed_revision_receipts_valid")
    assert "actual path driver" in finding.detail


def test_reviewed_source_url_is_not_a_blanket_exception_for_other_cashflow_drivers():
    from valuation_engine.new_business_research import business_path_snapshot_hash
    context, item = _derived_fixture()
    receipt = deepcopy(item.business_cashflow_receipt)
    # Retain the existing primary source so FCFF source-set replay still passes;
    # add the reviewed broker URL to an unrelated unreviewed capex assumption.
    receipt["path"]["periods"][0]["capex"]["source_refs"].append(BROKER_URL)
    receipt["path_hash"] = business_path_snapshot_hash(receipt["path"])
    result = _audit(_replace_derived(context, replace(item, business_cashflow_receipt=receipt)))
    assert result.status is StageStatus.BLOCKED
    finding = next(row for row in result.outputs["broker_research_audit_report"].findings
                   if row.check == "broker_informed_revision_receipts_valid")
    assert "unreviewed broker-backed driver" in finding.detail
