import json
from dataclasses import replace

import pytest

from valuation_engine.host_judgment import (
    HostJudgmentError, HostJudgmentRequest, JudgmentGap, judgment_hash,
    parse_host_judgment_response,
)


def request():
    return HostJudgmentRequest.build(
        gap=JudgmentGap("METHOD_DECISION_REQUIRED", "method_selection", "KR:DART:12345678",
                        "2026-10-26", "RUN_INPUT", "Choose among supported methods."),
        source_bindings=({"path": "run.yaml", "sha256": "a" * 64},),
        allowed_actions=("SELECT_METHOD", "UNCERTAIN"), response_schema={"proposal": "typed method"},
        input_hash="b" * 64, validator_id="supported-method/v1", write_targets=("run.yaml",))


def response(req):
    return {"schema_version": "host-judgment-response/v1", "request_hash": req.request_hash,
            "target_id": req.gap.target_id, "as_of": req.gap.as_of, "decision": "SELECT_METHOD",
            "proposal": {"method": "registered-only"}, "rationale": "Economic evidence",
            "counterevidence": [], "uncertainty": "Requires the concrete method validator"}


def test_hash_binds_target_sources_inputs_schema_and_allowed_actions():
    req = request()
    for changed in (replace(req, input_hash="c" * 64),
                    replace(req, source_bindings=({"sha256": "d" * 64},)),
                    replace(req, response_schema={"different": True}),
                    replace(req, allowed_actions=("UNCERTAIN",)),
                    replace(req, gap=replace(req.gap, target_id="other"))):
        assert changed.request_hash != req.request_hash
    assert parse_host_judgment_response(json.dumps(response(req)), req)["proposal"]
    assert "not a successful run status" in req.prompt()


@pytest.mark.parametrize("key,value", [("request_hash", "0" * 64), ("target_id", "other"),
                                        ("as_of", "2020-01-01"), ("decision", "PASS")])
def test_stale_wrong_target_date_and_unconditional_pass_rejected(key, value):
    req = request()
    with pytest.raises(HostJudgmentError):
        parse_host_judgment_response({**response(req), key: value}, req)


def test_unknown_audit_authority_fields_and_duplicate_keys_rejected():
    req = request()
    with pytest.raises(HostJudgmentError):
        parse_host_judgment_response({**response(req), "audit_status": "PASS"}, req)
    with pytest.raises(HostJudgmentError, match="duplicate"):
        parse_host_judgment_response('{"decision":"PASS","decision":"UNCERTAIN"}', req)


@pytest.mark.parametrize("cause", ["SOURCE_HASH_MISMATCH", "FORGERY", "SECURITY_FAILURE", "UNAUTHORIZED"])
def test_hard_failures_do_not_gain_discretion(cause):
    req = request()
    with pytest.raises(HostJudgmentError):
        replace(req, gap=replace(req.gap, cause_code=cause)).validate()


def test_no_permission_to_request_audit_pass():
    with pytest.raises(HostJudgmentError):
        replace(request(), allowed_actions=("PASS",)).validate()
