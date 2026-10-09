from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from exodiscovery import store, pipeline, assistant
from exodiscovery.api import app
from exodiscovery.schemas import AIConfig


def test_atomic_claim_and_restart_resumption(isolated_store):
    j=store.create_job('analysis',{'target':'test'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:store.claim_job(), range(4)))
    assert len([r for r in results if r])==1
    store.recover()
    assert store.get_job(j['id'])['status']=='interrupted'
    store.update_job(j['id'],status='queued')
    assert store.claim_job()['attempts']==2


def test_cancelled_job_cannot_be_claimed(isolated_store):
    j=store.create_job('search',{'target':'x'})
    store.update_job(j['id'],cancel=1)
    assert store.claim_job() is None
    try:
        pipeline.checkpoint(j['id'],'should cancel')
        assert False
    except pipeline.Cancelled:
        pass


def test_saved_checkpoint_is_reopened_without_network(isolated_store,monkeypatch):
    from exodiscovery.schemas import Analysis
    cfg=Analysis(target='test')
    ident=store.uid()
    folder=isolated_store/'artifacts'/ident
    folder.mkdir()
    payload={'id':ident,'target':'test','created':store.now(),'signals':[]}
    pipeline.atomic_json(folder/'result.json',payload)
    monkeypatch.setattr(pipeline.archive,'search',lambda *_: (_ for _ in ()).throw(AssertionError('network was called')))
    assert pipeline.analyze(cfg,artifact_id=ident)==payload
    assert store.investigation(ident)==payload


def test_local_api_origin_host_validation_and_credential_redaction(isolated_store,monkeypatch):
    vault={}
    monkeypatch.setattr(assistant.keyring,'set_password',lambda s,k,v:vault.update({k:v}))
    monkeypatch.setattr(assistant.keyring,'get_password',lambda s,k:vault.get(k))
    with TestClient(app) as c:
        assert c.get('/api/health').status_code==200
        assert c.post('/api/analyze',json={'target':'test'},headers={'Origin':'https://evil.example'}).status_code==403
        assert c.get('/api/health',headers={'Host':'evil.example'}).status_code==400
        secret='sk-test-SECRET123456'
        r=c.put('/api/settings/ai',json={'api_key':secret,'model':'test-model'})
        assert r.status_code==200
        assert secret not in r.text
        assert r.json()['key_configured'] is True
        assert secret not in c.get('/api/settings/ai').text
        assert secret not in (isolated_store/'research.sqlite').read_bytes().decode('latin1')
        r=c.put('/api/settings/ai',json={'api_key':secret,'temperature':99})
        assert r.status_code==422
        assert secret not in r.text
        assert assistant.redact('key='+secret)=='key=[REDACTED]'


def test_local_endpoint_boundary():
    assert assistant.endpoint(AIConfig(provider='local',base_url='http://127.0.0.1:11434/v1')).endswith('/v1')
    import pytest
    with pytest.raises(ValueError):
        assistant.endpoint(AIConfig(provider='local',base_url='http://example.com/v1'))


def test_unsafe_tool_cannot_run(isolated_store):
    import pytest
    with pytest.raises(ValueError,match='Unknown tool'):
        assistant.execute_tool('execute_shell',{'command':'anything'},None,{'analyses':0},AIConfig())
