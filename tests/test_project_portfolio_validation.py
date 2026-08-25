from scripts.validate_project_portfolio import validate_local, validate_pr_snapshot


def _portfolio(*items):
    return {
        "legacy_dependency_refs": ["LEGACY-DONE"],
        "work_items": list(items),
    }


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


def test_local_validation_accepts_known_legacy_dependency_reference():
    errors = validate_local(_portfolio(_item("NEXT", dependencies=["LEGACY-DONE"])))
    assert errors == []


def test_local_validation_rejects_unknown_dependency_and_cycle():
    portfolio = _portfolio(
        _item("A", dependencies=["B"]),
        _item("B", dependencies=["A", "MISSING"]),
    )
    errors = validate_local(portfolio)
    assert any("unknown dependencies: MISSING" in error for error in errors)
    assert any("dependency cycle" in error for error in errors)


def test_local_validation_rejects_multiple_active_items_for_same_owner():
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


def test_github_snapshot_rejects_merged_pr_left_active():
    item = _item("A", status="ACTIVE", github_pr=10)
    errors = validate_pr_snapshot(item, {"state": "closed", "merged_at": "2026-08-25T12:00:00Z"})
    assert errors == ["A: PR #10 is merged but portfolio status is ACTIVE"]


def test_github_snapshot_accepts_merged_pending_acceptance():
    item = _item("A", status="MERGED_PENDING_ACCEPTANCE", github_pr=10)
    errors = validate_pr_snapshot(item, {"state": "closed", "merged_at": "2026-08-25T12:00:00Z"})
    assert errors == []


def test_github_snapshot_requires_active_pr_to_remain_open():
    item = _item("A", status="ACTIVE", github_pr=10)
    errors = validate_pr_snapshot(item, {"state": "closed", "merged_at": None})
    assert errors == ["A: ACTIVE PR #10 is not open"]
