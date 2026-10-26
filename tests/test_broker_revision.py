"""Persistent revision budget and actual-run/immutable-freeze bindings."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from tests.test_broker_research_policy import reviewed_plan
from valuation_engine.broker_judgment import broker_review_receipt_from_dict, judgment_hash
from valuation_engine.broker_revision import (
    BrokerRevisionError, authorize_revision_attempt, validate_informed_run_lineage,
)
from valuation_engine.control_plane import issue_freeze_token


def freeze(tmp_path, run_id):
    token = issue_freeze_token(run_id=run_id, audit_passed=True, coverage_entries=(), expected_module_ids=(),
        ledger_snapshot_hash="ledger", assumption_set_hash="assumptions", valuation_hash="value",
        audit_hash="audit", industry_snapshot_hash="industry", source_snapshot_hash="sources")
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(asdict(token)))
    return token, path


def bind_review(token, *, source_hash="a" * 64, target="T"):
    raw = reviewed_plan()["informed_revision"]["review_receipt"]
    raw = deepcopy(raw)
    raw.update(run_id=token.run_id, freeze_token_hash=token.token_hash, target_id=target)
    raw["decisions"][0]["reference_content_sha256"] = source_hash
    raw["receipt_hash"] = judgment_hash({key: value for key, value in raw.items() if key != "receipt_hash"})
    return broker_review_receipt_from_dict(raw)


def underwriting(receipt, new_run_id="REVISION-1"):
    return {"target_id": receipt.target_id, "as_of": "2026-10-26", "research_mode": "INFORMED_REVISION",
        "informed_revision": {"schema_version": "broker-informed-revision/v1",
            "baseline_run_id": receipt.run_id, "new_run_id": new_run_id, "review_receipt": receipt.to_dict()},
        "declarations": {}}


def authorize(tmp_path, receipt, parent, root, run="REVISION-1", max_attempts=2, **kwargs):
    return authorize_revision_attempt(receipt, journal_path=tmp_path / "journal.json",
        parent_freeze_path=parent, root_freeze_path=root, new_run_id=run, max_attempts=max_attempts, **kwargs)


def test_blind_default_and_explicit_actual_run_parent_binding(tmp_path):
    token, path = freeze(tmp_path, "BASELINE")
    review = bind_review(token)
    assert validate_informed_run_lineage({"target_id": "T", "as_of": "2026-10-26"}, run_id="BLIND") is None
    assert validate_informed_run_lineage(underwriting(review), run_id="REVISION-1", parent_freeze_path=path) == review
    for run, parent in (("OTHER", path), ("BASELINE", path), ("REVISION-1", None)):
        with pytest.raises(ValueError):
            validate_informed_run_lineage(underwriting(review), run_id=run, parent_freeze_path=parent)
    with pytest.raises(BrokerRevisionError, match="blind"):
        validate_informed_run_lineage({"target_id": "T", "as_of": "2026-10-26"}, run_id="BLIND", parent_freeze_path=path)


def test_invalid_and_wrong_parent_freeze_cannot_authorize(tmp_path):
    token, path = freeze(tmp_path, "BASELINE")
    review = bind_review(token)
    _, other = freeze(tmp_path, "OTHER")
    with pytest.raises(BrokerRevisionError, match="parent freeze"):
        validate_informed_run_lineage(underwriting(review), run_id="REVISION-1", parent_freeze_path=other)
    raw = json.loads(path.read_text()); raw["valuation_hash"] = "tampered"; path.write_text(json.dumps(raw))
    with pytest.raises(PermissionError, match="invalid intrinsic"):
        authorize(tmp_path, review, path, path)
    assert not (tmp_path / "journal.json").exists()


def test_persists_before_return_and_identical_resume_is_idempotent(tmp_path):
    token, path = freeze(tmp_path, "BASELINE"); review = bind_review(token)
    result = authorize(tmp_path, review, path, path)
    assert result["canonical_execution_required"] and not result["valuation_completed"]
    journal = json.loads((tmp_path / "journal.json").read_text())
    assert journal["entries"][0] == json.loads(json.dumps(result["record"]))
    assert result["record"]["attempt"]["attempt_number"] == 1
    replay = authorize(tmp_path, review, path, path)
    assert replay["reused"] and replay["journal_hash"] == result["journal_hash"]
    assert len(json.loads((tmp_path / "journal.json").read_text())["entries"]) == 1
    with pytest.raises(BrokerRevisionError, match="renamed"):
        authorize(tmp_path, review, path, path, run="RENAMED")


def test_rephrasing_or_number_retune_cannot_spend_fresh_attempt(tmp_path):
    token, path = freeze(tmp_path, "BASELINE"); review = bind_review(token)
    authorize(tmp_path, review, path, path)
    raw = review.to_dict(); raw["decisions"][0]["rationale"] = "A rephrased opinion, no substantive new evidence."
    raw["decisions"][0]["assumption_candidate"]["value"] = "0.85"
    raw["receipt_hash"] = judgment_hash({k: v for k, v in raw.items() if k != "receipt_hash"})
    with pytest.raises(ValueError, match="substantive"):
        authorize(tmp_path, broker_review_receipt_from_dict(raw), path, path, run="REVISION-2")
    assert len(json.loads((tmp_path / "journal.json").read_text())["entries"]) == 1


def test_child_revision_uses_same_root_budget_and_never_resets_it(tmp_path):
    root, root_path = freeze(tmp_path, "BASELINE")
    authorize(tmp_path, bind_review(root), root_path, root_path)
    child, child_path = freeze(tmp_path, "REVISION-1")
    second = authorize(tmp_path, bind_review(child, source_hash="c" * 64), child_path, root_path, run="REVISION-2")
    assert second["record"]["attempt"]["attempt_number"] == 2
    assert second["record"]["attempt"]["baseline_identity"] == root.token_hash
    with pytest.raises(BrokerRevisionError, match="root"):
        authorize(tmp_path, bind_review(child, source_hash="d" * 64), child_path, child_path, run="REVISION-3")
    grandchild, grandchild_path = freeze(tmp_path, "REVISION-2")
    with pytest.raises(ValueError, match="budget exhausted"):
        authorize(tmp_path, bind_review(grandchild, source_hash="d" * 64), grandchild_path, root_path, run="REVISION-3")
    with pytest.raises(BrokerRevisionError, match="budget"):
        authorize(tmp_path, bind_review(grandchild, source_hash="d" * 64), grandchild_path, root_path, run="REVISION-3", max_attempts=3)


def test_child_without_recorded_parent_relationship_cannot_start_new_journal(tmp_path):
    root, root_path = freeze(tmp_path, "BASELINE")
    child, child_path = freeze(tmp_path, "UNRECORDED-CHILD")
    with pytest.raises(BrokerRevisionError, match="existing root-lineage"):
        authorize(tmp_path, bind_review(child), child_path, root_path)
    authorize(tmp_path, bind_review(root), root_path, root_path)
    with pytest.raises(BrokerRevisionError, match="relationship"):
        authorize(tmp_path, bind_review(child, source_hash="c" * 64), child_path, root_path, run="REVISION-2")


@pytest.mark.parametrize("rehash", [False, True])
def test_journal_tampering_is_rejected_even_if_outer_hash_is_recomputed(tmp_path, rehash):
    token, path = freeze(tmp_path, "BASELINE"); review = bind_review(token)
    authorize(tmp_path, review, path, path)
    journal_path = tmp_path / "journal.json"
    raw = json.loads(journal_path.read_text()); raw["entries"][0]["attempt"]["attempt_number"] = 0
    if rehash:
        entry = raw["entries"][0]
        entry["entry_hash"] = judgment_hash({k: v for k, v in entry.items() if k != "entry_hash"})
        raw["journal_hash"] = judgment_hash({k: v for k, v in raw.items() if k != "journal_hash"})
    journal_path.write_text(json.dumps(raw))
    with pytest.raises(BrokerRevisionError):
        authorize(tmp_path, review, path, path)


def test_concurrent_identical_authorizations_create_one_attempt(tmp_path):
    token, path = freeze(tmp_path, "BASELINE"); review = bind_review(token)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: authorize(tmp_path, review, path, path), range(12)))
    assert sum(not row["reused"] for row in results) == 1
    assert len(json.loads((tmp_path / "journal.json").read_text())["entries"]) == 1


def test_cross_target_and_symlink_journal_are_rejected(tmp_path):
    token, path = freeze(tmp_path, "BASELINE"); review = bind_review(token)
    authorize(tmp_path, review, path, path)
    with pytest.raises(BrokerRevisionError):
        authorize(tmp_path, bind_review(token, target="OTHER"), path, path, run="REVISION-2")
    journal = tmp_path / "journal.json"; saved = tmp_path / "saved.json"
    journal.rename(saved); journal.symlink_to(saved)
    with pytest.raises(BrokerRevisionError, match="symlink"):
        authorize(tmp_path, review, path, path)


def test_one_child_run_cannot_switch_frozen_snapshot_after_first_use(tmp_path):
    root, root_path = freeze(tmp_path, "BASELINE")
    authorize(tmp_path, bind_review(root), root_path, root_path, max_attempts=3)
    child, child_path = freeze(tmp_path, "REVISION-1")
    authorize(tmp_path, bind_review(child, source_hash="c" * 64), child_path, root_path, run="REVISION-2", max_attempts=3)
    changed = issue_freeze_token(run_id="REVISION-1", audit_passed=True, coverage_entries=(), expected_module_ids=(),
        ledger_snapshot_hash="new-ledger", assumption_set_hash="new-assumptions", valuation_hash="new-value",
        audit_hash="new-audit", industry_snapshot_hash="industry", source_snapshot_hash="new-sources")
    child_path.write_text(json.dumps(asdict(changed)))
    with pytest.raises(BrokerRevisionError, match="relationship"):
        authorize(tmp_path, bind_review(changed, source_hash="d" * 64), child_path, root_path, run="REVISION-3", max_attempts=3)
