from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_installed_wheel_constructs_live_registry_loaders_outside_checkout(tmp_path):
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "installed"
    source_dir = tmp_path / "source"
    wheel_dir.mkdir()
    source_dir.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", source_dir / "pyproject.toml")
    shutil.copytree(ROOT / "src", source_dir / "src")
    shutil.copytree(ROOT / "config", source_dir / "config")
    build_python = sys._base_executable

    subprocess.run(
        [
            build_python,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(source_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    script = """
from valuation_engine.dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from valuation_engine.finite_life_evaluators import FiniteLifeNPVRegistration, live_finite_npv_registry_loader
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.rnpv_evaluator import LiveRNPVRegistration, live_rnpv_registry_loader

registry = load_default_method_capability_registry()
assert registry.get("contracted_backlog", "normalized_dcf").execution_family == "explicit_fcff_dcf"
live_fcff_dcf_registry_loader(
    registrations=(LiveDCFRegistration("contracted_backlog", "normalized_dcf", "1", 5),)
)
live_finite_npv_registry_loader(
    registrations=(FiniteLifeNPVRegistration("project_finance", "project_npv", "1", 5),)
)
live_rnpv_registry_loader(
    registrations=(LiveRNPVRegistration("probabilistic_pipeline", "rnpv", "1", 5, "phase3"),)
)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(install_dir)
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
