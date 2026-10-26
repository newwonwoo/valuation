"""Runtime edges, not merely campaign-construction policy tests."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import pytest
import yaml
from tests.test_broker_research_policy import reviewed_plan, informed_answer
from valuation_engine.research_campaign import run_campaign, merge_underwriting
from valuation_engine.generic_underwriting import load_declared_underwriting, DeclaredUnderwritingError
from valuation_engine.official_market_data import load_broker_review_context, DataCollectionError
from valuation_engine.cold_start_probe import probe_runtime_spec, probe_network, _staff_scripts, PROBE_COMPANY_NAME
from valuation_engine.generic_live_providers import build_generic_kr_runtime_factory
from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.llm_transport import ScriptedTransport


def merged():
    plan = reviewed_plan()
    result = run_campaign(plan, responder=informed_answer)
    return merge_underwriting({'target_id': plan['target_id'], 'as_of': plan['as_of'], 'declarations': {}}, result)


def test_informed_nested_receipt_cannot_be_relabelled_blind_at_file_ingress(tmp_path):
    original = merged()
    path = tmp_path/'underwriting.yaml'
    path.write_text(yaml.safe_dump(original))
    assert load_declared_underwriting(path)['research_mode'] == 'INFORMED_REVISION'
    for key in ['research_mode', 'informed_revision']:
        broken = deepcopy(original); broken.pop(key)
        path.write_text(yaml.safe_dump(broken))
        with pytest.raises(DeclaredUnderwritingError): load_declared_underwriting(path)
    broken = deepcopy(original); broken.pop('research_mode'); broken.pop('informed_revision')
    path.write_text(yaml.safe_dump(broken))
    with pytest.raises(DeclaredUnderwritingError, match='nested informed'): load_declared_underwriting(path)


def test_nested_receipt_cannot_change_outer_new_run(tmp_path):
    value = merged(); value['informed_revision']['new_run_id'] = 'UNRELATED-NEW-RUN'
    path=tmp_path/'u.yaml'; path.write_text(yaml.safe_dump(value))
    with pytest.raises(DeclaredUnderwritingError, match='outer revision'):load_declared_underwriting(path)


def test_research_context_exposes_exact_bytes_and_rejects_changed_artifact(tmp_path):
    source=tmp_path/'source.json'; source.write_text('{"claim":"Forecast is uncertain, not a reported fact"}')
    manifest=tmp_path/'context.json'; manifest.write_text(json.dumps({'schema_version':'broker-review-context/v1',
        'purpose':'Source-bound host review only','artifacts':[{'artifact_id':'source1','path':'source.json',
        'sha256':sha256(source.read_bytes()).hexdigest(),'description':'A synthetic research attachment'}]}))
    out=load_broker_review_context(manifest)
    assert out['artifacts'][0]['content_text']==source.read_text()
    source.write_text('{"claim":"Different claim"}')
    with pytest.raises(DataCollectionError, match='hash mismatch'):load_broker_review_context(manifest)


def test_typed_factory_checks_actual_lineage_before_run(tmp_path):
    factory=build_generic_kr_runtime_factory(network=probe_network(),transport=ScriptedTransport(_staff_scripts()),spec=probe_runtime_spec())
    seen=[]
    def deny(request):
        seen.append(request.run_id); raise ValueError('wrong parent lineage')
    factory=replace(factory,run_lineage_validator=deny)
    req=LiveAnalysisRequest(command=f'분석시작 {PROBE_COMPANY_NAME}',company_query=PROBE_COMPANY_NAME,
        state_root=str(tmp_path),run_id='NEW-INFORMED-RUN',jurisdiction='KR')
    with pytest.raises(ValueError,match='wrong parent'):factory(req)
    assert seen==['NEW-INFORMED-RUN'] and not list(tmp_path.iterdir())


def test_derived_business_receipt_cannot_strip_outer_informed_mode(tmp_path):
    original=merged()
    row=original['declarations']['utilization']
    receipt=row.pop('research_receipt')
    row['business_cashflow_receipt']={'assumptions':{'capacity':receipt}}
    path=tmp_path/'business.yaml';path.write_text(yaml.safe_dump(original))
    assert load_declared_underwriting(path)['research_mode']=='INFORMED_REVISION'
    original.pop('research_mode');original.pop('informed_revision')
    path.write_text(yaml.safe_dump(original))
    with pytest.raises(DeclaredUnderwritingError,match='nested informed'):
        load_declared_underwriting(path)
