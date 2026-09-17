"""Atomic publication of a verified canonical run bundle.

The publisher never stages the working tree.  It builds a Git tree from the
expected execution-branch head, creates one commit, and advances the branch
with an expected-old-SHA check.  Optional remote publication uses the same
compare-and-swap lease.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Iterable, Mapping

from .canonical_completion import CompletionProof, validate_completion_bundle


class AtomicPublicationError(RuntimeError):
    """The execution branch could not be published without losing lineage."""


@dataclass(frozen=True)
class PublicationResult:
    commit_sha: str
    branch: str
    changed_paths: tuple[str, ...]
    completion: CompletionProof | None = None


def _git(
    repo_root: Path,
    args: list[str],
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        command = " ".join(args[:2])
        raise AtomicPublicationError(
            f"git {command} failed: {detail.splitlines()[0] if detail else 'unknown error'}"
        )
    return completed.stdout.strip()


def _branch_ref(branch: str) -> tuple[str, str]:
    name = str(branch or "").strip()
    if name.startswith("refs/heads/"):
        name = name.removeprefix("refs/heads/")
    forbidden = ("..", "@{", "//", "\\", "~", "^", ":", "?", "*", "[", " ")
    if (
        not name
        or name.startswith(("-", "/", "."))
        or name.endswith(("/", "."))
        or any(token in name for token in forbidden)
        or any(ord(char) < 32 for char in name)
    ):
        raise AtomicPublicationError(f"invalid execution branch: {branch!r}")
    return name, f"refs/heads/{name}"


def _destination(value: str) -> str:
    name = str(value or "").strip()
    path = Path(name)
    if (
        not name
        or name in {".", ".."}
        or path.is_absolute()
        or ".." in path.parts
        or name.endswith("/")
    ):
        raise AtomicPublicationError(f"invalid destination path: {value!r}")
    return path.as_posix()


def _tree_contains(repo_root: Path, commit: str, path: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{commit}:{path}"],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def atomic_publish_files(
    repo_root: str | Path,
    files: Mapping[str, str | Path],
    *,
    branch: str,
    expected_head: str,
    commit_message: str,
    replace_paths: Iterable[str] = (),
    remote: str = "origin",
    push: bool = False,
) -> PublicationResult:
    """Create one commit from verified files and publish it with a SHA lease.

    ``files`` maps repository-relative destination paths to existing source
    files.  No source file is copied into the checkout and the regular index is
    never touched.  A branch move between the preflight read and the compare
    and swap update aborts the publication.
    """
    root = Path(repo_root).resolve()
    if not (root / ".git").exists():
        raise AtomicPublicationError(f"repository root has no .git directory: {root}")
    branch_name, branch_ref = _branch_ref(branch)
    expected = str(expected_head or "").strip().lower()
    if len(expected) != 40 or any(char not in "0123456789abcdef" for char in expected):
        raise AtomicPublicationError("expected_head must be a full commit SHA")
    if not str(commit_message or "").strip():
        raise AtomicPublicationError("commit_message is required")
    current = _git(root, ["rev-parse", "--verify", branch_ref]).lower()
    if current != expected:
        raise AtomicPublicationError(
            f"execution branch moved before publish: expected {expected}, found {current}"
        )
    if not files:
        raise AtomicPublicationError("atomic publication has no files")
    normalized: dict[str, Path] = {}
    for destination, source in files.items():
        target = _destination(destination)
        path = Path(source).resolve()
        if not path.is_file() or path.is_symlink():
            raise AtomicPublicationError(f"publication source is not a regular file: {path}")
        if target in normalized:
            raise AtomicPublicationError(f"duplicate publication destination: {target}")
        normalized[target] = path
    replace = {_destination(path) for path in replace_paths}
    unknown_replacements = sorted(replace - normalized.keys())
    if unknown_replacements:
        raise AtomicPublicationError(
            "replace_paths contains destinations not in publication: "
            + ", ".join(unknown_replacements)
        )
    collisions = sorted(
        path
        for path in normalized
        if path not in replace and _tree_contains(root, expected, path)
    )
    if collisions:
        raise AtomicPublicationError(
            "immutable publication destination already exists: "
            + ", ".join(collisions)
        )

    env = os.environ.copy()
    descriptor, index_name = tempfile.mkstemp(prefix="valuation-canonical-index-")
    os.close(descriptor)
    index_path = Path(index_name)
    index_path.unlink(missing_ok=True)
    env["GIT_INDEX_FILE"] = str(index_path)
    commit = ""
    try:
        _git(root, ["read-tree", expected], env=env)
        for destination, source in sorted(normalized.items()):
            blob = _git(
                root,
                ["hash-object", "-w", "--path", destination, str(source)],
                env=env,
            )
            _git(
                root,
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{destination}",
                ],
                env=env,
            )
        tree = _git(root, ["write-tree"], env=env)
        commit_env = dict(env)
        commit_env.setdefault("GIT_AUTHOR_NAME", "canonical-publisher")
        commit_env.setdefault(
            "GIT_AUTHOR_EMAIL", "canonical-publisher@users.noreply.github.com"
        )
        commit_env.setdefault("GIT_COMMITTER_NAME", commit_env["GIT_AUTHOR_NAME"])
        commit_env.setdefault("GIT_COMMITTER_EMAIL", commit_env["GIT_AUTHOR_EMAIL"])
        commit = _git(
            root,
            ["commit-tree", tree, "-p", expected, "-m", commit_message.strip()],
            env=commit_env,
        )
        if push:
            lease = f"refs/heads/{branch_name}:{expected}"
            _git(
                root,
                [
                    "push",
                    "--atomic",
                    f"--force-with-lease={lease}",
                    remote,
                    f"{commit}:{branch_ref}",
                ],
                env=commit_env,
            )
        _git(root, ["update-ref", branch_ref, commit, expected])
    except AtomicPublicationError:
        raise
    finally:
        index_path.unlink(missing_ok=True)
    return PublicationResult(
        commit_sha=commit,
        branch=branch_name,
        changed_paths=tuple(sorted(normalized)),
    )


def publish_verified_bundle(
    repo_root: str | Path,
    bundle_dir: str | Path,
    *,
    stage_registry_path: str | Path,
    branch: str,
    expected_head: str,
    destination_prefix: str,
    latest_manifest_path: str | Path | None = None,
    latest_destination: str | None = None,
    commit_message: str,
    remote: str = "origin",
    push: bool = False,
) -> PublicationResult:
    """Validate a v2 bundle and publish its exact bytes in one commit."""
    bundle = Path(bundle_dir).resolve()
    completion = validate_completion_bundle(
        bundle,
        stage_registry_path=stage_registry_path,
        latest_manifest_path=latest_manifest_path,
    )
    prefix = _destination(destination_prefix)
    files: dict[str, Path] = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            relative = path.relative_to(bundle).as_posix()
            files[f"{prefix}/{relative}"] = path
    if latest_manifest_path is not None:
        if not latest_destination:
            raise AtomicPublicationError(
                "latest_destination is required with latest_manifest_path"
            )
        files[_destination(latest_destination)] = Path(latest_manifest_path).resolve()
    result = atomic_publish_files(
        repo_root,
        files,
        branch=branch,
        expected_head=expected_head,
        commit_message=commit_message,
        replace_paths=(latest_destination,) if latest_destination else (),
        remote=remote,
        push=push,
    )
    return PublicationResult(
        commit_sha=result.commit_sha,
        branch=result.branch,
        changed_paths=result.changed_paths,
        completion=completion,
    )
