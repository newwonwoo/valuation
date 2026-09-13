"""Operating research reaches declared evidence and the real assumption compiler."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from tests.test_new_business_research import path as business_path
from tests.test_research_campaign import answer, campaign_plan
from valuation_engine.research_campaign import ResearchCampaignError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("research_business_cli", ROOT / "scripts/run_research_campaign.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
D = Decimal


def plan():
    result = campaign_plan()
    result["requests"][0].update(metric="executable_capacity", unit="MW", economic_path_id="expansion")
    result["business_model"] = {
        "kind": "contracted_business", "segment": "core",
        "path": json.loads(json.dumps(asdict(business_path()), default=str)),
        "driver_bindings": {"capacity": "periods.0.capacity.value"},
        "discount_rate": "0.1", "discount_rate_source_refs": ["https://example.test/funding"],
        "discount_rate_rationale": "Explicit annual discount assumption for research sensitivity only.",
        "metric_prefix": "fcff_year_",
    }
    return result


def run(tmp_path, capacity):
    tmp_path.mkdir(parents=True)
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan()))
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(yaml.safe_dump({"target_id": "T", "as_of": "2026-08-29", "declarations": {}}))
    return cli.execute_campaign(plan_path, tmp_path / "work", underwriting_path=baseline,
                                provider=lambda order: answer(order, capacity))


def test_accepted_capacity_changes_fcff_and_retains_receipt_through_compiler(tmp_path):
    from valuation_engine.assumption_compiler import AssumptionSpec, CompilationStatus, compile_assumptions
    from valuation_engine.evidence_collection import EvidenceCollectionRequest
    from valuation_engine.generic_underwriting import declared_underwriting_collector
    from valuation_engine.ledger import EvidenceLedger
    from valuation_engine.records import AffectedVariable, BridgeRecord, Direction, HypothesisRecord, EvidenceSourceLayer

    compiled_values = []
    for capacity in ("10", "20"):
        result, merged_path = run(tmp_path / capacity, capacity)
        assert result["status"] == "READY_FOR_COMPILATION"
        assert result["business_cashflow"]["status"] == "RESEARCH_ESTIMATE"
        batch = declared_underwriting_collector(merged_path)(EvidenceCollectionRequest(
            target_id="T", required_metrics=("fcff_year_1",)))
        record = next(row for row in batch.records if row.metric == "fcff_year_1")
        assert record.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
        receipt = record.business_cashflow_receipt
        assert receipt["path"]["periods"][0]["capacity"]["value"] == capacity
        assert receipt["path"]["periods"][0]["capacity"]["assumption_refs"] == ["capacity"]
        assert receipt["assumptions"]["capacity"]["status"] == "ACCEPTED"
        assert receipt["assumptions"]["capacity"]["response"]["sources"][0]["locator"] == "capacity table"
        value = D(result["business_cashflow"]["fcff_path"][0])
        hypothesis = HypothesisRecord(id="H1", statement="Capacity permits contracted delivery and cashflow",
            causal_chain=("commissioned capacity", "contracted delivery", "FCFF"),
            supporting_evidence_ids=(record.id,), kill_conditions=("Delivery cancelled",))
        bridge = BridgeRecord(id="B1", evidence_ids=(record.id,), hypothesis_id="H1",
            affected_variable=AffectedVariable.SEGMENT_VALUE, direction=Direction.UP,
            old_value=0, new_value=float(value), unit="USD_million",
            rationale="Contracted revenue limited by capacity, net of explicit costs and investment",
            confidence=0.6, kill_condition="Delivery cancelled", verification_event="next filing",
            economic_path_id="expansion")
        compiled = compile_assumptions(target_id="T", ledger=EvidenceLedger(batch.records),
            hypotheses=(hypothesis,), bridges=(bridge,),
            specs=(AssumptionSpec("fcff_year_1", "Base", "B1", "USD_million", "identity_observation"),),
            bridge_input_map={})
        assert compiled.status is CompilationStatus.COMPILED
        compiled_values.append(compiled.assumption_set.get("fcff_year_1", "Base").measure.amount)
    assert compiled_values == [D("24"), D("72")]


def test_business_binding_unit_and_path_scope_must_match_request():
    for field, wrong in (("unit", "ratio"), ("economic_path_id", "unrelated")):
        candidate = plan()
        candidate["requests"][0][field] = wrong
        with pytest.raises(ResearchCampaignError, match="unit/economic path"):
            cli.model_evaluator(candidate)
    candidate = plan()
    candidate["business_model"]["driver_bindings"]["capacity"] = "periods.0.capacity.unit"
    with pytest.raises(ResearchCampaignError, match="typed estimate"):
        cli.model_evaluator(candidate)({"capacity": D("10")})


def test_business_research_discount_uses_calendar_gap_and_no_automatic_terminal():
    candidate = plan()
    near = cli.model_evaluator(candidate)({"capacity": D("10")})
    assert abs(near - sum((D("24") / D("1.1") ** year for year in range(1, 4)), D(0))) < D("1e-20")
    later = deepcopy(candidate)
    for period in later["business_model"]["path"]["periods"]:
        period["year"] += 1
    delayed = cli.model_evaluator(later)({"capacity": D("10")})
    assert abs(delayed - near / D("1.1")) < D("1e-20")
    invalid = deepcopy(candidate)
    invalid["as_of"] = "2027-01-01"
    with pytest.raises(ResearchCampaignError, match="after the as-of year"):
        cli.model_evaluator(invalid)({"capacity": D("10")})


def test_late_start_cannot_be_relabelled_as_first_year_compiler_cashflow(tmp_path):
    candidate = plan()
    for period in candidate["business_model"]["path"]["periods"]:
        period["year"] += 2
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(candidate))
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text(yaml.safe_dump({"target_id": "T", "as_of": "2026-08-29", "declarations": {}}))
    with pytest.raises(ResearchCampaignError, match="missing years explicitly"):
        cli.execute_campaign(plan_path, tmp_path / "work", underwriting_path=baseline,
                             provider=lambda order: answer(order, "10"))
    assert not list((tmp_path / "work").glob("underwriting-*.yaml"))
