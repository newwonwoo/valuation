"""Deterministic lineage and persistent budgets for broker-informed revisions.

The caller owns the trusted root freeze and journal location across runs. A
content hash detects inconsistent records, not a malicious replacement of every
trusted artifact. Authorization here spends research budget; it is not a
completed valuation, source-truth certificate or permission to edit a freeze.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, fields
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .broker_judgment import (
    BrokerReviewReceipt, authorize_broker_revision_attempt,
    broker_review_receipt_from_dict, judgment_hash,
)
from .control_plane import IntrinsicFreezeToken, authorize_post_freeze
from .research_campaign import _revision_context


class BrokerRevisionError(ValueError):
    pass


def _token(payload: Mapping) -> IntrinsicFreezeToken:
    if not isinstance(payload, Mapping) or set(payload) - {f.name for f in fields(IntrinsicFreezeToken)}:
        raise BrokerRevisionError("parent freeze must contain only IntrinsicFreezeToken fields")
    try:
        token = IntrinsicFreezeToken(**payload)
    except TypeError as exc:
        raise BrokerRevisionError("incomplete intrinsic freeze token") from exc
    if not all(isinstance(value, str) for value in asdict(token).values()):
        raise BrokerRevisionError("freeze token fields must be strings")
    if not all(getattr(token, key) for key in ("run_id", "ledger_snapshot_hash", "assumption_set_hash",
        "valuation_hash", "audit_hash", "industry_snapshot_hash", "source_snapshot_hash", "token_hash")):
        raise BrokerRevisionError("freeze token requires complete snapshot lineage")
    authorize_post_freeze(token, run_id=token.run_id)
    return token


def _load_token(path) -> IntrinsicFreezeToken:
    if path is None:
        raise BrokerRevisionError("an explicit parent/root freeze artifact is required")
    with Path(path).open(encoding="utf-8") as stream:
        return _token(json.load(stream))


def _bind_parent(receipt: BrokerReviewReceipt, token: IntrinsicFreezeToken) -> None:
    receipt.validate()
    if receipt.run_id != token.run_id or receipt.freeze_token_hash != token.token_hash:
        raise BrokerRevisionError("broker review is not bound to the supplied parent freeze")


def validate_informed_run_lineage(underwriting: Mapping, *, run_id: str,
                                  parent_freeze_path=None) -> BrokerReviewReceipt | None:
    """Bind actual execution identity; blind defaults must have no parent review."""
    receipt = _revision_context(underwriting)
    if receipt is None:
        if parent_freeze_path is not None:
            raise BrokerRevisionError("a blind baseline cannot silently carry a broker parent freeze")
        return None
    token = _load_token(parent_freeze_path)
    _bind_parent(receipt, token)
    new_run = underwriting["informed_revision"]["new_run_id"]
    if not isinstance(run_id, str) or not run_id or run_id != new_run or run_id == token.run_id:
        raise BrokerRevisionError("actual canonical run differs from the authorized new revision run")
    return receipt


def _unsigned(payload: Mapping, hash_key: str) -> dict:
    return {key: value for key, value in payload.items() if key != hash_key}


def _entry(receipt: BrokerReviewReceipt, token: IntrinsicFreezeToken,
           new_run_id: str, attempt: Mapping) -> dict:
    result = {"receipt": receipt.to_dict(), "parent_freeze": asdict(token),
              "new_run_id": new_run_id, "attempt": dict(attempt)}
    result["entry_hash"] = judgment_hash(result)
    return result


def _parent_known(token: IntrinsicFreezeToken, root: IntrinsicFreezeToken, entries: list) -> bool:
    if token.run_id == root.run_id:
        return token.token_hash == root.token_hash
    observed = [item["parent_freeze"] for item in entries if item["parent_freeze"]["run_id"] == token.run_id]
    if observed and any(item["token_hash"] != token.token_hash for item in observed):
        return False
    return any(item["new_run_id"] == token.run_id
               and item["attempt"]["status"] == "AUTHORIZED_RESEARCH_ATTEMPT" for item in entries)


def _validate_journal(payload: Mapping, root: IntrinsicFreezeToken, max_attempts: int, target_id: str) -> list:
    expected = {"schema_version", "target_id", "root_run_id", "root_freeze", "baseline_identity", "max_attempts", "entries", "journal_hash"}
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise BrokerRevisionError("invalid broker revision journal schema")
    if payload["journal_hash"] != judgment_hash(_unsigned(payload, "journal_hash")):
        raise BrokerRevisionError("broker revision journal content hash mismatch")
    if (payload["schema_version"] != "broker-revision-journal/v1" or payload["target_id"] != target_id
            or payload["root_run_id"] != root.run_id
            or payload["baseline_identity"] != root.token_hash
            or asdict(_token(payload["root_freeze"])) != asdict(root)):
        raise BrokerRevisionError("journal cannot be reset to a child or different root freeze")
    if payload["max_attempts"] != max_attempts or isinstance(payload["max_attempts"], bool):
        raise BrokerRevisionError("persisted lineage retry budget cannot be changed")
    if not isinstance(payload["entries"], list):
        raise BrokerRevisionError("journal entries must be a list")
    checked, receipts, run_ids = [], set(), {root.run_id}
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping) or set(entry) != {"receipt", "parent_freeze", "new_run_id", "attempt", "entry_hash"}:
            raise BrokerRevisionError("invalid broker revision journal entry")
        if entry["entry_hash"] != judgment_hash(_unsigned(entry, "entry_hash")):
            raise BrokerRevisionError("broker revision entry content hash mismatch")
        review = broker_review_receipt_from_dict(entry["receipt"])
        parent = _token(entry["parent_freeze"])
        if review.target_id != target_id:
            raise BrokerRevisionError("revision journal cannot cross target identities")
        _bind_parent(review, parent)
        new_run = entry["new_run_id"]
        if (not isinstance(new_run, str) or not new_run.strip() or new_run in run_ids
                or review.receipt_hash in receipts or not _parent_known(parent, root, checked)):
            raise BrokerRevisionError("journal has duplicate or unproven parent/child lineage")
        expected_attempt = authorize_broker_revision_attempt(review, [x["attempt"] for x in checked],
            baseline_identity=root.token_hash, max_attempts=max_attempts).to_dict()
        if expected_attempt != entry["attempt"]:
            raise BrokerRevisionError("journal attempt does not reproduce its authorization")
        checked.append(dict(entry)); receipts.add(review.receipt_hash); run_ids.add(new_run)
    return checked


@contextmanager
def _locked(journal_path: Path):
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = journal_path.with_name(journal_path.name + ".lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _save(path: Path, payload: dict) -> None:
    payload["journal_hash"] = judgment_hash(_unsigned(payload, "journal_hash"))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def authorize_revision_attempt(receipt: BrokerReviewReceipt, *, journal_path,
                               parent_freeze_path, root_freeze_path, new_run_id: str,
                               max_attempts: int) -> dict:
    """Persist authorization before execution, with one budget for the root lineage.

    root_freeze_path and journal_path must come from trusted orchestrator config,
    not the LLM response. Child revisions require a previously authorized parent
    in this same journal. Exact receipt/run replay is idempotent and does not
    consume another attempt. This function never runs or completes a valuation.
    """
    # Strictly reload even a hand-built dataclass; validation is not truth review.
    receipt = broker_review_receipt_from_dict(receipt.to_dict())
    parent, root = _load_token(parent_freeze_path), _load_token(root_freeze_path)
    _bind_parent(receipt, parent)
    if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1):
        raise BrokerRevisionError("explicit positive max_attempts is required")
    if not isinstance(new_run_id, str) or not new_run_id.strip() or new_run_id in {root.run_id, parent.run_id}:
        raise BrokerRevisionError("revision requires a distinct actual new_run_id")
    path = Path(journal_path)
    with _locked(path):
        if path.is_symlink():
            raise BrokerRevisionError("revision journal must not be a symlink")
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                journal = json.load(stream)
            entries = _validate_journal(journal, root, max_attempts, receipt.target_id)
        else:
            if parent.token_hash != root.token_hash or parent.run_id != root.run_id:
                raise BrokerRevisionError("child revision requires its existing root-lineage journal")
            entries = []
            journal = {"schema_version": "broker-revision-journal/v1", "target_id": receipt.target_id, "root_run_id": root.run_id,
                "root_freeze": asdict(root), "baseline_identity": root.token_hash,
                "max_attempts": max_attempts, "entries": entries}
        if not _parent_known(parent, root, entries):
            raise BrokerRevisionError("parent freeze has no authorized relationship to this root lineage")
        for entry in entries:
            if entry["receipt"]["receipt_hash"] == receipt.receipt_hash:
                if entry["new_run_id"] != new_run_id or entry["parent_freeze"] != asdict(parent):
                    raise BrokerRevisionError("same receipt cannot authorize a renamed or different-parent run")
                return {"record": entry, "reused": True, "journal_hash": journal["journal_hash"],
                    "journal_path": str(path),
                    "canonical_execution_required": entry["attempt"]["status"] == "AUTHORIZED_RESEARCH_ATTEMPT",
                    "valuation_completed": False}
            if entry["new_run_id"] == new_run_id:
                raise BrokerRevisionError("new run identity already has a different review authorization")
        attempt = authorize_broker_revision_attempt(receipt, [x["attempt"] for x in entries],
            baseline_identity=root.token_hash, max_attempts=max_attempts)
        entry = _entry(receipt, parent, new_run_id, attempt.to_dict())
        journal["entries"] = [*entries, entry]
        _save(path, journal)
        return {"record": entry, "reused": False, "journal_hash": journal["journal_hash"],
            "journal_path": str(path),
            "canonical_execution_required": attempt.status == "AUTHORIZED_RESEARCH_ATTEMPT", "valuation_completed": False}
