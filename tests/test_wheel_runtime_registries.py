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
    build_python = sys.executable

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

    # In this layout ``Path(live_runtime.__file__).parents[2]`` is tmp_path. These files
    # simulate an unrelated parent-level config directory and must never impersonate the
    # registries packaged in the wheel.
    incidental_config = tmp_path / "config"
    incidental_config.mkdir()
    runtime_registry_names = (
        "control_plane_stage_registry.yaml",
        "archetype_module_registry.yaml",
        "archetype_control_requirements.yaml",
        "industry_source_registry.yaml",
        "unit_contract_registry.yaml",
    )
    for name in runtime_registry_names:
        (incidental_config / name).write_text(
            "unrelated_parent_config: true\n",
            encoding="utf-8",
        )

    script = """
from pathlib import Path

from valuation_engine.cli_runtime import LiveAnalysisRequest, build_live_runtime_config
from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.dcf_evaluators import LiveDCFRegistration, live_fcff_dcf_registry_loader
from valuation_engine.finite_life_evaluators import FiniteLifeNPVRegistration, live_finite_npv_registry_loader
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import LiveCollectorProvider, LivePrimaryProviders, LivePrimaryRuntimeConfig
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.orchestrator import load_stage_sequence
from valuation_engine.rnpv_evaluator import LiveRNPVRegistration, live_rnpv_registry_loader
from valuation_engine.scenario_binding import ScenarioBindingSpec
from valuation_engine.unit_contracts import load_unit_contract_registry

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


def build_config(request):
    noop = lambda *args, **kwargs: None
    collector = LiveCollectorProvider(
        CollectorCapability(
            "fixture",
            "FIXTURE_PRIMARY",
            ("x",),
            ("GLOBAL",),
            "wheel.fixture",
        ),
        noop,
    )
    providers = LivePrimaryProviders(
        company_resolver=noop,
        industry_snapshot_loader=noop,
        freshness_loader=noop,
        segment_decomposer=noop,
        industry_dna_router=noop,
        collectors=(collector,),
        scanner_runners={},
        intelligence_officer=noop,
        red_team_officer=noop,
        bridge_analyst=noop,
        evaluator_registry_loader=noop,
        valuation_plan_inputs_loader=noop,
    )
    return LivePrimaryRuntimeConfig(
        run_id=request.run_id,
        state_root=request.state_root,
        company_request=CompanyResolutionRequest(request.company_query),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("x",)),
        providers=providers,
    )


request = LiveAnalysisRequest(
    command="분석시작 Wheel Target",
    company_query="Wheel Target",
    state_root=Path.cwd() / "state",
    run_id="WHEEL-LIVE-1",
)
config = build_live_runtime_config(request, build_config)
registry_fields = (
    "stage_registry_path",
    "archetype_registry_path",
    "archetype_control_requirements_path",
    "industry_source_registry_path",
    "unit_contract_registry_path",
)
incidental = Path.cwd() / "config"
for field in registry_fields:
    path = Path(getattr(config, field))
    assert path.is_file(), (field, path)
    assert path.parent != incidental, (field, path)
assert len(load_stage_sequence(config.stage_registry_path)) == 33
assert load_unit_contract_registry(config.unit_contract_registry_path).units
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
