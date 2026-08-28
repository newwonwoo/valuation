from __future__ import annotations

import pytest

from valuation_engine.assumption_compiler import compile_assumptions
from valuation_engine.continuous_financial_path_probability import (
    simulate_continuous_financial_paths,
)
from valuation_engine.probability_engine_v3 import run_probability_engine_v3
from valuation_engine.runtime_authority import llm_proposal_scope


def test_llm_callback_cannot_commit_assumptions():
    with llm_proposal_scope(), pytest.raises(
        PermissionError, match="assumption_compile"
    ):
        compile_assumptions(
            target_id="TEST",
            ledger=None,  # type: ignore[arg-type]
            hypotheses=(),
            bridges=(),
            specs=(),
            bridge_input_map={},
        )


def test_llm_callback_cannot_run_probability_engine():
    with llm_proposal_scope(), pytest.raises(PermissionError, match="probability"):
        run_probability_engine_v3(None)  # type: ignore[arg-type]


def test_llm_callback_cannot_run_continuous_path_monte_carlo():
    with llm_proposal_scope(), pytest.raises(PermissionError, match="probability"):
        simulate_continuous_financial_paths(
            drivers=(),
            scenarios=(),
            dependence=None,  # type: ignore[arg-type]
        )
