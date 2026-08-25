import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_project_portfolio",
    ROOT / "scripts" / "validate_project_portfolio.py",
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
accepted_pr_numbers = VALIDATOR.accepted_pr_numbers
validate_accepted_pr = VALIDATOR.validate_accepted_pr
validate_local = VALIDATOR.validate_local
validate_work_item_pr = VALIDATOR.validate_work_item_pr


def _item(item_id, status="READY", dependencies=None, github_pr=None, owner="owner"):
    item = {
        "id": item_id,
        "status": status,
        "owner": owner,
        "dependencies": list(dependencies or []),
    }
    if github_pr is not None:
        item["github_pr"] = github_pr
    return item


def _portfolio(*items, milestones=()):
    return {
        "departments": [
            {
                "accepted_milestones": list(milestones),
            }
        ],
        "work_items": list(items),
    }


def _milestone(item_id="DONE", refs=None, evidence=None):
    return {
        "id": item_id,
        "status": "VERIFIED",
        "implementation_refs": list(refs or []),
        "validation_evidence": list(evidence or ["GHA PASS"]),
        "accepted_sha": "abc123",
    }


def test_dependency_may_reference_verified_milestone():
    portfolio = _portfolio(
        _item("NEXT", dependencies=["DONE"]),
        milestones=[_milestone()],
    )
    assert validate_local(portfolio) == []


def test_unknown_dependency_and_cycle_fail_closed():
    portfolio = _portfolio(
        _item("A", dependencies=["B"]),
        _item("B", dependencies=["A", "MISSING"]),
    )
    errors = validate_local(portfolio)
    assert any("unknown dependencies: MISSING" in error for error in errors)
    assert any("dependency cycle" in error for error in errors)


def test_verified_item_cannot_remain_in_execution_queue():
    errors = validate_local(_portfolio(_item("DONE"), milestones=[_milestone()]))
    assert errors == ["work items already accepted as VERIFIED: DONE"]


def test_multiple_active_items_for_same_owner_fail_closed():
    errors = validate_local(
        _portfolio(
            _item("A", status="ACTIVE", owner="same"),
            _item("B", status="ACTIVE", owner="same"),
        )
    )
    assert any("more than one ACTIVE work item" in error for error in errors)


def test_merged_pending_acceptance_requires_pr_link():
    errors = validate_local(_portfolio(_item("A", status="MERGED_PENDING_ACCEPTANCE")))
    assert any("requires github_pr" in error for error in errors)


def test_linked_merged_pr_cannot_remain_active():
    item = _item("A", status="ACTIVE", github_pr=70)
    errors = validate_work_item_pr(
        item,
        {"state": "closed", "merged_at": "2026-08-25T12:00:00Z"},
    )
    assert errors == ["A: PR #70 is merged but portfolio status is ACTIVE"]


def test_merged_pending_acceptance_matches_merged_pr():
    item = _item("A", status="MERGED_PENDING_ACCEPTANCE", github_pr=70)
    assert validate_work_item_pr(
        item,
        {"state": "closed", "merged_at": "2026-08-25T12:00:00Z"},
    ) == []


def test_accepted_milestone_pr_references_are_deduplicated():
    portfolio = _portfolio(
        milestones=[
            _milestone(refs=["PR 61 risk pack", "PR #62 PER pack"], evidence=["PR 61 merged"]),
        ]
    )
    assert accepted_pr_numbers(portfolio) == (61, 62)


def test_verified_milestone_requires_referenced_pr_to_be_merged():
    assert validate_accepted_pr(61, {"state": "open", "merged_at": None}) == [
        "VERIFIED milestone references PR #61, but that PR is not merged"
    ]
