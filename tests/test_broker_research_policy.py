"""Broker-source semantics are judgment; lineage and authority are hard contracts."""
from copy import deepcopy

import pytest

from valuation_engine.broker_judgment import (
    build_broker_judgment_request, validate_broker_judgment_response,
)
from valuation_engine.research_campaign import (
    ResearchCampaignError, fingerprint, make_work_order, merge_underwriting,
    run_campaign, validate_campaign, validate_response,
)


def plan():
    return {"schema_version": "research-campaign/v1", "target_id": "T", "as_of": "2026-10-26",
        "model_version": "test", "policy_version": "test", "requests": [{"request_id": "utilization",
        "metric": "utilization", "segment": "core", "unit": "ratio", "question": "Assess supported utilization.",
        "economic_path_id": "capacity-path", "mandatory": True}]}


def answer(order):
    return {"schema_version": "research-response/v1", "request_id": "utilization",
        "request_hash": order["request_hash"], "status": "answered", "kind": "inferred", "unit": "ratio",
        "value": "0.8", "bounds": {"low": "0.6", "high": "0.9"},
        "rationale": "The proposal follows the explicitly uncertain operating capacity case.",
        "uncertainty": "Customer acceptance and timing remain uncertain.",
        "invalidation_condition": "Reconsider after adverse commissioning or customer evidence.",
        "counterevidence": ["Orders may be delayed despite installed capacity."],
        "search_attempts": [{"tier": tier, "query": "Capacity and customer evidence",
            "outcome": "No directly equivalent public forward estimate was available."}
            for tier in ("primary", "independent")],
        "independent_reasoning": "Evaluate the capacity mechanism and failure conditions rather than fit a price.",
        "sources": [{"source_id": "S1", "source_family": "capacity-study", "kind": "broker_estimate",
            "url": "https://example.test/report", "published_at": "2026-05-14", "first_seen_at": "2026-10-26",
            "locator": "page 2", "excerpt": "Capacity utilization forecast with uncertain demand.",
            "content_sha256": "a" * 64}]}


def reviewed_plan(*, ranged=False, claim_type="BROKER_FORECAST", unknown_vintage=False,
                  comparison_context=None, extra_refs=()):
    basis = {"period_start": "2027-01-01", "period_end": "2027-12-31", "frequency": "FY",
        "scope": "core", "perimeter": "consolidated", "accounting_basis": "IFRS", "currency": "NONE"}
    reference = {"reference_id": "R1", "reference_kind": "AGGREGATE_SNAPSHOT" if unknown_vintage else "NAMED_REPORT",
        "provider_name": "Public provider", "broker": None if unknown_vintage else "Broker",
        "source_ref": "https://example.test/report", "content_sha256": "a" * 64,
        "authorization_basis": "public_access", "observed_at": "2026-10-26T06:00:00+00:00",
        "original_published_at": None if unknown_vintage else "2026-05-14",
        "snapshot_as_of": None, "last_rating_at": None, "rating": "NR", "target_price": None,
        "underlying_source_families": ["capacity-study"],
        "claims": [{"claim_id": "C1", "claim_type": claim_type,
            "summary": "An uncertain operating assumption useful for individual review.",
            "source_locator": "page 2", "underlying_source_families": ["capacity-study"]}]}
    req = build_broker_judgment_request(target_id="T", intrinsic_as_of="2026-10-26",
        comparison_as_of="2026-10-26T06:00:00+00:00", run_id="BASELINE", freeze_token_hash="b" * 64,
        comparison_payload={"status": "BASIS_DIFFERENT", **(comparison_context or {})}, references_payload={
            "schema_version": "broker-reference-bundle/v1", "target_id": "T",
            "comparison_as_of": "2026-10-26T06:00:00+00:00", "references": [reference],
            "search_coverage": "Public reports", "limitations": []})
    candidate = {"candidate_id": "A1", "metric": "utilization", "unit": "ratio", **basis,
        "value": None if ranged else "0.8", "low": "0.6" if ranged else None, "high": "0.9" if ranged else None,
        "derivation": "Retain the reviewed operating assumption with explicit dependency and uncertainty.",
        "evidence_refs": ["R1", *extra_refs], "counterevidence": ["Customer acceptance may slip."],
        "uncertainty": "Future realization is not confirmed."}
    decision = {"claim_id": "C1", "disposition": "PROPOSE_NEW_RUN_ASSUMPTION",
        "rationale": "Adopt this case as a provisional assumption, not an observed result.",
        "evidence_refs": ["R1", *extra_refs], "support_relation": "UNKNOWN", "basis_status": "DIFFERENT",
        "uncertainty": "Demand conversion remains uncertain.", "missing_evidence": [],
        "research_question": "Is the proposed utilization suitable for a conditional scenario?",
        "proposed_metric": "utilization", "independent_derivation_plan": "Assess source dependence, alternatives and the proposed case individually.",
        "falsifier": "New customer evidence contradicts expected commissioning.",
        "normalization_requirements": ["Retain the exact FY2027 perimeter and ratio units."],
        "source_dependence": "One broker study, not independently confirmed.",
        "impact_scope": "Core operating utilization only.", "assumption_candidate": candidate}
    receipt = validate_broker_judgment_response({"schema_version": "host-judgment-response/v1",
        "request_hash": req.request_hash, "target_id": "T", "as_of": req.gap.as_of,
        "decision": "REVIEW_CLAIMS", "proposal": {"decisions": [decision]},
        "rationale": "Individual adoption is justified conditionally; no output-price fitting.",
        "counterevidence": [], "uncertainty": "Forward assumptions remain uncertain."}, req)
    p = plan()
    p.update(research_mode="INFORMED_REVISION", informed_revision={"schema_version": "broker-informed-revision/v1",
        "baseline_run_id": "BASELINE", "new_run_id": "INFORMED-1", "review_receipt": receipt.to_dict()})
    p["requests"][0]["inference_basis"] = basis
    return p


def informed_answer(order):
    row = answer(order)
    receipt = order["informed_revision"]["review_receipt"]
    reviewed = receipt["decisions"][0]
    row.pop("independent_reasoning")
    row["adoption_reasoning"] = "Adopt the same forecast as a dependent and uncertain operating assumption after individual review."
    row["sources"][0].update(review_claim_id="C1", claim_type=reviewed["claim_type"])
    if reviewed["original_published_at"] is None:
        row["sources"][0].update(published_at=None, publication_status="UNKNOWN")
    row["informed_assumption"] = {"claim_id": "C1", "candidate_id": "A1", "review_receipt_hash": receipt["receipt_hash"],
        "inference_basis": order["request"]["inference_basis"],
        "derivation": "Retain the individually assessed point as a conditional operating assumption, not fact."}
    return row


def test_unclassified_legacy_estimate_is_work_required_never_numerical_acceptance():
    p = plan(); order = make_work_order(p, p["requests"][0], {})
    result = validate_response(order, answer(order))
    assert result["status"] == "WORK_REQUIRED"
    assert result["authority"] == "analyst_underwriting"  # possible future layer, not approval
    assert "value" not in result and "response" not in result
    cache = {}
    pending = run_campaign(p, responder=answer, cache=cache)
    assert pending["status"] == "WORK_REQUIRED" and cache == {}
    with pytest.raises(ResearchCampaignError):
        merge_underwriting({"target_id": "T", "as_of": p["as_of"], "declarations": {}}, pending)


@pytest.mark.parametrize("kind", ["broker_estimate", "primary", "industry"])
def test_blind_target_forecast_cannot_be_smuggled_by_source_label(kind):
    p = plan(); order = make_work_order(p, p["requests"][0], {})
    row = answer(order)
    row["sources"][0].update(kind=kind, claim_type="broker_forecast", subject_scope="target", subject_id="T")
    with pytest.raises(ResearchCampaignError, match="forecast"):
        validate_response(order, row)


@pytest.mark.parametrize("scope", ["industry", "peer"])
def test_scoped_non_target_expert_mechanism_remains_available(scope):
    p = plan(); order = make_work_order(p, p["requests"][0], {})
    row = answer(order)
    row["sources"][0].update(claim_type="industry_mechanism", subject_scope=scope, subject_id="sector-or-peer")
    assert validate_response(order, row)["status"] == "ACCEPTED"


def test_target_fact_requires_exact_claim_not_same_metric_or_industry_label():
    p = plan(); order = make_work_order(p, p["requests"][0], {})
    row = answer(order)
    source = row["sources"][0]
    source.update(claim_type="realized_fact", subject_scope="target", subject_id="T", metric="utilization",
        period="2026-H1", perimeter="consolidated", accounting_basis="IFRS", unit="ratio", claim_value="0.8",
        independent_support_source_ids=["P1"])
    primary = {**source, "source_id": "P1", "kind": "primary", "url": "https://example.test/filing", "evidence_relation": "supports"}
    row["sources"].append(primary)
    assert validate_response(order, row)["source_family_count"] == 1
    for field, value in (("period", "2027"), ("claim_value", "0.7"), ("evidence_relation", "contradicts"), ("kind", "industry")):
        bad = deepcopy(row); bad["sources"][1][field] = value
        with pytest.raises(ResearchCampaignError):
            validate_response(order, bad)


@pytest.mark.parametrize("ranged", [False, True])
def test_informed_forecast_adoption_can_use_same_number_without_future_fact(ranged):
    p = reviewed_plan(ranged=ranged)
    result = run_campaign(p, responder=informed_answer)
    assert result["status"] == "READY_FOR_COMPILATION"
    out = result["outputs"]["utilization"]
    assert out["value"] == "0.8" and out["authority"] == "analyst_underwriting"
    assert out["response"]["sources"][0]["kind"] == "broker_estimate"
    assert out["broker_source_policy"]["research_mode"] == "INFORMED_REVISION"
    merged = merge_underwriting({"target_id": "T", "as_of": p["as_of"], "declarations": {}}, result)
    assert merged["declarations"]["utilization"]["value"] == "0.8"


def test_unknown_original_aggregate_date_is_not_backfilled_with_observation_date():
    p = reviewed_plan(claim_type="METHOD_ASSUMPTION", unknown_vintage=True)
    order = make_work_order(p, p["requests"][0], {})
    row = informed_answer(order)
    assert validate_response(order, row)["status"] == "ACCEPTED"
    row["sources"][0]["published_at"] = "2026-10-26"
    with pytest.raises(ResearchCampaignError, match="publication date"):
        validate_response(order, row)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(kind="observed"),
    lambda r: r.update(value="0.7"),
    lambda r: r["informed_assumption"].update(review_receipt_hash="0" * 64),
    lambda r: r["informed_assumption"].update(candidate_id="wrong"),
    lambda r: r["informed_assumption"].update(inference_basis={"period_end": "2028-12-31"}),
    lambda r: r["sources"][0].update(content_sha256="c" * 64),
    lambda r: r["sources"][0].update(url="https://example.test/other"),
    lambda r: r["sources"][0].update(kind="primary"),
    lambda r: r.update(final_value="10000"),
])
def test_informed_mode_checks_binding_and_authority_not_economic_source_label(mutation):
    p = reviewed_plan(); order = make_work_order(p, p["requests"][0], {})
    row = informed_answer(order); mutation(row)
    with pytest.raises(ValueError):
        validate_response(order, row)


@pytest.mark.parametrize("mutation", [
    lambda p: p["informed_revision"].update(new_run_id="BASELINE"),
    lambda p: p.update(target_id="OTHER"),
    lambda p: p.update(as_of="2026-10-25"),
    lambda p: p.update(research_mode="BLIND_BASELINE"),
    lambda p: p["informed_revision"]["review_receipt"].update(value_commit_allowed=True),
    lambda p: p["informed_revision"]["review_receipt"]["decisions"][0].update(rationale="tampered"),
])
def test_review_cannot_authorize_same_run_backdating_or_change_its_payload(mutation):
    p = reviewed_plan(); mutation(p)
    with pytest.raises(ValueError):
        validate_campaign(p)


def test_revision_identity_changes_workorder_and_cache():
    p = reviewed_plan(); cache = {}
    first = run_campaign(p, responder=informed_answer, cache=cache)
    assert first["executed_task_ids"] == ["utilization"]
    reused = run_campaign(p, responder=lambda _: pytest.fail("same receipt should reuse"), cache=cache)
    assert reused["reused_task_ids"] == ["utilization"]
    changed = deepcopy(p); changed["informed_revision"]["new_run_id"] = "INFORMED-2"
    second = run_campaign(changed, responder=informed_answer, cache=cache)
    assert second["executed_task_ids"] == ["utilization"]
    assert second["outputs"]["utilization"]["request_hash"] != first["outputs"]["utilization"]["request_hash"]


def test_pre_policy_legacy_acceptance_cannot_survive_cache_or_merge():
    p = plan(); cache = {}
    def primary(order):
        row = answer(order); row["sources"][0]["kind"] = "primary"; return row
    good = run_campaign(p, responder=primary, cache=cache)
    old = deepcopy(good["outputs"]["utilization"])
    old["response"]["sources"][0]["kind"] = "broker_estimate"
    old["response_hash"] = fingerprint(old["response"])
    entry = next(iter(cache.values())); entry["output"] = old; entry["output_hash"] = fingerprint(old)
    retried = run_campaign(p, responder=answer, cache=cache)
    assert retried["executed_task_ids"] == ["utilization"] and retried["status"] == "WORK_REQUIRED"
    tampered_result = deepcopy(good); tampered_result["outputs"]["utilization"] = old
    with pytest.raises(ResearchCampaignError):
        merge_underwriting({"target_id": "T", "as_of": p["as_of"], "declarations": {}}, tampered_result)


def test_informed_candidate_reaches_existing_evidence_bridge_compiler_as_underwriting(tmp_path):
    import yaml
    from decimal import Decimal
    from valuation_engine.assumption_compiler import AssumptionSpec, CompilationStatus, compile_assumptions
    from valuation_engine.evidence_collection import EvidenceCollectionRequest
    from valuation_engine.generic_underwriting import declared_underwriting_collector
    from valuation_engine.ledger import EvidenceLedger
    from valuation_engine.records import AffectedVariable, BridgeRecord, Direction, HypothesisRecord, EvidenceSourceLayer

    p = reviewed_plan()
    baseline = {"target_id": "T", "as_of": p["as_of"], "declarations": {}}
    before = deepcopy(baseline)
    result = run_campaign(p, responder=informed_answer)
    merged = merge_underwriting(baseline, result)
    assert baseline == before
    path = tmp_path / "informed.yaml"
    path.write_text(yaml.safe_dump(merged))
    batch = declared_underwriting_collector(path)(EvidenceCollectionRequest(target_id="T", required_metrics=("utilization",)))
    record = batch.records[0]
    assert record.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
    assert record.research_receipt["broker_source_policy"]["baseline_run_id"] == "BASELINE"
    hypothesis = HypothesisRecord(id="H1", statement="Conditional utilization supports deliveries",
        causal_chain=("capacity", "utilization", "cashflow"), supporting_evidence_ids=(record.id,),
        kill_conditions=("Customer evidence contradicts adoption",))
    bridge = BridgeRecord(id="B1", evidence_ids=(record.id,), hypothesis_id="H1",
        affected_variable=AffectedVariable.UTILIZATION, direction=Direction.UP, old_value=0,
        new_value=0.8, unit="ratio", rationale="Individually reviewed dependent forecast retained as uncertain underwriting",
        confidence=0.6, kill_condition="Customer evidence contradicts adoption", verification_event="next filing",
        economic_path_id="capacity-path")
    compiled = compile_assumptions(target_id="T", ledger=EvidenceLedger(batch.records),
        hypotheses=(hypothesis,), bridges=(bridge,),
        specs=(AssumptionSpec("utilization", "Base", "B1", "ratio", "identity_observation"),), bridge_input_map={})
    assert compiled.status is CompilationStatus.COMPILED
    assert compiled.assumption_set.get("utilization", "Base").measure.amount == Decimal("0.8")


def test_informed_result_mode_cannot_be_dropped_at_merge():
    p = reviewed_plan(); result = run_campaign(p, responder=informed_answer)
    existing = {"target_id": "T", "as_of": p["as_of"], "declarations": {}}
    merged = merge_underwriting(existing, result)
    assert merged["research_mode"] == "INFORMED_REVISION"
    assert merged["informed_revision"]["new_run_id"] == "INFORMED-1"
    result.pop("research_mode"); result.pop("informed_revision")
    with pytest.raises(ResearchCampaignError, match="revision bindings"):
        merge_underwriting(existing, result)


def test_range_cannot_change_without_a_new_reviewed_candidate():
    p = reviewed_plan(ranged=True); order = make_work_order(p, p["requests"][0], {})
    row = informed_answer(order); row["bounds"]["low"] = "0.1"
    with pytest.raises(ResearchCampaignError, match="bounds"):
        validate_response(order, row)


def test_legacy_broker_lead_is_discovery_only_and_industry_consensus_not_target_forecast():
    p = plan(); order = make_work_order(p, p["requests"][0], {})
    row = answer(order); row["sources"][0]["kind"] = "broker_lead"
    row["sources"].append({**row["sources"][0], "source_id": "P1", "kind": "primary", "url": "https://example.test/original"})
    out = validate_response(order, row)
    assert out["broker_source_policy"]["dispositions"][0]["disposition"] == "LEGACY_DISCOVERY_ONLY"
    row = answer(order); row["sources"][0].update(claim_type="consensus", subject_scope="industry", subject_id="sector")
    assert validate_response(order, row)["status"] == "ACCEPTED"


def context_plan(tmp_path, *, document_hash=True, source_layer="realized_or_filing"):
    from hashlib import sha256
    artifact = tmp_path / "operating_cases.json"
    artifact.write_text('{"economic_fcff": "-43.98370577960", "kind": "analyst_case"}\n')
    frozen = {"id": "PRIMARY1", "source_ref": "https://example.test/filing", "target": "T",
        "metric": "issued_shares", "value": "1234", "unit": "shares", "source_layer": source_layer,
        "status": "active", "source_family": "issuer-filing", "effective_date": "2026-06-30"}
    if document_hash:
        frozen["content_sha256"] = "c" * 64
    context = {"frozen_model_context": {"evidence": [frozen]}, "followup_research_context": {"artifacts": [{
        "artifact_id": "sbc-operating-cases", "path": str(artifact),
        "sha256": sha256(artifact.read_bytes()).hexdigest(), "content_text": artifact.read_text()}]}}
    return reviewed_plan(comparison_context=context, extra_refs=("PRIMARY1", "sbc-operating-cases"))


def context_answer(order):
    row = informed_answer(order)
    row["sources"].append({"source_id": "P1", "kind": "primary", "source_family": "issuer-filing",
        "url": "https://example.test/filing", "published_at": "2026-08-01", "first_seen_at": "2026-10-26",
        "locator": "issued capital table", "excerpt": "Issued capital and awards data.", "content_sha256": "c" * 64})
    index = order["informed_revision"]["review_receipt"]["context_evidence_index"]
    row["context_evidence_bindings"] = []
    for item in index:
        binding = {key: item[key] for key in ("context_id", "source_ref", "source_content_sha256", "payload_hash")}
        if item["context_kind"] == "FROZEN_EVIDENCE":
            binding["source_id"] = "P1"
            if item["source_content_sha256"] is None:
                row["sources"][-1]["context_payload_hash"] = item["payload_hash"]
        row["context_evidence_bindings"].append(binding)
    return row


def test_broker_inspired_candidate_can_bind_real_primary_and_verified_research_artifacts(tmp_path):
    p = context_plan(tmp_path); order = make_work_order(p, p["requests"][0], {})
    row = context_answer(order)
    result = validate_response(order, row)
    assert result["status"] == "ACCEPTED"
    assert result["response"]["sources"][1]["kind"] == "primary"
    assert result["broker_source_policy"]["context_evidence"][0]["source_layer"] == "realized_or_filing"
    assert result["broker_source_policy"]["context_evidence"][1]["source_layer"] == "UNKNOWN"


@pytest.mark.parametrize("mutation", [
    lambda r: r["sources"][1].update(content_sha256="d" * 64),
    lambda r: r["sources"][1].update(url="https://example.test/other"),
    lambda r: r["sources"][1].update(source_family="INVENTED_INDEPENDENT_FAMILY"),
    lambda r: r["context_evidence_bindings"][0].update(payload_hash="d" * 64),
    lambda r: r["context_evidence_bindings"][0].pop("source_id"),
    lambda r: r["context_evidence_bindings"][1].update(context_id="unknown-artifact"),
    lambda r: r["context_evidence_bindings"][1].update(source_content_sha256="d" * 64),
    lambda r: r["context_evidence_bindings"].pop(),
    lambda r: r.pop("context_evidence_bindings"),
])
def test_context_references_require_exact_primary_and_artifact_bindings(tmp_path, mutation):
    p = context_plan(tmp_path); order = make_work_order(p, p["requests"][0], {})
    row = context_answer(order); mutation(row)
    with pytest.raises(ResearchCampaignError):
        validate_response(order, row)


def test_artifact_bytes_are_rechecked_not_just_self_asserted_hash(tmp_path):
    p = context_plan(tmp_path); order = make_work_order(p, p["requests"][0], {})
    row = context_answer(order)
    (tmp_path / "operating_cases.json").write_text('{"economic_fcff": "999999"}')
    with pytest.raises(ResearchCampaignError, match="artifact bytes"):
        validate_response(order, row)


def test_unknown_frozen_document_hash_uses_payload_binding_without_fabricated_document_hash(tmp_path):
    p = context_plan(tmp_path, document_hash=False); order = make_work_order(p, p["requests"][0], {})
    row = context_answer(order)
    assert row["context_evidence_bindings"][0]["source_content_sha256"] is None
    assert validate_response(order, row)["status"] == "ACCEPTED"
    row["sources"][1].pop("context_payload_hash")
    with pytest.raises(ResearchCampaignError, match="payload binding"):
        validate_response(order, row)


def test_context_analyst_underwriting_cannot_be_promoted_to_primary(tmp_path):
    p = context_plan(tmp_path, source_layer="analyst_underwriting"); order = make_work_order(p, p["requests"][0], {})
    with pytest.raises(ResearchCampaignError, match="promoted"):
        validate_response(order, context_answer(order))


def test_broker_family_binding_preserves_shared_source_and_rejects_invented_independence():
    p = reviewed_plan(); order = make_work_order(p, p["requests"][0], {})
    row = informed_answer(order)
    good = validate_response(order, row)
    assert good["broker_source_policy"]["dispositions"][0]["underlying_source_families"] == ["capacity-study"]
    row["sources"][0]["source_family"] = "INVENTED_INDEPENDENT_FAMILY"
    with pytest.raises(ResearchCampaignError, match="source family"):
        validate_response(order, row)


def original_fact_plan():
    p = reviewed_plan()
    p["requests"][0].update(metric="issued_shares", unit="shares")
    p["requests"][0].pop("inference_basis")
    return p


def original_fact_answer(order, *, calculated=False):
    row = answer(order)
    row.update(kind="calculated" if calculated else "observed", unit="shares", value="1234", bounds={"low": "1234", "high": "1234"})
    row["sources"][0].update(kind="primary", url="https://example.test/filing", content_sha256="c" * 64,
        source_family="issuer-filing", claim_type="realized_fact", subject_scope="target", subject_id="T")
    if calculated:
        row["calculation"] = {"operation": "sum", "inputs": [
            {"value": "1000", "unit": "shares", "source_ids": ["S1"]},
            {"value": "234", "unit": "shares", "source_ids": ["S1"]}]}
    return row


@pytest.mark.parametrize("calculated", [False, True])
def test_informed_original_primary_facts_keep_observed_or_calculated_kind(calculated):
    p = original_fact_plan()
    result = run_campaign(p, responder=lambda order: original_fact_answer(order, calculated=calculated))
    assert result["status"] == "READY_FOR_COMPILATION"
    out = result["outputs"]["utilization"]
    assert out["response"]["kind"] == ("calculated" if calculated else "observed")
    assert out["broker_source_policy"]["new_run_id"] == "INFORMED-1"
    assert out["broker_source_policy"]["dispositions"][0]["disposition"] == "ORIGINAL_PRIMARY_FACT_RESEARCH"
    merged = merge_underwriting({"target_id": "T", "as_of": p["as_of"], "declarations": {}}, result)
    assert merged["declarations"]["issued_shares"]["value"] == "1234"


@pytest.mark.parametrize("mutation", [
    lambda r: r["sources"][0].update(claim_type="BROKER_FORECAST"),
    lambda r: r["sources"][0].update(kind="broker_estimate"),
    lambda r: r["sources"][0].update(url="https://example.test/report"),
    lambda r: r["sources"][0].update(content_sha256="a" * 64),
    lambda r: r.update(kind="inferred"),
])
def test_original_fact_route_cannot_launder_broker_forecast_or_unreviewed_assumption(mutation):
    p = original_fact_plan(); order = make_work_order(p, p["requests"][0], {})
    row = original_fact_answer(order); mutation(row)
    with pytest.raises(ResearchCampaignError):
        validate_response(order, row)


def test_unknown_broker_family_remains_unknown_not_a_new_independent_family():
    from valuation_engine.broker_judgment import judgment_hash
    p = reviewed_plan()
    receipt = p["informed_revision"]["review_receipt"]
    receipt["decisions"][0]["underlying_source_families"] = ()
    receipt["source_families"] = ()
    receipt["receipt_hash"] = judgment_hash({k: v for k, v in receipt.items() if k != "receipt_hash"})
    order = make_work_order(p, p["requests"][0], {})
    row = informed_answer(order)
    with pytest.raises(ResearchCampaignError, match="UNKNOWN"):
        validate_response(order, row)
    row["sources"][0]["source_family"] = "UNKNOWN"
    result = validate_response(order, row)
    assert result["source_family_count"] == 0
    assert result["broker_source_policy"]["dispositions"][0]["underlying_source_families"] == []
