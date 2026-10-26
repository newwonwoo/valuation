"""Per-run map plumbing preserves registry authority and canonical validation.

All custom economic classifications here are explicit synthetic test fixtures,
not findings about the company used for the runner's source replay fixture.
"""
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import research_report_completion as completion
import run_kr_live
import valuation_engine.generic_live_providers as providers
from valuation_engine.generic_kr_industry import DEFAULT_CLASSIFICATION_MAP_PATH

SOURCE = ROOT / 'runs/kisco-104700'


@pytest.fixture
def prepared(tmp_path):
    target = tmp_path / 'run'
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns('out'))
    config = yaml.safe_load((target / 'run.yaml').read_text())
    for key in ('artifact', 'provenance', 'conditioning'):
        config['calibration'][key] = str((SOURCE / config['calibration'][key]).resolve())
    (target / 'run.yaml').write_text(yaml.safe_dump(config))
    return target


def configure(prepared, **changes):
    path = prepared / 'run.yaml'
    config = yaml.safe_load(path.read_text())
    config.update(changes)
    path.write_text(yaml.safe_dump(config))
    return config


def custom_map(path, *, archetypes=('service_operations', 'metered_usage_network')):
    default = yaml.safe_load(Path(DEFAULT_CLASSIFICATION_MAP_PATH).read_text())
    row = next(row for row in default['classifications']
               if '2411'.startswith(str(row['ksic_prefix'])))
    row = {**row, 'ksic_prefix': '2411', 'label': 'Synthetic mixed-economics plumbing fixture',
           'sector_adapter': 'software.it_services', 'archetypes': list(archetypes)}
    path.write_text(yaml.safe_dump({'version': 'fixture-1', 'classifications': [row]}))
    return path


@pytest.mark.parametrize('declared', [False, True])
def test_actual_runtime_factory_receives_declared_map_or_unchanged_default(prepared, tmp_path, monkeypatch, declared):
    expected = Path(DEFAULT_CLASSIFICATION_MAP_PATH)
    if declared:
        expected = custom_map(prepared / 'custom-map.yaml')
        configure(prepared, classification_map_path='custom-map.yaml')
    loaded, specs = [], []
    real_load = providers.load_kr_industry_classification
    def tracked_load(path):
        loaded.append(Path(path))
        return real_load(path)
    monkeypatch.setattr(providers, 'load_kr_industry_classification', tracked_load)
    real_factory = run_kr_live.build_generic_kr_runtime_factory
    def tracked_factory(**kwargs):
        specs.append(kwargs['spec'])
        return real_factory(**kwargs)  # exercise the actual factory and map parser
    monkeypatch.setattr(run_kr_live, 'build_generic_kr_runtime_factory', tracked_factory)
    monkeypatch.setattr(run_kr_live, 'run_prism', lambda runtime:
        SimpleNamespace(result=SimpleNamespace(stage_traces=(), data={})))
    run_kr_live.execute_run(prepared, state_root=str(tmp_path / 'state'))
    assert len(specs) == 1
    assert Path(specs[0].classification_map_path) == expected
    assert loaded == [expected]


def test_missing_method_candidates_use_same_declared_map_and_primary_role(prepared):
    custom_map(prepared / 'custom-map.yaml')
    configure(prepared, classification_map_path='custom-map.yaml', method='')
    request = completion._early_judgment(prepared, run_kr_live._run_input_sha256(prepared))
    candidates = request.response_schema['proposal']['method']['enum']
    assert set(candidates) == {'service_operations/normalized_service_dcf',
                               'metered_usage_network/usage_driver_dcf'}
    assert not any('warranted_per' in method for method in candidates)
    assert not any('commodity_price_taker' in method for method in candidates)


def test_default_missing_method_candidates_remain_unchanged(prepared):
    configure(prepared, method='')
    request = completion._early_judgment(prepared, run_kr_live._run_input_sha256(prepared))
    assert 'commodity_price_taker/normalized_multiple' in request.response_schema['proposal']['method']['enum']
    assert 'service_operations/normalized_service_dcf' not in request.response_schema['proposal']['method']['enum']


@pytest.mark.parametrize('invalid', [None, '', '   ', 3, [], {}, 'missing-map.yaml', '.'])
def test_explicit_invalid_or_missing_map_never_silently_uses_default(prepared, invalid, monkeypatch):
    configure(prepared, classification_map_path=invalid)
    monkeypatch.setattr(run_kr_live, '_build_network', lambda path: pytest.fail('invalid map must fail before runtime assembly'))
    with pytest.raises(run_kr_live.RunbookError, match='classification_map_path|classification map file is missing'):
        run_kr_live.execute_run(prepared)
    configure(prepared, method='')
    request = completion._early_judgment(prepared, run_kr_live._run_input_sha256(prepared))
    assert request.allowed_actions == ('UNRESOLVED',)
    assert request.response_schema['proposal']['method']['enum'] == []
    assert 'classification' in request.gap.detail


def test_invalid_existing_map_cannot_fall_back_to_default(prepared, tmp_path):
    path = prepared / 'invalid-map.yaml'
    path.write_text('classifications: []\n')
    configure(prepared, classification_map_path=path.name)
    with pytest.raises(ValueError, match='non-empty|classifications list'):
        run_kr_live.execute_run(prepared, state_root=str(tmp_path / 'state'))
    configure(prepared, method='')
    request = completion._early_judgment(prepared, run_kr_live._run_input_sha256(prepared))
    assert request.allowed_actions == ('UNRESOLVED',)


def test_changed_external_map_invalidates_run_input_and_old_host_response(prepared, tmp_path):
    mapping = custom_map(tmp_path / 'external-classification.yaml')
    configure(prepared, classification_map_path=str(mapping), method='')
    old_hash = run_kr_live._run_input_sha256(prepared)
    request = completion._early_judgment(prepared, old_hash)
    with mapping.open('a') as stream:
        stream.write('# Explicit new evidence version fixture; not a host answer retry.\n')
    changed_hash = run_kr_live._run_input_sha256(prepared)
    assert old_hash != changed_hash
    changed = completion._early_judgment(prepared, changed_hash)
    assert request.request_hash != changed.request_hash
    with pytest.raises(run_kr_live.RunbookError, match='input hash is stale'):
        completion._apply_judgment(prepared, request, {
            'schema_version': 'host-judgment-response/v1', 'request_hash': request.request_hash,
            'target_id': request.gap.target_id, 'as_of': request.gap.as_of,
            'decision': 'UNRESOLVED', 'proposal': {}, 'rationale': 'Old map fixture answer.',
            'counterevidence': [], 'uncertainty': 'Unresolved fixture classification.'})


def test_no_map_kisco_canonical_completion_regression(prepared, tmp_path):
    reached, stopped, reason, result = run_kr_live.execute_run(prepared,
        state_root=str(tmp_path / 'state'), staff_mode='replay')
    assert stopped is None, reason
    assert result.completed and len(reached) >= 33
    assert '17,339' in result.data['final_report']
