"""Real protocol smoke check. --recover queues a real WASP-18 analysis; no model/API key is used."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path
import sys
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connection(http):
    if http:
        async with httpx.AsyncClient(trust_env=False,timeout=60) as client:
            async with streamable_http_client('http://127.0.0.1:8766/mcp',http_client=client) as (read,write,_):
                yield read,write
    else:
        script=Path(__file__).resolve().parents[1]/'mcp_server.py'
        async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(script)])) as streams:
            yield streams


async def check(args):
    async with connection(args.http) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=60)) as client:
            init=await client.initialize()
            async def tool(name,params):
                result=await client.call_tool(name,params)
                if result.isError:
                    raise RuntimeError(result.content)
                if result.structuredContent is None:
                    raise RuntimeError('Missing structured MCP result')
                return result.structuredContent
            tools=await client.list_tools()
            status=await tool('workbench_status',{})
            print(json.dumps({'server':init.serverInfo.name,'tools':len(tools.tools),'health':status['health']}),flush=True)
            saved=await tool('list_investigations',{'limit':10})
            print('Saved investigations:',saved['total'],flush=True)
            if not args.recover:
                return
            submitted=await tool('analyze_target',{'config':{'target':'WASP-18','sectors':[2],'max_products':1,'max_signals':1}})
            ident=submitted['job_id']
            print('Queued real WASP-18 analysis:',ident,flush=True)
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                job=await tool('wait_for_job',{'job_id':ident,'seconds':10})
                print(job['status'],job['stage'],flush=True)
                if job['status'] not in ('queued','running'):
                    break
            else:
                raise RuntimeError('Still queued/running; inspect or cancel '+ident+' in the workbench')
            if job['status']!='completed':
                raise RuntimeError(job)
            iid=job['result']['id']
            evidence=await tool('read_investigation',{'investigation_id':iid})
            signal=evidence['signals'][0]
            assert abs(signal['period']/.94145223-1)<.001,signal['period']
            assert signal['classification']=='Known planet',signal['classification']
            preview=await tool('get_plot_data',{'investigation_id':iid,'series':'folded','limit':20})
            assert len(preview['arrays']['phase'])==20
            report=await tool('get_report',{'investigation_id':iid})
            print(json.dumps({'investigation':iid,'period_days':signal['period'],'classification':signal['classification'],
                              'cadences':evidence['cadences'],'report':report['links']['report']},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http',action='store_true',help='Use the separately running MCP HTTP server on port 8766')
    parser.add_argument('--recover',action='store_true',help='Queue real TESS analysis and require a known-planet recovery')
    asyncio.run(check(parser.parse_args()))
