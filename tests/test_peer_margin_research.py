from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, localcontext

import pytest

from valuation_engine.peer_margin_research import (
    PeerMarginResearchError, build_peer_margin_proposal, validate_peer_margin_receipt,
)


def proposal_inputs():
    source = {"source_id": "annual", "url": "https://example.com/annual", "published_at": "2025-03-01",
              "first_seen_at": "2025-03-02", "locator": "segment note p. 42", "content_sha256": "a" * 64}
    peer = {"peer_id": "peer-a", "issuer": "A", "segment": "cloud", "metric": "EBITDA",
            "accounting_basis": "lease-expensed, SBC-expensed", "money_unit": "USD_million",
            "period_start": "2024-01-01", "period_end": "2024-12-31", "revenue": "100",
            "profit": {"low": "20", "base": "30", "high": "40"}, "weight": "1",
            "weight_rationale": "Comparable capacity and revenue mix", "comparability_rationale": "Same contracted compute service",
            "period_comparability_rationale": "Both peers cover the same full calendar year; no annualization needed",
            "source_refs": ["annual"], "normalization_adjustments": []}
    second = deepcopy(peer)
    second.update(peer_id="peer-b", issuer="B", weight="3", profit={"low": "40", "base": "50", "high": "60"})
    adjustment = {"adjustment_id": "early-utilization", "percentage_points": {"low": "-15", "base": "-10", "high": "-5"},
                  "rationale": "Target has lower initial utilization and higher power costs", "source_refs": ["annual"]}
    return {"schema_version": "peer-margin-input/v1", "target": "NewCo", "segment": "cloud", "as_of": "2025-09-01",
            "economic_path_id": "cloud-operating", "metric": "EBITDA", "accounting_basis": peer["accounting_basis"],
            "sources": [source], "peers": [peer, second],
            "years": [{"year": 2026, "rationale": "Initial operating stage", "adjustments": [adjustment]},
                      {"year": 2027, "rationale": "Mature utilization assumption with risk retained", "adjustments": []}]}


def test_weighted_peer_margin_explicit_ramp_recomputes_and_preserves_inputs():
    inputs = proposal_inputs()
    original = deepcopy(inputs)
    result = build_peer_margin_proposal(inputs)
    assert result["pooled_margin"] == {"low": "0.35", "base": "0.45", "high": "0.55"}
    assert result["years"][0] == {"year": 2026, "low": "0.20", "base": "0.35", "high": "0.50", "unit": "ratio"}
    assert result["years"][1]["base"] == "0.45"
    assert inputs == original
    assert validate_peer_margin_receipt(result, 2026, "base", "0.35", economic_path_id="cloud-operating") == result
    inputs["peers"][0]["revenue"] = "99"
    assert result["inputs"] == original


def test_negative_initial_margin_and_explicit_normalization_are_allowed():
    inputs = proposal_inputs()
    inputs["peers"][0]["normalization_adjustments"] = [{"adjustment_id": "one-off", "percentage_points": {"low": "-10", "base": "-10", "high": "-10"}, "rationale": "Remove nonrecurring credit", "source_refs": ["annual"]}]
    inputs["years"][0]["adjustments"][0]["percentage_points"] = {"low": "-100", "base": "-70", "high": "-60"}
    result = build_peer_margin_proposal(inputs)
    assert Decimal(result["years"][0]["base"]) == Decimal("-0.275")


@pytest.mark.parametrize("field,value", [("metric", "EBIT"), ("accounting_basis", "lease-capitalized"),
    ("revenue", "0"), ("revenue", "NaN"), ("revenue", 100.0), ("weight", "0"), ("weight", "-1"),
    ("weight", "Infinity"), ("source_refs", []), ("source_refs", ["missing"]), ("issuer", "NewCo"),
    ("period_end", "2025-04-01"), ("period_start", "2025-01-01"), ("comparability_rationale", ""),
    ("period_comparability_rationale", ""), ("money_unit", "ratio"), ("money_unit", "invented_currency")])
def test_rejects_noncomparable_or_invalid_peer(field, value):
    inputs = proposal_inputs()
    inputs["peers"][0][field] = value
    with pytest.raises(PeerMarginResearchError):
        build_peer_margin_proposal(inputs)


@pytest.mark.parametrize("field,value", [("url", "file:///tmp/annual"), ("url", "https://user:secret@example.com/x"),
    ("published_at", "2025-10-01"), ("first_seen_at", "2025-10-01"), ("first_seen_at", "2025-02-01"),
    ("first_seen_at", "2025-03-02T12:00:00"), ("content_sha256", "fake"), ("locator", "")])
def test_source_provenance_and_lookahead(field, value):
    inputs = proposal_inputs()
    inputs["sources"][0][field] = value
    with pytest.raises(PeerMarginResearchError):
        build_peer_margin_proposal(inputs)


@pytest.mark.parametrize("where", ["inputs", "output", "hash", "year", "case", "value", "unit", "path"])
def test_consumer_receipt_rejects_tampering_and_binding_mismatch(where):
    result = build_peer_margin_proposal(proposal_inputs())
    kwargs = {"year": 2026, "case": "base", "value": "0.35", "unit": "ratio", "economic_path_id": "cloud-operating"}
    if where == "inputs":
        result["inputs"]["peers"][0]["revenue"] = "200"
    elif where == "output":
        result["years"][0]["base"] = "0.99"
    elif where == "hash":
        result["input_sha256"] = "b" * 64
    else:
        key = "economic_path_id" if where == "path" else where
        kwargs[key] = {"year": 2028, "case": "bull", "value": "0.36", "unit": "percent", "path": "other"}[where]
    with pytest.raises(PeerMarginResearchError):
        validate_peer_margin_receipt(result, **kwargs)


@pytest.mark.parametrize("mutation", ["empty", "bounds", "over100", "duplicate_peer", "duplicate_source", "duplicate_adjustment", "past_year", "gap_year", "missing_adjustments"])
def test_missing_or_inconsistent_assumptions_rejected(mutation):
    inputs = proposal_inputs()
    if mutation == "empty":
        inputs["peers"] = []
    elif mutation == "bounds":
        inputs["peers"][0]["profit"]["low"] = "50"
    elif mutation == "over100":
        inputs["years"][0]["adjustments"][0]["percentage_points"] = {"low": "100", "base": "100", "high": "100"}
    elif mutation == "duplicate_peer":
        inputs["peers"].append(deepcopy(inputs["peers"][0]))
    elif mutation == "duplicate_source":
        inputs["sources"].append(deepcopy(inputs["sources"][0]))
    elif mutation == "duplicate_adjustment":
        inputs["years"][0]["adjustments"] *= 2
    elif mutation == "past_year":
        inputs["years"][0]["year"] = 2025
    elif mutation == "gap_year":
        inputs["years"][1]["year"] = 2028
    else:
        del inputs["years"][0]["adjustments"]
    with pytest.raises(PeerMarginResearchError):
        build_peer_margin_proposal(inputs)


def test_decimal_context_does_not_change_proposal_or_receipt():
    inputs = proposal_inputs()
    inputs["peers"][1]["weight"] = "2"
    result = build_peer_margin_proposal(inputs)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert build_peer_margin_proposal(inputs) == result
        validate_peer_margin_receipt(result, 2026, "base", result["years"][0]["base"])


def test_alias_cannot_double_weight_same_observation():
    inputs = proposal_inputs()
    duplicate = deepcopy(inputs["peers"][0])
    duplicate.update(peer_id="alias", issuer=" a ", segment="CLOUD")
    inputs["peers"].append(duplicate)
    with pytest.raises(PeerMarginResearchError, match="duplicate issuer"):
        build_peer_margin_proposal(inputs)


@pytest.mark.parametrize("profit_projection", [False, True])
def test_peer_assumption_accepted_by_actual_research_response_contract(profit_projection):
    from valuation_engine.research_campaign import make_work_order, validate_response

    inputs = proposal_inputs()
    receipt = build_peer_margin_proposal(inputs)
    request = {"request_id": "cloud-margin", "metric": "ebitda" if profit_projection else "ebitda_margin",
               "margin_basis": "EBITDA", "forecast_year": 2026, "segment": "cloud", "unit": "USD_million" if profit_projection else "ratio",
               "question": "Infer target operating profitability", "economic_path_id": "cloud-operating"}
    plan = {"target_id": "NewCo", "as_of": inputs["as_of"], "model_version": "test", "policy_version": "test"}
    order = make_work_order(plan, request, {})
    source = {**inputs["sources"][0], "source_family": "peer-filings", "kind": "primary", "excerpt": "Illustrative peer operating results fixture."}
    factor = Decimal(200) if profit_projection else Decimal(1)
    selected = receipt["years"][0]
    response = {"schema_version": "research-response/v1", "request_id": request["request_id"], "request_hash": order["request_hash"],
                "status": "answered", "kind": "inferred", "unit": request["unit"], "value": str(Decimal(selected["base"]) * factor),
                "bounds": {case: str(Decimal(selected[case]) * factor) for case in ("low", "high")},
                "rationale": "Peer operating margins adjusted for initial utilization and cost differences.",
                "invalidation_condition": "Capacity utilization remains below the assumed ramp.",
                "uncertainty": "Target may fail to attain comparable operating efficiency.",
                "counterevidence": [], "counterevidence_search": "Checked peer operating losses and target ramp setbacks.",
                "sources": [source], "peer_margin": {"receipt": receipt, "year": 2026, "case": "base"}}
    if profit_projection:
        response["peer_margin"]["revenue"] = {"value": "200", "unit": "USD_million", "source_ids": ["annual"], "year": 2026,
                                               "rationale": "Explicit illustrative target sales assumption for the annual operating case."}
    validate_response(order, response)
    response["value"] = str(Decimal(response["value"]) + Decimal("0.01"))
    with pytest.raises(ValueError, match="reproduce"):
        validate_response(order, response)
