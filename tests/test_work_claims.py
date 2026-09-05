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
    ForeignClaim,
    GuardedArea,
    WorkClaim,
    WorkClaimError,
    WorkClaimRegistry,
    changed_paths_from_diff,
    check_against_open_requests,
    check_changed_paths,
    claim_for,
    load_work_claim_registry,
    validate_claim_path,
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


def test_a_rename_out_of_a_guarded_area_is_still_a_change_to_it():
    """Moving a run directory out from under the guard is the change most worth
    catching, and only the destination shows in --name-only output."""
    paths = changed_paths_from_diff(
        "R100\truns/kisco-104700/run.yaml\tdocs/run.yaml\nM\tdocs/README.md\n"
    )
    assert paths == ("runs/kisco-104700/run.yaml", "docs/run.yaml", "docs/README.md")

    registry = _registry()
    violations = check_changed_paths(registry, paths, pull_request=172)
    assert [item.path for item in violations] == ["runs/kisco-104700/run.yaml"]


def test_a_copy_reports_both_sides_too():
    assert changed_paths_from_diff(
        "C75\truns/kisco-104700/run.yaml\truns/new/run.yaml\n"
    ) == ("runs/kisco-104700/run.yaml", "runs/new/run.yaml")


def test_plain_status_lines_still_read():
    assert changed_paths_from_diff("M\tsrc/a.py\nA\tdocs/b.md\nD\tdocs/c.md\n") == (
        "src/a.py",
        "docs/b.md",
        "docs/c.md",
    )


@pytest.mark.parametrize(
    "pattern",
    ("runs/*/raw/**", "runs/?oo/**", "config/kr_*.yaml", "runs/[ab]/**"),
)
def test_a_pattern_whose_overlap_cannot_be_decided_is_refused(pattern):
    """runs/*/raw/** and runs/foo/** both cover runs/foo/raw/data.json while
    neither matches the other's text; a registry that accepted both would hand
    one write set to two owners."""
    with pytest.raises(WorkClaimError, match="not decidable"):
        validate_claim_path(pattern)


def test_a_nested_subtree_overlap_is_caught():
    with pytest.raises(WorkClaimError, match="one of them has to go first"):
        _registry(_claim(170, "runs/**"), _claim(171, "runs/koreazinc-010130/**"))


def test_an_exact_path_inside_another_claims_subtree_is_caught():
    with pytest.raises(WorkClaimError, match="one of them has to go first"):
        _registry(
            _claim(170, "runs/koreazinc-010130/**"),
            _claim(171, "runs/koreazinc-010130/run.yaml"),
        )


def test_sibling_subtrees_do_not_overlap():
    registry = _registry(
        _claim(170, "runs/koreazinc-010130/**"), _claim(171, "runs/kisco-104700/**")
    )
    assert len(registry.claims) == 2


def test_a_prefix_that_is_not_a_path_boundary_does_not_overlap():
    """runs/kisco-104700 is not inside runs/kisco, despite the string prefix."""
    registry = _registry(_claim(170, "runs/kisco/**"), _claim(171, "runs/kisco-104700/**"))
    assert len(registry.claims) == 2


def test_a_claim_in_another_open_request_is_seen_before_either_merges():
    """Each request's CI reads its own checkout, so without this the collision
    would only surface when the second one merged."""
    foreign = (
        ForeignClaim(
            pull_request=173,
            claim=WorkClaim(
                claim_id="koreazinc-second-run",
                owner="codex",
                pull_request=173,
                paths=("runs/koreazinc-010130/**",),
                status="active",
            ),
        ),
    )
    violations = check_against_open_requests(
        ("runs/koreazinc-010130/run.yaml",), foreign, pull_request=172
    )
    assert len(violations) == 1
    assert "#173" in violations[0].describe()
    assert "codex" in violations[0].describe()


def test_a_request_does_not_collide_with_its_own_claim():
    foreign = (
        ForeignClaim(
            pull_request=172,
            claim=WorkClaim("mine", "claude", 172, ("runs/**",), "active"),
        ),
    )
    assert check_against_open_requests(
        ("runs/kisco-104700/run.yaml",), foreign, pull_request=172
    ) == ()


def test_a_merged_claim_in_another_request_does_not_block():
    foreign = (
        ForeignClaim(
            pull_request=173,
            claim=WorkClaim("old", "codex", 173, ("runs/**",), "merged"),
        ),
    )
    assert check_against_open_requests(
        ("runs/kisco-104700/run.yaml",), foreign, pull_request=172
    ) == ()


def test_a_contents_response_yields_only_the_active_claims():
    """The cross-request read is the one part that needs the network, so its
    parsing is covered here: a check that silently reads nothing would be worse
    than no check at all."""
    import base64
    import importlib.util
    from pathlib import Path as _Path

    spec = importlib.util.spec_from_file_location(
        "collect_open_pull_request_claims",
        _Path(__file__).resolve().parents[1]
        / "scripts"
        / "collect_open_pull_request_claims.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    registry = """
version: 1
claims:
  - claim_id: live
    owner: codex
    pull_request: 173
    status: active
    paths: ["runs/koreazinc-010130/**"]
  - claim_id: done
    owner: codex
    pull_request: 168
    status: merged
    paths: ["src/valuation_engine/segment_note.py"]
"""
    blob = {"content": base64.b64encode(registry.encode("utf-8")).decode("ascii")}
    rows = module.active_claims_from_contents(blob)
    assert [row["claim_id"] for row in rows] == ["live"]

    assert module.active_claims_from_contents({}) == []
    assert module.active_claims_from_contents(None) == []
    empty = {"content": base64.b64encode(b"version: 1\n").decode("ascii")}
    assert module.active_claims_from_contents(empty) == []
