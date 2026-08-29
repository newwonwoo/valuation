"""The generic LLM staff: judgment through the transport, authority never.

These tests run the officers through the same ``run_*`` wrappers the live
runtime uses, so every assertion about containment is about the real path:
proposal scope, ledger-checked citations, strict parsing, bounded repair.
"""

from __future__ import annotations

import json

import pytest

from valuation_engine.generic_llm_staff import (
    GenericBridgeAnalyst,
    GenericIntelligenceOfficer,
    GenericRedTeamOfficer,
    ProposalParseError,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.llm_staff import (
    LLMStaffContext,
    run_bridge_analyst,
    run_intelligence_officer,
    run_red_team,
)
from valuation_engine.llm_transport import (
    ROLE_BRIDGE,
    ROLE_INTELLIGENCE,
    ROLE_RED_TEAM,
    ScriptedTransport,
    TransportError,
)
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.runtime_authority import current_actor, RuntimeActor


TARGET = "KR:DART:00999901"


def _record(evidence_id: str, metric: str, value: float, unit: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id, target=TARGET, metric=metric, value=value, unit=unit,
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-08-27", observed_date="2026-08-27",
        source_name="dart", source_ref="https://dart.fss.or.kr/x",
        source_grade="A", confidence=0.9, segment="core",
    )


LEDGER = EvidenceLedger(
    (
        _record("E:REV", "revenue", 5200.0, "KRW_billion"),
        _record("E:EBITDA", "normalized_ebitda", 940.0, "KRW_billion"),
        _record("E:SHARES", "diluted_shares", 30000000.0, "shares"),
    )
)


def _context(**overrides) -> LLMStaffContext:
    defaults = dict(company="한빛중전기", ticker="900990", ledger=LEDGER)
    defaults.update(overrides)
    return LLMStaffContext(**defaults)


def _hypothesis_payload(**overrides) -> dict:
    payload = {
        "rationale": "Filed revenue and EBITDA support a normalized-earnings view.",
        "hypotheses": [
            {
                "id": "H1",
                "statement": "Normalized EBITDA of 940 KRW bn is sustainable",
                "causal_chain": ["filed EBITDA", "normalized earnings", "enterprise value"],
                "supporting_evidence_ids": ["E:EBITDA"],
                "contradicting_evidence_ids": [],
                "kill_conditions": ["EBITDA falls below 700 KRW bn for two quarters"],
                "next_checks": ["next quarterly filing"],
            }
        ],
        "requested_evidence": [],
        "scanner_reinforcements": [],
        "context_strength_linkage": {
            "not_applicable_reason": (
                "No non-obvious environment-to-strength connection is observable "
                "in the collected evidence for this run."
            )
        },
    }
    payload.update(overrides)
    return payload


def _intelligence(*responses: dict) -> GenericIntelligenceOfficer:
    return GenericIntelligenceOfficer(
        transport=ScriptedTransport(
            {ROLE_INTELLIGENCE: tuple(json.dumps(item) for item in responses)}
        )
    )


# --------------------------------------------------------------- intelligence


def test_a_valid_response_becomes_a_typed_validated_proposal():
    proposal = run_intelligence_officer(_context(), _intelligence(_hypothesis_payload()))
    assert proposal.hypotheses[0].id == "H1"
    assert proposal.hypotheses[0].supporting_evidence_ids == ("E:EBITDA",)
    assert proposal.context_strength_linkage_decision is not None


def test_the_officer_runs_under_llm_proposal_scope():
    seen: dict[str, RuntimeActor] = {}

    class Probe:
        def complete(self, *, role: str, prompt: str) -> str:
            seen["actor"] = current_actor()
            return json.dumps(_hypothesis_payload())

    run_intelligence_officer(_context(), GenericIntelligenceOfficer(transport=Probe()))
    assert seen["actor"] is RuntimeActor.LLM


def test_an_unknown_evidence_citation_is_rejected():
    bad = _hypothesis_payload()
    bad["hypotheses"][0]["supporting_evidence_ids"] = ["E:FABRICATED"]
    with pytest.raises(ProposalParseError, match="unknown evidence_id"):
        run_intelligence_officer(
            _context(), _intelligence(bad, bad)  # both attempts fail
        )


def test_the_model_cannot_set_probability_or_calibration():
    bad = _hypothesis_payload()
    bad["hypotheses"][0]["probability"] = 0.95
    with pytest.raises(ProposalParseError, match="unknown keys: probability"):
        run_intelligence_officer(_context(), _intelligence(bad, bad))


def test_non_json_gets_one_repair_attempt_then_fails_closed():
    transport = ScriptedTransport(
        {
            ROLE_INTELLIGENCE: (
                "I think the company is great!",
                json.dumps(_hypothesis_payload()),
            )
        }
    )
    proposal = run_intelligence_officer(
        _context(), GenericIntelligenceOfficer(transport=transport)
    )
    assert proposal.hypotheses
    # The repair prompt carried the rejection reason back to the model.
    assert "rejected" in transport.calls[1][1]


def test_two_bad_responses_fail_closed():
    transport = ScriptedTransport({ROLE_INTELLIGENCE: ("nope", "still nope")})
    with pytest.raises(ProposalParseError, match="failed after 2 attempts"):
        run_intelligence_officer(
            _context(), GenericIntelligenceOfficer(transport=transport)
        )


def test_market_comparison_evidence_blocks_the_context_before_any_model_call():
    market = EvidenceLedger(
        (
            *LEDGER.active(),
            EvidenceRecord(
                id="E:PX", target=TARGET, metric="market_price", value=100.0,
                unit="KRW", source_layer=EvidenceSourceLayer.MARKET_COMPARISON,
                effective_date="2026-08-27", observed_date="2026-08-27",
                source_name="krx", source_ref="https://example.test",
                source_grade="A", confidence=0.9, segment="core",
            ),
        )
    )
    with pytest.raises(PermissionError, match="market-comparison"):
        run_red_team(
            _context(ledger=market),
            (),
            GenericRedTeamOfficer(transport=ScriptedTransport({})),
        )


# ------------------------------------------------------------------- red team


def _red_team_payload(**overrides) -> dict:
    payload = {
        "counter_thesis": "Normalized EBITDA may reflect a cyclical peak, not a run-rate.",
        "issues": [
            {"id": "R1", "description": "single-year EBITDA basis", "blocking": False,
             "requested_evidence": ["three-year EBITDA history"]}
        ],
        "requested_evidence": [],
    }
    payload.update(overrides)
    return payload


def test_red_team_parses_and_validates():
    proposal = run_red_team(
        _context(),
        (),
        GenericRedTeamOfficer(
            transport=ScriptedTransport(
                {ROLE_RED_TEAM: (json.dumps(_red_team_payload()),)}
            )
        ),
    )
    assert proposal.counter_thesis
    assert proposal.issues[0].id == "R1"
    assert not proposal.issues[0].blocking


def test_red_team_blocking_flag_must_be_boolean():
    bad = _red_team_payload()
    bad["issues"][0]["blocking"] = "yes"
    with pytest.raises(ProposalParseError, match="must be a boolean"):
        run_red_team(
            _context(), (),
            GenericRedTeamOfficer(
                transport=ScriptedTransport(
                    {ROLE_RED_TEAM: (json.dumps(bad), json.dumps(bad))}
                )
            ),
        )


# --------------------------------------------------------------------- bridge


def _draft(key: str, scenario: str, evidence: str, value: float, unit: str) -> dict:
    return {
        "assumption_key": key, "scenario_id": scenario,
        "hypothesis_id": "H1", "evidence_ids": [evidence],
        "affected_variable": "margin", "direction": "unchanged",
        "value": value, "unit": unit, "canonical_unit": unit,
        "transform_id": "identity_observation",
        "rationale": "filed value carried through unchanged",
        "confidence": 0.7,
        "kill_condition": "restatement of the filed figure",
        "verification_event": "next quarterly filing",
        "economic_path_id": f"path:core:{key}",
    }


def _bridge_payload(drafts: list[dict]) -> dict:
    return {"rationale": "Evidence-backed pass-through of filed values.", "drafts": drafts}


HYPOTHESES = ()


def _hypotheses():
    proposal = run_intelligence_officer(_context(), _intelligence(_hypothesis_payload()))
    return proposal.hypotheses


def test_bridge_covers_the_declared_grid_or_fails():
    analyst = GenericBridgeAnalyst(
        transport=ScriptedTransport(
            {
                ROLE_BRIDGE: (
                    json.dumps(
                        _bridge_payload(
                            [_draft("normalized_ebitda", "Base", "E:EBITDA", 940.0, "KRW_billion")]
                        )
                    ),
                ) * 2
            }
        ),
        scenario_ids=("Base",),
        required_keys=("normalized_ebitda", "diluted_shares"),
    )
    with pytest.raises(ProposalParseError, match="missing required .* cells"):
        run_bridge_analyst(_context(), _hypotheses(), _red_team_proposal(), analyst)


def _red_team_proposal():
    return run_red_team(
        _context(), (),
        GenericRedTeamOfficer(
            transport=ScriptedTransport({ROLE_RED_TEAM: (json.dumps(_red_team_payload()),)})
        ),
    )


def test_a_complete_grid_becomes_typed_bridge_drafts():
    drafts = [
        _draft("normalized_ebitda", "Base", "E:EBITDA", 940.0, "KRW_billion"),
        _draft("diluted_shares", "Base", "E:SHARES", 30000000.0, "shares"),
    ]
    analyst = GenericBridgeAnalyst(
        transport=ScriptedTransport({ROLE_BRIDGE: (json.dumps(_bridge_payload(drafts)),)}),
        scenario_ids=("Base",),
        required_keys=("normalized_ebitda", "diluted_shares"),
    )
    bundle = run_bridge_analyst(_context(), _hypotheses(), _red_team_proposal(), analyst)
    assert len(bundle.drafts) == 2
    ebitda = next(d for d in bundle.drafts if d.assumption_key == "normalized_ebitda")
    assert ebitda.bridge.new_value == 940.0
    assert ebitda.transform_id == "identity_observation"


def test_an_unregistered_transform_is_rejected():
    draft = _draft("normalized_ebitda", "Base", "E:EBITDA", 940.0, "KRW_billion")
    draft["transform_id"] = "llm_invented_transform"
    payload = json.dumps(_bridge_payload([draft]))
    analyst = GenericBridgeAnalyst(
        transport=ScriptedTransport({ROLE_BRIDGE: (payload, payload)}),
        scenario_ids=("Base",),
        required_keys=("normalized_ebitda",),
    )
    with pytest.raises(ProposalParseError, match="unregistered transform"):
        run_bridge_analyst(_context(), _hypotheses(), _red_team_proposal(), analyst)


def test_an_invented_bridge_value_survives_parsing_and_dies_in_the_compiler():
    """The analyst layer checks form; the compiler re-derives the number.

    999 is not the cited Evidence value, so the deterministic compiler must
    reject it with PROPOSAL_RECALC_MISMATCH — the containment boundary working
    end to end on generic-staff output.
    """
    from valuation_engine.assumption_compiler import compile_assumptions
    from valuation_engine.llm_staff import materialize_bridge_bundle

    draft = _draft("normalized_ebitda", "Base", "E:EBITDA", 999.0, "KRW_billion")
    analyst = GenericBridgeAnalyst(
        transport=ScriptedTransport(
            {ROLE_BRIDGE: (json.dumps(_bridge_payload([draft])),)}
        ),
        scenario_ids=("Base",),
        required_keys=("normalized_ebitda",),
    )
    hypotheses = _hypotheses()
    bundle = run_bridge_analyst(_context(), hypotheses, _red_team_proposal(), analyst)
    bridges, specs, input_map = materialize_bridge_bundle(bundle)
    result = compile_assumptions(
        target_id=TARGET, ledger=LEDGER, hypotheses=hypotheses,
        bridges=bridges, specs=specs, bridge_input_map=input_map,
    )
    assert not result.passed
    assert any(f.code == "PROPOSAL_RECALC_MISMATCH" for f in result.findings)


def test_an_honest_bridge_value_compiles():
    from valuation_engine.assumption_compiler import compile_assumptions
    from valuation_engine.llm_staff import materialize_bridge_bundle

    draft = _draft("normalized_ebitda", "Base", "E:EBITDA", 940.0, "KRW_billion")
    analyst = GenericBridgeAnalyst(
        transport=ScriptedTransport(
            {ROLE_BRIDGE: (json.dumps(_bridge_payload([draft])),)}
        ),
        scenario_ids=("Base",),
        required_keys=("normalized_ebitda",),
    )
    hypotheses = _hypotheses()
    bundle = run_bridge_analyst(_context(), hypotheses, _red_team_proposal(), analyst)
    bridges, specs, input_map = materialize_bridge_bundle(bundle)
    result = compile_assumptions(
        target_id=TARGET, ledger=LEDGER, hypotheses=hypotheses,
        bridges=bridges, specs=specs, bridge_input_map=input_map,
    )
    assert result.passed
    compiled = result.assumption_set.assumptions[0]
    assert compiled.key == "normalized_ebitda"
    assert compiled.evidence_ids == ("E:EBITDA",)


# ------------------------------------------------------------------ transport


def test_an_exhausted_script_is_an_error_not_an_empty_answer():
    transport = ScriptedTransport({ROLE_INTELLIGENCE: ()})
    with pytest.raises(TransportError, match="no response"):
        transport.complete(role=ROLE_INTELLIGENCE, prompt="x")


def test_the_prompt_carries_the_evidence_table():
    transport = ScriptedTransport(
        {ROLE_INTELLIGENCE: (json.dumps(_hypothesis_payload()),)}
    )
    run_intelligence_officer(_context(), GenericIntelligenceOfficer(transport=transport))
    prompt = transport.calls[0][1]
    assert "E:EBITDA" in prompt and "normalized_ebitda" in prompt
    assert "940.0" in prompt
