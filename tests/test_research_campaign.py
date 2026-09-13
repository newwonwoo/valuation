"""Research acceptance, selective reuse, and the actual underwriting compiler."""
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from valuation_engine.research_campaign import (
    ResearchCampaignError, make_work_order, merge_underwriting, run_campaign,
    validate_campaign, validate_response,
)


def campaign_plan():
    return {"schema_version": "research-campaign/v1", "target_id": "T",
            "as_of": "2026-08-29", "model_version": "engine-test", "policy_version": "policy-test",
            "requests": [{"request_id": "capacity", "metric": "utilization", "segment": "core",
                          "unit": "ratio", "question": "Estimate deliverable utilization from the source.",
                          "economic_path_id": "capacity-path", "mandatory": True}]}


def answer(order, value="0.8"):
    return {"schema_version": "research-response/v1", "request_id": order["request"]["request_id"],
            "request_hash": order["request_hash"], "status": "answered", "kind": "inferred",
            "unit": order["request"]["unit"], "value": value,
            "bounds": {"low": "0.1", "high": "100"},
            "rationale": "Assumed deliverable capacity from the cited commissioned facilities.",
            "invalidation_condition": "Customer delivery is cancelled.",
            "uncertainty": "Commissioning may be delayed.", "counterevidence": [],
            "counterevidence_search": "Searched cancellation and commissioning notices.",
            "sources": [{"source_id": "S1", "source_family": "issuer", "kind": "primary",
                         "url": "https://example.test/filing", "published_at": "2026-08-01",
                         "first_seen_at": "2026-08-02", "locator": "capacity table",
                         "excerpt": "The facility is commissioned.", "content_sha256": "a" * 64}]}


def test_pending_answer_reuse_and_updated_sensitivity():
    plan, cache = campaign_plan(), {}
    pending = run_campaign(plan, responder=lambda _: None, cache=cache)
    assert pending["status"] == "WORK_REQUIRED"
    assert pending["priorities"][0]["status"] == "NOT_YET_MEASURED"
    ready = run_campaign(plan, responder=answer, cache=cache,
                         evaluator=lambda values: values["capacity"] * Decimal(100))
    assert ready["status"] == "READY_FOR_COMPILATION"
    assert ready["updated_priorities"][0]["status"] == "MEASURED"
    assert Decimal(ready["updated_priorities"][0]["absolute_swing"]) > 0
    reused = run_campaign(plan, responder=lambda _: pytest.fail("cached work ran"), cache=cache)
    assert reused["executed_task_ids"] == []
    assert reused["reused_task_ids"] == ["capacity"]


def test_parent_response_change_invalidates_only_descendants():
    plan, cache = campaign_plan(), {}
    plan["requests"] += [{**plan["requests"][0], "request_id": "revenue", "metric": "revenue_ratio",
                          "depends_on": ["capacity"]},
                         {**plan["requests"][0], "request_id": "independent", "metric": "cost_ratio"}]
    run_campaign(plan, responder=answer, cache=cache, response_versions={"capacity": "v1"})
    result = run_campaign(plan, responder=lambda order: answer(order, "0.9"), cache=cache,
                          response_versions={"capacity": "v2"})
    assert set(result["executed_task_ids"]) == {"capacity", "revenue"}
    assert result["reused_task_ids"] == ["independent"]


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(request_hash="stale"),
    lambda row: row.update(value=float("nan")),
    lambda row: row.update(bounds=[]),
    lambda row: row.update(sources=[None]),
    lambda row: row.update(target_price=123),
])
def test_invalid_responses_remain_repairable(mutation):
    def respond(order):
        row = answer(order)
        mutation(row)
        return row
    result = run_campaign(campaign_plan(), responder=respond)
    assert result["status"] == "WORK_REQUIRED"
    assert "work_order" in result["outputs"]["capacity"]


def test_hyphenated_target_price_cannot_be_a_metric():
    plan = campaign_plan()
    plan["requests"][0]["metric"] = "target-price"
    with pytest.raises(ResearchCampaignError):
        validate_campaign(plan)


def test_broker_estimate_needs_searches_and_inference_label():
    plan = campaign_plan()
    order = make_work_order(plan, plan["requests"][0], {})
    row = answer(order)
    row["sources"][0]["kind"] = "broker_estimate"
    with pytest.raises(ResearchCampaignError, match="search"):
        validate_response(order, row)
    row["search_attempts"] = [{"tier": tier, "query": "capacity filing", "outcome": "No direct annual schedule found"}
                              for tier in ("primary", "independent")]
    row["independent_reasoning"] = "Cross checked the commissioning schedule against customer requirements."
    row["kind"] = "observed"
    with pytest.raises(ResearchCampaignError):
        validate_response(order, row)
    row["kind"] = "inferred"
    accepted = validate_response(order, row)
    assert accepted["authority"] == "analyst_underwriting"


def test_research_value_and_structured_receipt_reach_real_compiler(tmp_path):
    from valuation_engine.assumption_compiler import AssumptionSpec, CompilationStatus, compile_assumptions
    from valuation_engine.evidence_collection import EvidenceCollectionRequest
    from valuation_engine.generic_underwriting import declared_underwriting_collector
    from valuation_engine.ledger import EvidenceLedger
    from valuation_engine.records import AffectedVariable, BridgeRecord, Direction, HypothesisRecord, EvidenceSourceLayer
    plan = campaign_plan()
    values = []
    for number in ("0.7", "0.9"):
        result = run_campaign(plan, responder=lambda order: answer(order, number))
        merged = merge_underwriting({"target_id": "T", "as_of": plan["as_of"], "declarations": {}}, result)
        path = tmp_path / f"underwriting-{number}.yaml"
        path.write_text(yaml.safe_dump(merged))
        batch = declared_underwriting_collector(path)(EvidenceCollectionRequest(target_id="T", required_metrics=("utilization",)))
        record = batch.records[0]
        assert record.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
        assert record.research_receipt["response"]["bounds"]["low"] == "0.1"
        hypothesis = HypothesisRecord(id="H1", statement="Utilization determines achievable deliveries",
            causal_chain=("commissioning", "utilization", "cashflow"), supporting_evidence_ids=(record.id,),
            kill_conditions=("Customer delivery is cancelled",))
        bridge = BridgeRecord(id="B1", evidence_ids=(record.id,), hypothesis_id="H1",
            affected_variable=AffectedVariable.UTILIZATION, direction=Direction.UP, old_value=0,
            new_value=float(number), unit="ratio", rationale="Source-backed commissioning utilization inference",
            confidence=0.6, kill_condition="Customer delivery is cancelled", verification_event="next filing",
            economic_path_id="capacity-path")
        compiled = compile_assumptions(target_id="T", ledger=EvidenceLedger(batch.records),
            hypotheses=(hypothesis,), bridges=(bridge,),
            specs=(AssumptionSpec("utilization", "Base", "B1", "ratio", "identity_observation"),), bridge_input_map={})
        assert compiled.status is CompilationStatus.COMPILED
        values.append(compiled.assumption_set.get("utilization", "Base").measure.amount)
    assert values == [Decimal("0.7"), Decimal("0.9")]


def test_modified_acceptance_receipt_cannot_enter_underwriting():
    plan = campaign_plan()
    result = run_campaign(plan, responder=answer)
    result["outputs"]["capacity"]["value"] = "99"
    with pytest.raises(ResearchCampaignError, match="changed"):
        merge_underwriting({"target_id": "T", "as_of": plan["as_of"], "declarations": {}}, result)
