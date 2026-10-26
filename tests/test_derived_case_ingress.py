"""Registered operating-driver replay reaches the real Evidence/Bridge compiler.

The tiny calculator below is a provenance fixture, not another financial model.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tests.test_broker_research_policy import informed_answer, reviewed_plan
from valuation_engine import derived_case
from valuation_engine.assumption_compiler import AssumptionSpec, CompilationStatus, compile_assumptions
from valuation_engine.control_plane import StageStatus
from valuation_engine.derived_case import (
    CaseCalculationModel, CaseCodeFile, DerivedCaseRegistry, DerivedDriverContract,
    DerivedOutputContract, build_derived_case_receipt, canonical_case_json, derived_case_hash,
)
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.generic_underwriting import (
    DeclaredUnderwritingError, DerivedCaseUnderwritingEvidenceRecord,
    declared_underwriting_collector, load_declared_underwriting,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import AffectedVariable, BridgeRecord, Direction, EvidenceSourceLayer, HypothesisRecord
from valuation_engine.research_campaign import run_campaign


PRIMARY_URL = "https://example.test/own-model-filing"
BROKER_URL = "https://example.test/report"


def _synthetic_calculate(baseline, drivers):
    cashflow = Decimal(baseline["capacity"]) * drivers["operating_rate"]
    return {"cashflow": cashflow, "liquidity": Decimal(baseline["cash"]) + cashflow,
            "terminal": cashflow, "ev_adjustment": Decimal(baseline["ev_adjustment"]),
            "shares": Decimal(baseline["shares"])}


def _fixture(tmp_path, *, new_run_id="INFORMED-1", plan=None):
    plan = deepcopy(plan or reviewed_plan())
    plan["informed_revision"]["new_run_id"] = new_run_id
    research = run_campaign(plan, responder=informed_answer)["outputs"]["utilization"]
    driver = DerivedDriverContract(input_name="operating_rate", metric="utilization", unit="ratio",
        period_start="2027-01-01", period_end="2027-12-31", frequency="FY", scope="core",
        perimeter="consolidated", accounting_basis="IFRS", currency="NONE", segment_id="core",
        economic_path_id="capacity-path", candidate_id="A1")
    outputs = tuple(DerivedOutputContract(output_key=key, metric=metric, unit=unit,
        scenario_id="Base", year=2027, segment_id="core", economic_path_id="synthetic-own-case",
        depends_on=("operating_rate",) if key in {"cashflow", "liquidity", "terminal"} else (),
        source_refs=(PRIMARY_URL,)) for key, metric, unit in (
            ("cashflow", "fcff_base_year_1", "KRW_billion"),
            ("liquidity", "liquidity_base_year_1", "KRW_billion"),
            ("terminal", "terminal_fcff_base", "KRW_billion"),
            ("ev_adjustment", "equity_bridge_adjustment", "KRW_billion"),
            ("shares", "fully_diluted_shares", "shares")))
    source = Path(__file__).resolve()
    model = CaseCalculationModel(model_id="synthetic-ingress-case", version="1",
        code_files=(CaseCodeFile(str(source), sha256(source.read_bytes()).hexdigest()),),
        baseline_inputs_json=canonical_case_json({"capacity": "100", "cash": "10",
            "ev_adjustment": "7", "shares": "1000"}),
        driver_contracts=(driver,), output_contracts=outputs, calculate=_synthetic_calculate)
    receipt = build_derived_case_receipt(model, target_id="T", as_of=plan["as_of"],
        baseline_run_id="BASELINE", new_run_id=new_run_id,
        review_receipt=plan["informed_revision"]["review_receipt"], drivers={"operating_rate": research})
    artifact = tmp_path / "case.json"
    artifact.write_text(canonical_case_json(receipt))
    ref = {"schema_version": "derived-case-reference/v1", "artifact_path": str(artifact),
           "artifact_sha256": sha256(artifact.read_bytes()).hexdigest()}
    payload = {"target_id": "T", "as_of": plan["as_of"], "research_mode": "INFORMED_REVISION",
        "informed_revision": plan["informed_revision"], "declarations": {
            row["metric"]: {"value": row["value"], "unit": row["unit"], "segment": row["segment_id"],
                "scenario_id": row["scenario_id"], "year": row["year"], "economic_path_id": row["economic_path_id"],
                "source_refs": row["source_refs"], "rationale": "Derived uncertain case from one individually reviewed operating driver.",
                "derived_case_ref": {**ref, "output_key": key}}
            for key, row in receipt["outputs"].items()}}
    path = tmp_path / "underwriting.yaml"
    _write(path, payload)
    return path, payload, receipt, DerivedCaseRegistry((model,))


def _write(path, payload):
    path.write_text(yaml.safe_dump(payload))


def _collect(path, registry):
    collector = declared_underwriting_collector(path, trusted_derived_case_registry=registry)
    metrics = tuple(load_declared_underwriting(path)["declarations"])
    return collector(EvidenceCollectionRequest(target_id="T", required_metrics=metrics))


def _replace_receipt(path, payload, receipt):
    receipt["outputs_hash"] = derived_case_hash(receipt["outputs"])
    receipt["receipt_hash"] = derived_case_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    artifact = Path(next(iter(payload["declarations"].values()))["derived_case_ref"]["artifact_path"])
    artifact.write_text(canonical_case_json(receipt))
    for row in payload["declarations"].values():
        row["derived_case_ref"]["artifact_sha256"] = sha256(artifact.read_bytes()).hexdigest()
    _write(path, payload)


def test_registered_case_compiles_through_actual_evidence_bridge_compiler(tmp_path):
    path, _, _, registry = _fixture(tmp_path)
    batch = _collect(path, registry)
    item = next(row for row in batch.records if row.metric == "fcff_base_year_1")
    assert isinstance(item, DerivedCaseUnderwritingEvidenceRecord)
    assert item.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
    assert item.research_receipt is None and item.business_cashflow_receipt is None
    assert item.derived_case_proof["upstream_origins"][0]["candidate_id"] == "A1"
    assert len(json.dumps(EvidenceLedger(batch.records).to_list())) < 16000
    hypothesis = HypothesisRecord(id="H1", statement="Conditional own-business cashflow",
        causal_chain=("operating driver", "own model", "cashflow"), supporting_evidence_ids=(item.id,),
        kill_conditions=("The operating driver is contradicted by new evidence",))
    bridge = BridgeRecord(id="B1", evidence_ids=(item.id,), hypothesis_id="H1",
        affected_variable=AffectedVariable.UTILIZATION, direction=Direction.UP, old_value=0,
        new_value=80, unit="KRW_billion", rationale="Deterministic own-case output from a reviewed conditional driver",
        confidence=0.6, kill_condition="New evidence contradicts the operating case", verification_event="next filing",
        economic_path_id="synthetic-own-case")
    compiled = compile_assumptions(target_id="T", ledger=EvidenceLedger(batch.records), hypotheses=(hypothesis,),
        bridges=(bridge,), specs=(AssumptionSpec(item.metric, "Base", "B1", "KRW_billion", "identity_observation"),),
        bridge_input_map={})
    assert compiled.status is CompilationStatus.COMPILED
    assert compiled.assumption_set.get(item.metric, "Base").measure.amount == Decimal("80.0")


def test_full_receipt_replays_once_for_all_outputs_and_repeated_collection(tmp_path):
    path, payload, _, registry = _fixture(tmp_path)
    with patch.object(derived_case, "_calculate", wraps=derived_case._calculate) as calculate:
        collector = declared_underwriting_collector(path, trusted_derived_case_registry=registry)
        request = EvidenceCollectionRequest(target_id="T", required_metrics=tuple(payload["declarations"]))
        assert len(collector(request).records) == 5
        assert len(collector(request).records) == 5
        assert calculate.call_count == 1  # The verifier itself evaluates twice for determinism.


def test_missing_registry_is_exact_work_required_not_metadata_authority(tmp_path):
    path, _, _, _ = _fixture(tmp_path)
    with pytest.raises(DeclaredUnderwritingError, match="WORK_REQUIRED.*orchestrator-supplied"):
        declared_underwriting_collector(path)


@pytest.mark.parametrize("change", ["mode", "revision", "both", "blind", "new_run", "review"])
def test_derived_mode_and_outer_review_cannot_be_removed_or_replaced(tmp_path, change):
    path, payload, _, _ = _fixture(tmp_path)
    if change in {"mode", "both"}:
        payload.pop("research_mode")
    if change in {"revision", "both"}:
        payload.pop("informed_revision")
    if change == "blind":
        payload["research_mode"] = "BLIND_BASELINE"
    elif change == "new_run":
        payload["informed_revision"]["new_run_id"] = "UNRELATED"
    elif change == "review":
        payload["informed_revision"]["review_receipt"]["receipt_hash"] = "0" * 64
    _write(path, payload)
    with pytest.raises(DeclaredUnderwritingError):
        load_declared_underwriting(path)


@pytest.mark.parametrize("field,value", [("value", "99"), ("unit", "ratio"), ("scenario_id", "Bull"),
    ("year", 2028), ("year", True), ("segment", "other"), ("economic_path_id", "other-path"),
    ("source_refs", ["https://example.test/changed-source"])])
def test_output_row_cannot_substitute_another_value_or_contract(tmp_path, field, value):
    path, payload, _, registry = _fixture(tmp_path)
    payload["declarations"]["fcff_base_year_1"][field] = value
    _write(path, payload)
    with pytest.raises(DeclaredUnderwritingError, match="exact declaration"):
        _collect(path, registry)


@pytest.mark.parametrize("change", ["key", "metric"])
def test_output_key_or_metric_substitution_is_not_a_receipt_reuse(tmp_path, change):
    path, payload, _, registry = _fixture(tmp_path)
    if change == "key":
        payload["declarations"]["fcff_base_year_1"]["derived_case_ref"]["output_key"] = "liquidity"
    else:
        payload["declarations"]["unreviewed_metric"] = payload["declarations"].pop("fcff_base_year_1")
    _write(path, payload)
    with pytest.raises(DeclaredUnderwritingError, match="exact declaration"):
        _collect(path, registry)


@pytest.mark.parametrize("change", ["baseline", "driver", "cashflow", "liquidity", "terminal", "ev_adjustment", "shares"])
def test_self_rehashed_receipt_cannot_change_unrelated_parameters_or_outputs(tmp_path, change):
    path, payload, receipt, registry = _fixture(tmp_path)
    if change == "baseline":
        receipt["baseline_inputs"]["cash"] = "99999"
        receipt["baseline_inputs_hash"] = derived_case_hash(receipt["baseline_inputs"])
    elif change == "driver":
        receipt["driver_values"]["operating_rate"] = "0.9"
    else:
        receipt["outputs"][change]["value"] = "99999"
        payload["declarations"][receipt["outputs"][change]["metric"]]["value"] = "99999"
    _replace_receipt(path, payload, receipt)
    with pytest.raises(DeclaredUnderwritingError, match="replay"):
        _collect(path, registry)


@pytest.mark.parametrize("field,value", [("source_family", "invented-independent-family"),
    ("content_sha256", "f" * 64), ("kind", "primary"), ("url", "https://example.test/substituted")])
def test_original_driver_source_identity_cannot_drift_even_after_rehash(tmp_path, field, value):
    path, payload, receipt, registry = _fixture(tmp_path)
    receipt["drivers"]["operating_rate"]["response"]["sources"][0][field] = value
    _replace_receipt(path, payload, receipt)
    with pytest.raises(DeclaredUnderwritingError, match="replay"):
        _collect(path, registry)


def test_relative_reference_is_resolved_but_still_hash_bound(tmp_path):
    path, payload, _, registry = _fixture(tmp_path)
    for row in payload["declarations"].values():
        row["derived_case_ref"]["artifact_path"] = "case.json"
    _write(path, payload)
    assert len(_collect(path, registry).records) == 5
    (tmp_path / "case.json").write_text("{}")
    with pytest.raises(DeclaredUnderwritingError, match="content hash"):
        _collect(path, registry)


def _audit_records(records, *, context=None):
    from tests.test_broker_informed_audit import _fixture as broker_fixture
    from tests.test_broker_runtime import evidence
    from valuation_engine.broker_runtime_v2 import broker_research_audit_adapter
    context = context or broker_fixture()[0]
    context.data["evidence_ledger"] = EvidenceLedger((*records,
        evidence("E:UTILIZATION", "utilization"), evidence("E:LAND", "expansion_land_control")))
    return broker_research_audit_adapter(required=True)(context)


def test_registered_replay_is_a_narrow_broker_source_exception_not_primary_truth(tmp_path):
    path, _, _, registry = _fixture(tmp_path)
    batch = _collect(path, registry)
    result = _audit_records(batch.records)
    assert result.status is StageStatus.PASS, result.rationale
    assert all(row.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING for row in batch.records)
    assert not any(set(ids) & {row.id for row in batch.records}
                   for _, _, ids in result.outputs["broker_primary_verification_bindings"])
    findings = {row.check: row for row in result.outputs["broker_research_audit_report"].findings}
    assert findings["broker_informed_revision_receipts_valid"].passed
    assert findings["broker_sources_not_in_primary_ledger"].passed


@pytest.mark.parametrize("field,value", [("value", "99"), ("unit", "ratio"), ("metric", "other"),
    ("scenario_id", "Bull"), ("year", 2028), ("segment", "other"), ("target", "OTHER"),
    ("observed_date", "2026-10-25"), ("economic_path_id", "other"),
    ("source_layer", EvidenceSourceLayer.REALIZED_OR_FILING)])
def test_broker_audit_independently_binds_each_registered_output_row(tmp_path, field, value):
    path, _, _, registry = _fixture(tmp_path)
    item = next(row for row in _collect(path, registry).records if row.metric == "fcff_base_year_1")
    result = _audit_records((replace(item, **{field: value}),))
    assert result.status is StageStatus.BLOCKED
    assert "broker_informed_revision_receipts_valid" in result.rationale


@pytest.mark.parametrize("change", ["mode", "review", "run", "proof", "source", "reference_key", "artifact", "missing_artifact"])
def test_broker_audit_checks_actual_run_proof_sources_and_artifact_after_ingress(tmp_path, change):
    from tests.test_broker_informed_audit import _fixture as broker_fixture
    from valuation_engine.orchestrator import OrchestratorContext
    path, _, _, registry = _fixture(tmp_path)
    item = next(row for row in _collect(path, registry).records if row.metric == "fcff_base_year_1")
    context, _ = broker_fixture()
    if change == "mode":
        context.data["research_mode"] = "BLIND_BASELINE"
    elif change == "review":
        context.data["informed_revision_receipt_hash"] = "0" * 64
    elif change == "run":
        context = OrchestratorContext("OTHER", context.execution_mode, context.data)
    elif change == "proof":
        item = replace(item, derived_case_proof={**item.derived_case_proof, "model_hash": "0" * 64})
    elif change == "source":
        item = replace(item, source_ref=PRIMARY_URL, source_refs=(PRIMARY_URL,))
    elif change == "reference_key":
        item = replace(item, derived_case_ref={**item.derived_case_ref, "output_key": "liquidity"})
    elif change == "artifact":
        Path(item.derived_case_ref["artifact_path"]).write_text("{}")
    else:
        Path(item.derived_case_ref["artifact_path"]).unlink()
    result = _audit_records((item,), context=context)
    assert result.status is StageStatus.BLOCKED
    assert "broker_informed_revision_receipts_valid" in result.rationale


def test_persisted_proof_or_pass_metadata_cannot_replace_actual_registered_replay(tmp_path, monkeypatch):
    path, _, _, registry = _fixture(tmp_path)
    item = next(row for row in _collect(path, registry).records if row.metric == "fcff_base_year_1")
    # Simulates a persisted Evidence row reloaded in a new process without the
    # orchestrator's trusted registry/replay. Exact old metadata is insufficient.
    monkeypatch.setattr(derived_case, "_REPLAY_ATTESTATIONS", {})
    result = _audit_records((item,))
    assert result.status is StageStatus.BLOCKED
    finding = next(row for row in result.outputs["broker_research_audit_report"].findings
                   if row.check == "broker_informed_revision_receipts_valid")
    assert "no trusted process-local replay" in finding.detail
    result = _audit_records((replace(item, derived_case_proof={"status": "PASS"}),))
    assert result.status is StageStatus.BLOCKED


def test_cached_collector_rechecks_artifact_bytes_before_reusing_replay(tmp_path):
    path, payload, _, registry = _fixture(tmp_path)
    collector = declared_underwriting_collector(path, trusted_derived_case_registry=registry)
    request = EvidenceCollectionRequest(target_id="T", required_metrics=tuple(payload["declarations"]))
    collector(request)
    (tmp_path / "case.json").write_text("{}")
    with pytest.raises(DeclaredUnderwritingError, match="content hash"):
        collector(request)


@pytest.mark.parametrize("warm_cache", [False, True])
def test_derived_collector_rejects_llm_actor_even_with_cached_replay(tmp_path, warm_cache):
    from valuation_engine.runtime_authority import llm_proposal_scope

    path, payload, _, registry = _fixture(tmp_path)
    collector = declared_underwriting_collector(path, trusted_derived_case_registry=registry)
    request = EvidenceCollectionRequest(target_id="T", required_metrics=tuple(payload["declarations"]))
    if warm_cache:
        assert len(collector(request).records) == 5
    with llm_proposal_scope(), pytest.raises(PermissionError, match="assumption_compile"):
        collector(request)
    # The rejected proposal invocation neither discards nor elevates the
    # deterministic collector's authority outside the LLM proposal scope.
    assert len(collector(request).records) == 5


def test_nested_direct_response_cannot_hide_informed_lineage_by_stripping_its_order(tmp_path):
    from valuation_engine.research_campaign import merge_underwriting
    plan = reviewed_plan()
    payload = merge_underwriting({"target_id": "T", "as_of": plan["as_of"], "declarations": {}},
                                run_campaign(plan, responder=informed_answer))
    payload.pop("research_mode")
    payload.pop("informed_revision")
    order = payload["declarations"]["utilization"]["research_receipt"]["work_order"]
    order.pop("research_mode")
    order.pop("informed_revision")
    path = tmp_path / "stripped-direct.yaml"
    _write(path, payload)
    with pytest.raises(DeclaredUnderwritingError, match="nested informed"):
        load_declared_underwriting(path)
