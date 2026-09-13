import json
from hashlib import sha256

import pytest

from valuation_engine.staff_transport import StaffTransport, StaffWorkRequired


def test_replay_exhaustion_requests_fresh_answer(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUATION_LLM_TRANSPORT", raising=False)
    (tmp_path / "analyst.json").write_text('{"value": 1}')
    transport = StaffTransport(tmp_path)
    assert json.loads(transport.complete(role="analyst", prompt="initial")) == {"value": 1}
    with pytest.raises(StaffWorkRequired, match="exhausted"):
        transport.complete(role="analyst", prompt="repair: missing source")
    request = next((tmp_path / "requests").glob("*.json"))
    assert json.loads(request.read_text())["prompt"] == "repair: missing source"


def test_assisted_prompt_bound_response_and_stale_rejection(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUATION_LLM_TRANSPORT", raising=False)
    (tmp_path / "analyst.json").write_text('{"value": 99}')
    digest = sha256(b"question").hexdigest()
    responses = tmp_path / "responses"
    responses.mkdir()
    path = responses / f"analyst.{digest}.json"
    envelope = {"schema_version": "staff-response/v1", "role": "analyst",
                "prompt_sha256": digest, "response": {"value": 5}}
    path.write_text(json.dumps(envelope))
    assert json.loads(StaffTransport(tmp_path, mode="assisted").complete(role="analyst", prompt="question"))["value"] == 5
    envelope["prompt_sha256"] = "old"
    path.write_text(json.dumps(envelope))
    with pytest.raises(StaffWorkRequired, match="stale"):
        StaffTransport(tmp_path, mode="assisted").complete(role="analyst", prompt="question")
    with pytest.raises(StaffWorkRequired, match="no staff proposal"):
        StaffTransport(tmp_path, mode="assisted").complete(role="analyst", prompt="new question")


def test_live_repair_receives_actual_prompt_and_audits(tmp_path):
    class Model:
        def __init__(self):
            self.prompts = []
        def complete(self, *, role, prompt):
            self.prompts.append(prompt)
            return '{"fixed":true}'
    model = Model()
    (tmp_path / "analyst.json").write_text('{"stale": true}')
    transport = StaffTransport(tmp_path, mode="live", live_transport=model)
    assert json.loads(transport.complete(role="analyst", prompt="repair error"))["fixed"]
    assert model.prompts == ["repair error"]
    assert len(list((tmp_path / "requests" / "audit").glob("*.response.json"))) == 1


def test_assisted_requires_envelope(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUATION_LLM_TRANSPORT", raising=False)
    digest = sha256(b"question").hexdigest()
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / f"analyst.{digest}.json").write_text('{"value":1}')
    with pytest.raises(StaffWorkRequired, match="envelope"):
        StaffTransport(tmp_path, mode="assisted").complete(role="analyst", prompt="question")
