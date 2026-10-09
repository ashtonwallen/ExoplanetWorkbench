"""Offline orchestration tests. Synthetic observations and scripted providers stay in tmp_path."""
import hashlib
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from exodiscovery import assistant, discovery, pipeline, research, science, store
from exodiscovery.allowance import InputAllowance, current
from exodiscovery.api import app
from exodiscovery.schemas import AIConfig, ResearchGoal, TargetDiscovery

PLAN={'strategy':'Inspect calibrated observations and test transit interpretations.',
      'steps':['Search the public archive','Analyze and review the evidence'],
      'selection_reason':'User supplied a named target for this test.'}
CONCLUSION={'assessment':'Computed evidence has been reviewed. These signals require additional observations and independent validation.',
            'investigation_ids':[],'limitations':['Synthetic test, not a scientific discovery.'],'next_steps':[]}


def call(name,args,ident=None):
    return {'id':ident or name,'name':name,'arguments':json.dumps(args)}


@pytest.fixture
def local_job(isolated_store,monkeypatch):
    monkeypatch.setattr(assistant,'config',lambda:AIConfig(provider='local',model='test-tools'))
    monkeypatch.setattr(assistant,'secret',lambda _:None)
    return research.create(ResearchGoal(goal='Investigate the explicitly synthetic test target.'))


def test_tic_ranking_uses_unique_sectors_and_filters():
    stars=[{'ID':str(i),'ra':90.,'dec':-66.,'Tmag':10+i/10,'rad':r,'Teff':5000} for i,r in [(1,1.),(2,.6),(3,5.),(4,None)]]
    observations=[{'target_name':str(i),'sequence_number':s} for i in range(1,5) for s in [1,1,1,2]]
    ranked=discovery.rank_targets(stars,observations,TargetDiscovery(rank_by='small_stars'))
    assert [r['tic_id'] for r in ranked]==['2','1']
    assert all(r['sector_count']==2 for r in ranked)
    assert not discovery.rank_targets(stars,observations,TargetDiscovery(min_sectors=3))


def test_region_failure_is_missing_evidence_and_retried(isolated_store,monkeypatch):
    attempts=[]
    def fail(*_):
        attempts.append(1)
        raise TimeoutError('Archive unavailable')
    monkeypatch.setattr(discovery,'query_region',fail)
    for _ in range(2):
        result=discovery.discover(TargetDiscovery())
        assert not result['targets']
        assert all(r['status']=='unavailable' and r['eligible_observed_stars'] is None for r in result['regions'])
    assert len(attempts)==4


def test_api_requires_configuration_and_snapshots_no_credentials(isolated_store,monkeypatch):
    cfg=AIConfig()
    monkeypatch.setattr(assistant,'config',lambda:cfg)
    monkeypatch.setattr(assistant,'secret',lambda _: 'test-provider-secret')
    with TestClient(app) as client:
        assert client.post('/api/research',json={'goal':'Analyze a real TESS target'}).status_code==400
        cfg.model='test-cloud-model'
        assert client.post('/api/research',json={'goal':'Analyze a real TESS target'}).status_code==400
        cfg.input_usd_per_million=1
        cfg.output_usd_per_million=2
        cfg.api_key='test-provider-secret'
        response=client.post('/api/research',json={'goal':'Analyze a real TESS target'})
        assert response.status_code==200
        ident=response.json()['id']
        assert 'test-provider-secret' not in response.text
        assert 'api_key' not in response.json()['params']['provider_config']
        view=client.get('/api/research/'+ident).json()
        assert view['status']=='queued' and view['plan'] is None
        assert 'provider_config' not in view
        assert client.post(f'/api/jobs/{ident}/cancel').json()['status']=='cancelled'
        assert client.post(f'/api/jobs/{ident}/resume').json()['status']=='queued'
        assert 'No final model assessment' in client.get(f'/api/research/{ident}/report').text


def test_plan_and_review_are_required_before_completion(local_job):
    state=research.initial(local_job)
    with pytest.raises(ValueError,match='Save a scientific plan'):
        research.execute_call(state,call('finish_research',CONCLUSION),state['id'])
    research.execute_call(state,call('save_research_plan',PLAN),state['id'])
    state['actions']=[{'name':'save_research_plan','status':'completed'}]
    with pytest.raises(ValueError,match='plan alone'):
        research.execute_call(state,call('finish_research',CONCLUSION),state['id'])
    state['investigation_ids']=['a'*16]
    with pytest.raises(ValueError,match='Read the completed'):
        research.execute_call(state,call('finish_research',CONCLUSION),state['id'])


def test_text_only_model_cannot_claim_completed_work(local_job,monkeypatch):
    monkeypatch.setattr(assistant,'turn',lambda *_:({'role':'assistant','content':'I finished everything.'},[],{}))
    result=research.run(local_job['id'])
    assert result['outcome']=='iteration_limit'
    assert result['model_calls']==20
    assert research.public_state(local_job['id'])['conclusion'] is None


def test_budget_stops_before_billable_request(local_job,monkeypatch):
    state=research.initial(local_job)
    state['provider_config'].update(provider='openai',input_usd_per_million=100,output_usd_per_million=100)
    state['request']['budget_usd']=.01
    research.save(state)
    monkeypatch.setattr(assistant,'secret',lambda _:'test')
    monkeypatch.setattr(assistant,'turn',lambda *_:pytest.fail('Billable request was made'))
    result=research.run(local_job['id'])
    assert result['outcome']=='budget_limit' and result['model_calls']==0


def test_input_allowance_is_cumulative_and_resumable(tmp_path):
    path=tmp_path/'allowance.json'
    allowance=InputAllowance(path,100)
    with allowance.activate():
        assert current.get() is allowance
        assert allowance.reserve('first',60,100)==60
        allowance.settle('first',55)
        assert allowance.reserve('second',40,100)==40
        with pytest.raises(ValueError,match='exhausted'):
            allowance.reserve('third',10,100)
    assert current.get() is None
    resumed=InputAllowance(path,100)
    assert resumed.reserve('first',55,100)==55  # Replay is not charged twice.
    assert sum(resumed.files.values())==95
    with pytest.raises(ValueError,match='exceeded'):
        resumed.settle('second',41)


def test_resource_limits_and_replay_keep_stable_artifact(local_job,monkeypatch):
    state=research.initial(local_job)
    state['request'].update(max_targets=1,max_operations=1)
    research.execute_call(state,call('save_research_plan',PLAN),state['id'])
    seen=[]
    def interrupted(cfg,job_id,artifact_id):
        seen.append(artifact_id)
        raise pipeline.Cancelled()
    monkeypatch.setattr(pipeline,'analyze',interrupted)
    for _ in range(2):
        with pytest.raises(pipeline.Cancelled):
            research.execute_call(state,call('analyze_target',{'target':'Synthetic A'},'analysis1'),state['id'])
        state=json.loads(research.state_path(state['id']).read_text())
    assert seen[0]==seen[1] and len(state['operations'])==1
    with pytest.raises(ValueError,match='Target limit'):
        research.execute_call(state,call('analyze_target',{'target':'Synthetic B'},'analysis2'),state['id'])
    with pytest.raises(ValueError,match='Scientific-operation limit'):
        research.execute_call(state,call('analyze_target',{'target':'Synthetic A'},'analysis2'),state['id'])


def test_automated_loop_executes_real_numerics_reviews_and_resumes(local_job,monkeypatch,isolated_store):
    # Only I/O and model transport are stubbed. Preprocessing, BLS, fits,
    # validation, classification, saved artifacts and evidence reads are real.
    t=np.arange(0,26,.01)+2457000
    rng=np.random.default_rng(21)
    f=1+rng.normal(0,.0003,len(t))
    f[np.abs(science.phase(t,3.18,2457000.8))<.065]-=.003
    product={'uri':'synthetic://test','sector':1,'exptime':120,'target_name':'synthetic'}
    monkeypatch.setattr(pipeline.archive,'search',lambda *_:{'products':[product]})
    monkeypatch.setattr(pipeline.archive,'download',lambda *_:(isolated_store/'synthetic.fits',{'bytes':1024,'uri':'synthetic://test'}))
    monkeypatch.setattr(pipeline.archive,'read_lightcurve',lambda *_:(t,f,np.full(len(t),.0003),np.ones(len(t)),{'tic_id':None,'ra':90.,'dec':-66.,'stellar_radius_solar':1.}))
    monkeypatch.setattr(pipeline.archive,'catalogs',lambda *_:{'records':{k:{'status':'complete','rows':[]} for k in ['confirmed','toi']}})
    iid=hashlib.sha256((local_job['id']+'analysis1').encode()).hexdigest()[:16]
    sequence=[call('save_research_plan',PLAN),call('analyze_target',{'target':'Synthetic test','min_period':1,'max_period':6,'max_signals':1},'analysis1'),
              call('read_investigation',{'investigation_id':iid}),call('finish_research',{**CONCLUSION,'investigation_ids':[iid]})]
    count=0
    def turn(cfg,transcript,specs):
        nonlocal count
        if count==2:
            count+=1
            raise ConnectionError('Simulated provider interruption after science saved')
        index=count if count<2 else count-1
        count+=1
        c=sequence[index]
        if c['name']=='finish_research':
            evidence=json.loads(transcript[-1]['content'])
            assert abs(evidence['signals'][0]['period']/3.18-1)<.002
        return {'role':'assistant','content':'Executing the test plan.'},[c],{}
    monkeypatch.setattr(assistant,'turn',turn)
    with pytest.raises(ConnectionError):
        research.run(local_job['id'])
    assert len(store.investigations())==1
    result=research.run(local_job['id'])
    assert result['outcome']=='completed' and result['investigation_ids']==[iid]
    assert len(store.investigations())==1
    view=research.public_state(local_job['id'])
    assert view['reviewed_ids']==[iid] and view['operations_used']==1
    assert view['findings'][0]['parameters']['max_period']==6
    assert len(view['actions'])==4
    assert iid in research.report(local_job['id'])
    assert research.run(local_job['id'])==result
