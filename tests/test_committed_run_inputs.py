"""A committed run directory holds run inputs and nothing else.

``_run_input_sha256`` fingerprints every file under a run directory except
``out/``, so the directory's contents *are* the run's identity. That makes a
stray file — a tool's receipt, an editor backup, a scratch export — not merely
untidy: it silently changes what the run attests to, and anyone re-deriving the
hash without it gets a different answer.

So the shape is pinned. A run is its declaration, its declared judgments and
the raw payloads they were read from; anything else has to be added here
deliberately, with a reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"

#: Files a prepared run directory may carry at its top level. Each one is
#: hashed into the run's identity, so adding to this set is a deliberate act.
_ALLOWED_TOP_LEVEL = frozenset({"run.yaml", "RESEARCH_STATUS.md"})

#: Directories whose contents are run inputs.
_INPUT_DIRECTORIES = frozenset({"declarations", "raw"})

#: The result store. It is excluded from the run input hash, so a published
#: bundle committed there is a record rather than an input and does not change
#: what the run attests to.
_RESULT_DIRECTORY = "out"


def _committed_runs() -> tuple[Path, ...]:
    return tuple(
        sorted(path for path in RUNS.iterdir() if (path / "run.yaml").is_file())
    )


def test_the_repository_carries_the_runs_the_runbook_names():
    assert {path.name for path in _committed_runs()} == {
        "kisco-104700",
        "shinhanalpha-293940",
        "daehansteel-084010",
        "koreazinc-010130",
        "celltrion-068270",
    }


@pytest.mark.parametrize("run_dir", _committed_runs(), ids=lambda path: path.name)
def test_a_committed_run_carries_only_run_inputs(run_dir: Path):
    unexpected: list[str] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir)
        head = relative.parts[0]
        if head == _RESULT_DIRECTORY:
            continue  # ignored by git and excluded from the run input hash
        if head in _INPUT_DIRECTORIES:
            continue
        if len(relative.parts) == 1 and relative.name in _ALLOWED_TOP_LEVEL:
            continue
        unexpected.append(relative.as_posix())
    assert unexpected == [], (
        f"{run_dir.name} carries files that are not run inputs but are hashed "
        f"into the run's identity: {unexpected}"
    )
