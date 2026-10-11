"""Real MCP protocol sessions against the real API and an isolated durable store."""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from exodiscovery import archive, execute, pipeline, store
from exodiscovery.api import app
from exodiscovery.mcp_server import build_server, local_url


def test_mcp_protocol_validated_tools_queue_poll_cancel_resume_and_quota(isolated_store,monkeypatch):
    monkeypatch.setattr(archive,'search',lambda target,mission:{'target':target,'mission':mission,'products':[],'source':'offline-test'})
    async def check():
        server=build_server(max_jobs=3,transport=httpx.ASGITransport(app=app))
        async with create_connected_server_and_client_session(server) as client:
            listing=await client.list_tools()
            names={t.name for t in listing.tools}
            assert {'analyze_target','discover_targets','read_investigation','get_plot_data','control_job'}<=names
            assert not any('key' in n or 'settings' in n or 'shell' in n for n in names)
            assert next(t for t in listing.tools if t.name=='analyze_target').annotations.readOnlyHint is False
            status=await client.call_tool('workbench_status',{})
            assert status.structuredContent['health']['status']=='ok'
            invalid=await client.call_tool('analyze_target',{'config':{'target':'test','window_days':.2}})
            assert invalid.isError and not store.jobs()
            too_large=await client.call_tool('analyze_target',{'config':{'target':'test','max_products':4}})
            assert too_large.isError and not store.jobs()
            queued=await client.call_tool('search_observations',{'config':{'target':'offline fixture'}})
            ident=queued.structuredContent['job_id']
            assert queued.structuredContent['status']=='queued'
            assert store.get_job(ident)['kind']=='search'
            execute.run(ident)
            complete=await client.call_tool('wait_for_job',{'job_id':ident,'seconds':1})
            assert complete.structuredContent['status']=='completed'
            assert complete.structuredContent['result']['source']=='offline-test'
            second=await client.call_tool('analyze_target',{'config':{'target':'fixture'}})
            sid=second.structuredContent['job_id']
            cancelled=await client.call_tool('control_job',{'job_id':sid,'action':'cancel'})
            assert cancelled.structuredContent['status']=='cancelled'
            resumed=await client.call_tool('control_job',{'job_id':sid,'action':'resume'})
            assert resumed.structuredContent['status']=='queued'
            cap=await client.call_tool('search_observations',{'config':{'target':'third'}})
            assert cap.isError and len(store.jobs())==2
            # Cancellation and reads remain possible after the submission quota.
            assert not (await client.call_tool('control_job',{'job_id':sid,'action':'cancel'})).isError
            assert (await client.call_tool('get_job',{'job_id':'../../settings/ai'})).isError
            private=store.create_job('chat',{'conversation_id':'a'*16,'text':'Private conversation'})
            assert (await client.call_tool('get_job',{'job_id':private['id']})).isError
            jobs=(await client.call_tool('list_jobs',{})).structuredContent['jobs']
            assert all(j['kind']!='chat' for j in jobs)
            resource=await client.read_resource('exodiscovery://guide')
            assert 'Never invent' in resource.contents[0].text
            prompt=await client.get_prompt('investigate',{'goal':'Recover WASP-18'})
            assert 'Recover WASP-18' in prompt.messages[0].content.text
    asyncio.run(check())


def test_mcp_evidence_resources_and_numeric_pagination(isolated_store,monkeypatch):
    ident='a'*16
    record={'id':ident,'target':'explicit offline fixture','created':store.now(),'signals':[],
            'star':{},'cadences':4000,'baseline_days':20,'limitations':['Test only.'],'config':{'max_period':5}}
    store.save_investigation(record,ident)
    monkeypatch.setattr(pipeline,'plots',lambda *_:{'time':list(range(4000)),'normalized':[1.]*4000,
        'flux':[.999]*4000,'error':[.001]*4000,'sector':[1]*4000,'total_points':9000})
    # API validates signal count before retrieving arrays.
    record['signals']=[{'index':0,'period':3.,'depth_ppm':1000,'depth_snr':5,'classification':'Insufficient evidence',
                       'fit':{'flux':[1]*500,'phase_days':[0]*500,'status':'fixture'}}]
    store.save_investigation(record,ident)
    async def check():
        async with create_connected_server_and_client_session(build_server(transport=httpx.ASGITransport(app=app))) as client:
            evidence=await client.call_tool('read_investigation',{'investigation_id':ident})
            assert not evidence.isError
            assert 'flux' not in evidence.structuredContent['signals'][0]['fit']
            page=await client.call_tool('get_plot_data',{'investigation_id':ident,'series':'lightcurve','offset':100,'limit':20})
            assert page.structuredContent['arrays']['time']==list(range(100,120))
            assert page.structuredContent['next_offset']==120
            assert page.structuredContent['total_original_cadences']==9000
            assert (await client.call_tool('get_plot_data',{'investigation_id':ident,'limit':100000})).isError
            resource=await client.read_resource('exodiscovery://investigations/'+ident)
            assert json.loads(resource.contents[0].text)['config']['max_period']==5
            report=await client.call_tool('get_report',{'investigation_id':ident})
            assert report.structuredContent['links']['reproduction_zip'].endswith('/'+ident+'/export')
    asyncio.run(check())


def test_mcp_discovery_runs_in_durable_worker(isolated_store,monkeypatch):
    from exodiscovery import discovery
    monkeypatch.setattr(discovery,'discover',lambda cfg,notify:{'query':cfg.model_dump(),'targets':[],'fixture':True})
    async def check():
        async with create_connected_server_and_client_session(build_server(transport=httpx.ASGITransport(app=app))) as client:
            queued=await client.call_tool('discover_targets',{'config':{'region':'south_pole','radius_degrees':.5}})
            ident=queued.structuredContent['job_id']
            assert store.get_job(ident)['result'] is None
            execute.run(ident)
            result=(await client.call_tool('get_job',{'job_id':ident})).structuredContent
            assert result['status']=='completed'
            assert result['result']['query']['radius_degrees']==.5
    asyncio.run(check())


def test_mcp_stdio_subprocess_initialization_tools_resources_prompt_from_any_cwd(tmp_path):
    script=Path(__file__).resolve().parents[2]/'mcp_server.py'
    async def check():
        params=StdioServerParameters(command=sys.executable,args=[str(script)],cwd=str(tmp_path),
                                     env={**os.environ,'EXO_DATA_DIR':str(tmp_path)})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                result=await session.initialize()
                assert result.serverInfo.name=='EXODISCOVERY'
                assert len((await session.list_tools()).tools)==17
                assert (await session.list_prompts()).prompts[0].name=='investigate'
                assert 'workflow' in (await session.read_resource('exodiscovery://guide')).contents[0].text
    asyncio.run(check())


@pytest.mark.parametrize('url',['https://example.com','http://127.0.0.1/api','http://user:secret@localhost','http://localhost?x=1','http://0.0.0.0:8765'])
def test_mcp_backend_restricted_to_loopback_origins(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_mcp_http_dns_rebinding_and_origin_guard():
    from fastapi.testclient import TestClient
    server=build_server()
    with TestClient(server.streamable_http_app(),base_url='http://127.0.0.1:8766') as client:
        headers={'Accept':'application/json, text/event-stream'}
        assert client.post('/mcp',headers={**headers,'Host':'evil.example'},json={}).status_code==421
        assert client.post('/mcp',headers={**headers,'Origin':'https://evil.example'},json={}).status_code==403
