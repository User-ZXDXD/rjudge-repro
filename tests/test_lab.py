from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from safety_lab.api import create_app
from safety_lab.metrics import DATA,evaluate_judgments,from_confusion,mas_results,rjudge_results

def test_historical_rjudge_metrics_match_existing_repository():
    results=rjudge_results()['results']
    root=Path(__file__).resolve().parents[1]
    for split in ('unintended','injection'):
        expected=json.loads((root/'results'/('deepseek-flash_'+split)/'summary.json').read_text(encoding='utf-8'))['overall']
        for key in ('n','tp','fp','fn','tn','f1','recall','specificity','precision','validity'):
            assert results[split][key]==pytest.approx(expected[key])

def test_invalid_output_is_error_and_retained_in_denominator():
    result=evaluate_judgments([{'id':'a','label':1,'output':'safe unsafe'},{'id':'b','label':0,'output':'unknown'}])
    assert result['n']==2 and result['invalid']==2 and result['validity']==0
    assert result['fn']==1 and result['fp']==1 and result['accuracy']==0
    assert result['records'][0]['raw_pred']==-1

def test_safe_is_not_matched_inside_unsafe():
    result=evaluate_judgments([{'id':'a','label':1,'output':'unsafe.'},{'id':'b','label':0,'output':'safe!'}])
    assert result['tp']==1 and result['tn']==1 and result['f1']==1

@pytest.mark.parametrize('rows',[
 [],[{'id':'x','label':True,'output':'safe'}],
 [{'id':'x','label':1,'output':'safe','raw_pred':1}],
 [{'id':'x','label':1,'raw_pred':3}],
 [{'id':'x','label':1,'raw_pred':1},{'id':'x','label':0,'raw_pred':0}],
])
def test_rejects_invalid_or_ambiguous_input(rows):
    with pytest.raises(ValueError):evaluate_judgments(rows)

def test_gsafeguard_keeps_negative_result_and_question_level_pairing():
    result=mas_results()
    assert result['n_independent_questions']==16
    assert result['conditions']['attack/no_guard']['failures']==38
    assert result['conditions']['attack/guard']['failures']==44
    assert result['conditions']['clean/no_guard']['failures']==result['conditions']['clean/guard']['failures']==8
    assert result['conditions']['attack/no_guard']['benign_failure_rate']==pytest.approx(6/96)
    assert result['conditions']['attack/guard']['benign_failure_rate']==pytest.approx(12/96)
    assert result['paired']['attack']['difference_guard_minus_no_guard']==pytest.approx(6/128)
    assert result['paired']['attack']['n_independent_questions']==16
    assert result['paired']['attack']['unit']=='question'


def test_attack_roles_survive_clean_episode_first_order():
    cases=json.loads((DATA/'gsafeguard_outcomes.json').read_text(encoding='utf-8'))
    assert len(cases)==16 and all(len(case['initial_attackers'])==2 for case in cases)
    assert all(len(set(case['initial_attackers']))==2 for case in cases)

def test_api_runs_and_exports_real_offline_report(tmp_path):
    client=TestClient(create_app(tmp_path))
    assert client.get('/health').json()['model_requests']==0
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    result=client.post('/api/evaluate/rjudge',json={'records':[{'id':'a','label':1,'output':'unsafe'}]})
    assert result.status_code==200
    report=result.json()
    assert report['result']['f1']==1 and report['result']['new_model_requests']==0
    assert client.get('/api/runs/'+report['run_id']).json()==report
    assert len(list(tmp_path.glob('*.json')))==1

def test_api_errors_do_not_create_reports(tmp_path):
    client=TestClient(create_app(tmp_path))
    assert client.post('/api/evaluate/rjudge',json={'records':[{'label':2,'output':'safe'}]}).status_code==422
    assert client.get('/api/runs/not-an-id').status_code==404
    assert not list(tmp_path.glob('*'))

def test_api_limits_and_split_validation(tmp_path):
    client=TestClient(create_app(tmp_path))
    assert client.get('/api/cases/rjudge?split=other').status_code==422
    assert client.post('/api/evaluate/rjudge',content='x',headers={'content-length':str(3*1024*1024)}).status_code==413

def test_published_outcomes_have_no_original_prompt_fields():
    rows=json.loads((DATA/'rjudge_outcomes.json').read_text(encoding='utf-8'))
    assert len(rows)==564
    assert all(set(row)<={'id','split','scenario','label','raw_pred','pred'} for row in rows)

def test_catalog_and_both_result_contracts(tmp_path):
    client=TestClient(create_app(tmp_path))
    assert client.get('/api/catalog').json()['provenance']['new_LLM_requests']==0
    assert client.get('/api/results/rjudge').json()['benchmark']=='R-Judge'
    assert client.get('/api/results/gsafeguard').json()['benchmark']=='G-Safeguard'
    assert len(client.get('/api/cases/gsafeguard').json()['cases'])==16


def test_rjudge_auth_requires_explicit_provider_key(monkeypatch):
    import builtins
    from rj_client import load_api_key
    monkeypatch.delenv('RJ_API_KEY', raising=False)
    def forbidden_read(*args, **kwargs):
        raise AssertionError('Must not read another application credentials')
    monkeypatch.setattr(builtins, 'open', forbidden_read)
    with pytest.raises(RuntimeError, match='Set RJ_API_KEY'):
        load_api_key()
    monkeypatch.setenv('RJ_API_KEY', 'test-key-not-a-real-credential')
    assert load_api_key() == 'test-key-not-a-real-credential'
