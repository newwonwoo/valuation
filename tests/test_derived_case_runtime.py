"""Trusted operator registry wiring, not an artifact-provided code loader."""
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
from tests.test_derived_case_ingress import _fixture
from valuation_engine.cold_start_probe import probe_runtime_spec, probe_network, _staff_scripts
from valuation_engine.generic_live_providers import build_generic_kr_runtime_factory, GenericValuationPlanError
from valuation_engine.llm_transport import ScriptedTransport
from valuation_engine.generic_underwriting import declared_underwriting_provider, DeclaredUnderwritingError


def test_typed_runtime_factory_forwards_exact_operator_registry(tmp_path):
    path, _, _, registry = _fixture(tmp_path)
    spec = replace(probe_runtime_spec(), as_of='2026-10-26', declared_underwriting_path=path,
                   trusted_derived_case_registry=registry)
    with patch('valuation_engine.generic_live_providers.declared_underwriting_provider',
               wraps=declared_underwriting_provider) as provider:
        factory = build_generic_kr_runtime_factory(network=probe_network(),
            transport=ScriptedTransport(_staff_scripts()), spec=spec)
    assert provider.call_args.kwargs['trusted_derived_case_registry'] is registry
    assert factory.run_lineage_validator is not None


def test_derived_run_without_trusted_registry_does_not_silently_fallback(tmp_path):
    path, _, _, _ = _fixture(tmp_path)
    with pytest.raises(DeclaredUnderwritingError, match='WORK_REQUIRED'):
        build_generic_kr_runtime_factory(network=probe_network(),
            transport=ScriptedTransport(_staff_scripts()),
            spec=replace(probe_runtime_spec(), as_of='2026-10-26', declared_underwriting_path=path))


@pytest.mark.parametrize('untrusted', [{'models': []}, 'module:execute', ['calculator.py']])
def test_serialized_registry_never_becomes_runtime_execution_authority(untrusted):
    with pytest.raises(GenericValuationPlanError, match='operator-supplied'):
        replace(probe_runtime_spec(), trusted_derived_case_registry=untrusted).validate()


def test_runner_rejects_registry_directive_in_run_yaml_before_network(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_kr_live
    with patch.object(run_kr_live, '_load_run', return_value={'trusted_derived_case_registry': 'source:run'}), \
         patch.object(run_kr_live, '_build_network') as network:
        with pytest.raises(run_kr_live.RunbookError, match='operator/API'):
            run_kr_live.execute_run(tmp_path)
    network.assert_not_called()


def test_llm_cannot_select_preexisting_registry_in_runtime_factory(tmp_path):
    from valuation_engine.runtime_authority import llm_proposal_scope
    path, _, _, registry = _fixture(tmp_path)
    spec = replace(probe_runtime_spec(), as_of='2026-10-26',
                   declared_underwriting_path=path, trusted_derived_case_registry=registry)
    with llm_proposal_scope():
        with pytest.raises(PermissionError, match='LLM'):
            spec.validate()
        with pytest.raises(PermissionError, match='LLM'):
            build_generic_kr_runtime_factory(network=probe_network(),
                transport=ScriptedTransport(_staff_scripts()), spec=spec)
