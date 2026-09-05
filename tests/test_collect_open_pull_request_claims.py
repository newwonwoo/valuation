"""Network contract tests: partial coverage cannot become an empty success."""
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest


def _module(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def collector():
    return _module("collect_open_pull_request_claims")


def _blob(number):
    # Other open branches inherit this stale declaration; it is not their claim.
    payload = {"claims": [
        {"claim_id": f"claim-{number}", "owner": "agent", "pull_request": number,
         "status": "active", "paths": ["runs/shared/**"]},
        {"claim_id": "stale", "owner": "agent", "pull_request": 900,
         "status": "active", "paths": ["runs/stale/**"]},
    ]}
    return {"content": base64.b64encode(json.dumps(payload).encode()).decode()}


def test_paginates_and_reads_only_each_requests_own_claim(collector, monkeypatch):
    calls = []

    def get(url, token):
        calls.append(url)
        if "pulls?" in url:
            numbers = range(1, 101) if url.endswith("&page=1") else [101]
            return [{"number": n, "head": {"sha": f"head-{n}"}} for n in numbers]
        return _blob(int(url.rsplit("head-", 1)[1]))

    monkeypatch.setattr(collector, "_get", get)
    result = collector.collect_claims("owner/repo", "token", 1, "config/work_claims.yaml")
    assert len(result) == 100
    assert result["101"][0]["claim_id"] == "claim-101"
    assert all(len(rows) == 1 for rows in result.values())
    assert any("page=2" in url for url in calls)


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError(),
    HTTPError("url", 403, "forbidden", {}, None),
    HTTPError("url", 404, "not found", {}, None), ValueError("malformed")])
def test_collection_failure_returns_nonzero_without_success_artifact(
    collector, monkeypatch, tmp_path, failure
):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    out = tmp_path / "claims.json"
    monkeypatch.setattr("sys.argv", ["collect", "--repository", "owner/repo", "--out", str(out)])
    monkeypatch.setattr(collector, "_get", lambda *args: (_ for _ in ()).throw(failure))
    assert collector.main() == 1
    assert not out.exists()


def test_missing_token_is_failure(collector, monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["collect", "--repository", "owner/repo", "--out", str(tmp_path / "out")])
    assert collector.main() == 1


def test_shifted_pages_require_a_stable_rescan(collector, monkeypatch):
    snapshots = iter([
        ({}, {1: "a", 3: "c"}),
        ({"2": [{"paths": ["runs/shared/**"]}]}, {2: "b", 3: "c"}),
        ({"2": [{"paths": ["runs/shared/**"]}]}, {2: "b", 3: "c"}),
    ])
    monkeypatch.setattr(collector, "_collect_snapshot", lambda *args: next(snapshots))
    assert "2" in collector.collect_claims("repo", "token", None, "registry")


def test_unstable_membership_fails_closed(collector, monkeypatch):
    snapshots = iter([({}, {1: str(n)}) for n in range(4)])
    monkeypatch.setattr(collector, "_collect_snapshot", lambda *args: next(snapshots))
    with pytest.raises(ValueError, match="did not stabilize"):
        collector.collect_claims("repo", "token", None, "registry")


def test_invalid_list_response_is_failure(collector, monkeypatch):
    monkeypatch.setattr(collector, "_get", lambda *args: {"message": "rate limited"})
    with pytest.raises(ValueError, match="must be a list"):
        collector.collect_claims("owner/repo", "token", None, "config/work_claims.yaml")


def test_later_page_failure_does_not_publish_partial_result(collector, monkeypatch, tmp_path):
    def get(url, token):
        if url.endswith("&page=1"):
            return [{"number": n, "head": {"sha": f"head-{n}"}} for n in range(1, 101)]
        if url.endswith("&page=2"):
            raise URLError("page two unavailable")
        return _blob(int(url.rsplit("head-", 1)[1]))

    out = tmp_path / "claims.json"
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setattr("sys.argv", ["collect", "--repository", "owner/repo", "--out", str(out)])
    monkeypatch.setattr(collector, "_get", get)
    assert collector.main() == 1
    assert not out.exists()


def test_required_foreign_snapshot_missing_is_failure(tmp_path):
    validator = _module("validate_work_claims")
    with pytest.raises(ValueError, match="not collected"):
        validator._foreign_violations(SimpleNamespace(open_requests=str(tmp_path / "missing")), ())
