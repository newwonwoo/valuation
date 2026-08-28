from __future__ import annotations

import inspect

import valuation_engine
from valuation_engine.strict_cli_runtime import execute_live_analysis
from valuation_engine.strict_live_runtime import (
    CANONICAL_ENTRYPOINT_ID,
    run_prism as strict_run_prism,
)


def test_package_run_prism_is_strict_attested_entrypoint():
    assert valuation_engine.run_prism is strict_run_prism
    assert valuation_engine.run_prism_legacy is not strict_run_prism
    assert CANONICAL_ENTRYPOINT_ID == "prism_strict_live_primary/v1"


def test_live_cli_default_runner_is_strict_entrypoint():
    parameter = inspect.signature(execute_live_analysis).parameters["runner"]
    assert parameter.default is strict_run_prism


def test_package_execute_live_analysis_exports_strict_cli_path():
    assert valuation_engine.execute_live_analysis is execute_live_analysis
