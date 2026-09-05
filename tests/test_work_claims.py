"""Two agents heading for the same area meet in one short file, not in ten.

The registry is not a lock and does not reserve the repository. Its whole job
is to move a collision earlier: on 2026-09-04 two agents built two different
Korea Zinc runs in the same directory and fixed one evaluator defect twice in
incompatible ways, and git merged most of that text cleanly — the conflict was
in meaning and surfaced only after both sides were finished.
"""

from __future__ import annotations

import pytest

from valuation_engine.work_claims import (
    GuardedArea,
    WorkClaim,
    WorkClaimError,
    WorkClaimRegistry,
    changed_paths_from_diff,
    check_changed_paths,
    claim_for,
    load_work_claim_registry,
)


def _registry(*claims: WorkClaim) -> WorkClaimRegistry:
    registry = WorkClaimRegistry(
        guarded_areas=(
            GuardedArea(paths=("runs/**",), reason="one company's valuation"),
            GuardedArea(
                paths=("config/kr_industry_classification_map.yaml",),
                reason="the economic contracts",
            ),
        ),
        claims=claims,
    )
    registry.validate()
    return registry


def _claim(pull_request: int, *paths: str, status: str = "active") -> WorkClaim:
    return WorkClaim(
        claim_id=f"claim-{pull_request}",
        owner="agent",
        pull_request=pull_request,
        paths=paths,
        status=status,
    )


def test_the_committed_registry_is_coherent():
    registry = load_work_claim_registry()
    assert registry.guarded_areas
    assert all(area.paths and area.reason for area in registry.guarded_areas)


def test_a_change_inside_a_guarded_area_needs_this_requests_claim():
    registry = _registry()
    violations = check_changed_paths(
        registry, ("runs/koreazinc-010130/run.yaml",), pull_request=171
    )
    assert [item.path for item in violations] == ["runs/koreazinc-010130/run.yaml"]
    assert "no claim from this pull request" in violations[0].describe()


def test_a_claim_of_this_request_admits_the_change():
    registry = _registry(_claim(171, "runs/koreazinc-010130/**"))
    assert (
        check_changed_paths(
            registry,
            ("runs/koreazinc-010130/run.yaml", "runs/koreazinc-010130/raw/list.json"),
            pull_request=171,
        )
        == ()
    )


def test_another_requests_claim_names_who_to_talk_to():
    """The exact case that cost half a branch: #170 was already building this
    run directory while another request built its own version of it."""
    registry = _registry(_claim(170, "runs/koreazinc-010130/**"))
    violations = check_changed_paths(
        registry, ("runs/koreazinc-010130/run.yaml",), pull_request=171
    )
    assert len(violations) == 1
    described = violations[0].describe()
    assert "claim-170" in described and "#170" in described
    assert "coordinate" in described


def test_a_path_outside_the_guarded_areas_needs_no_claim():
    registry = _registry()
    assert check_changed_paths(
        registry, ("docs/RUNBOOK_KR_LIVE.md", "src/valuation_engine/report.py"),
        pull_request=171,
    ) == ()


def test_a_directory_claim_covers_the_directory_itself():
    registry = _registry(_claim(171, "runs/**"))
    assert check_changed_paths(registry, ("runs",), pull_request=171) == ()


def test_a_released_claim_stops_holding_its_paths():
    registry = _registry(_claim(170, "runs/**", status="merged"))
    violations = check_changed_paths(
        registry, ("runs/kisco-104700/run.yaml",), pull_request=171
    )
    # Still guarded — this request needs its own claim — but nobody holds it.
    assert violations[0].held_by is None


def test_two_active_claims_may_not_hold_the_same_path():
    with pytest.raises(WorkClaimError, match="one of them has to go first"):
        _registry(_claim(170, "runs/**"), _claim(171, "runs/koreazinc-010130/**"))


def test_one_request_may_hold_several_claims():
    registry = _registry(
        _claim(171, "runs/kisco-104700/**"),
        WorkClaim(
            claim_id="claim-171-contracts",
            owner="agent",
            pull_request=171,
            paths=("config/kr_industry_classification_map.yaml",),
            status="active",
        ),
    )
    assert len(claim_for(registry, 171)) == 2
    assert check_changed_paths(
        registry,
        ("runs/kisco-104700/run.yaml", "config/kr_industry_classification_map.yaml"),
        pull_request=171,
    ) == ()


@pytest.mark.parametrize(
    "field, value, message",
    (
        ("claim_id", "", "requires claim_id"),
        ("owner", "", "requires claim_id"),
        ("status", "wishful", "unknown status"),
        ("pull_request", 0, "requires its pull request"),
    ),
)
def test_an_incoherent_claim_is_refused(field, value, message):
    fields = dict(
        claim_id="claim-1",
        owner="agent",
        pull_request=1,
        paths=("runs/**",),
        status="active",
    )
    fields[field] = value
    with pytest.raises(WorkClaimError, match=message):
        _registry(WorkClaim(**fields))


def test_a_claim_without_paths_is_refused():
    with pytest.raises(WorkClaimError, match="requires paths"):
        _registry(
            WorkClaim(
                claim_id="claim-1",
                owner="agent",
                pull_request=1,
                paths=(),
                status="active",
            )
        )


def test_diff_output_reads_as_changed_paths():
    assert changed_paths_from_diff(
        "runs/kisco-104700/run.yaml\n\n  docs/RUNBOOK_KR_LIVE.md  \n"
    ) == ("runs/kisco-104700/run.yaml", "docs/RUNBOOK_KR_LIVE.md")
