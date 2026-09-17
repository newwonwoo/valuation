from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from valuation_engine.transactional_publisher import (
    AtomicPublicationError,
    atomic_publish_files,
)


def _run(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    head = _run(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "branch", "execution", head], check=True)
    return repo, head


def test_atomic_publisher_creates_one_commit_without_touching_checkout(tmp_path):
    repo, head = _repo(tmp_path)
    source = tmp_path / "report.md"
    source.write_text("verified\n", encoding="utf-8")
    before_index = _run(repo, "rev-parse", "--git-path", "index")
    result = atomic_publish_files(
        repo,
        {"canonical-runs/000001/report.md": source},
        branch="execution",
        expected_head=head,
        commit_message="publish verified run",
    )
    assert result.branch == "execution"
    assert result.changed_paths == ("canonical-runs/000001/report.md",)
    assert _run(repo, "rev-parse", "execution") == result.commit_sha
    assert _run(repo, "show", "execution:canonical-runs/000001/report.md") == "verified"
    assert _run(repo, "status", "--porcelain") == ""
    assert Path(before_index).is_file()
    assert _run(repo, "rev-list", "--count", "execution") == "2"


def test_atomic_publisher_rejects_stale_head_before_writing(tmp_path):
    repo, head = _repo(tmp_path)
    source = tmp_path / "report.md"
    source.write_text("verified\n", encoding="utf-8")
    first = atomic_publish_files(
        repo,
        {"a.txt": source},
        branch="execution",
        expected_head=head,
        commit_message="first",
    )
    with pytest.raises(AtomicPublicationError, match="moved"):
        atomic_publish_files(
            repo,
            {"b.txt": source},
            branch="execution",
            expected_head=head,
            commit_message="stale",
        )
    assert _run(repo, "rev-parse", "execution") == first.commit_sha
    assert not (repo / "b.txt").exists()


def test_atomic_publisher_does_not_overwrite_an_immutable_artifact(tmp_path):
    repo, head = _repo(tmp_path)
    source = tmp_path / "report.md"
    source.write_text("verified\n", encoding="utf-8")
    first = atomic_publish_files(
        repo,
        {"canonical-runs/000001/report.md": source},
        branch="execution",
        expected_head=head,
        commit_message="first",
    )
    with pytest.raises(AtomicPublicationError, match="already exists"):
        atomic_publish_files(
            repo,
            {"canonical-runs/000001/report.md": source},
            branch="execution",
            expected_head=first.commit_sha,
            commit_message="overwrite",
        )
