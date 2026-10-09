"""Prompt-driven, checkpointed research runs with deterministic tools and hard bounds."""
import hashlib
import html
import json
import re

from . import assistant, archive, discovery, pipeline, store
from .allowance import InputAllowance
from .schemas import AIConfig, Analysis, CandidateRef, Search, TargetDiscovery, ResearchGoal, ResearchPlan, ResearchConclusion

INSTRUCTIONS = """
AUTOMATED RESEARCH MODE: The user supplies a scientific goal, not analysis code or required parameter choices.
Act on the goal autonomously within the supplied limits. Do not just give instructions or ask the user to select
routine parameters. Use save_research_plan first, then run tools, review actual results, and finish_research.
For vague requests about the 'most likely region', explain that no global planet-yield optimum is established.
Compare bounded north/south ecliptic-pole cones with discover_targets, or choose a justified custom cone.
Use catalog stellar properties and actual SPOC sector coverage as observing/sensitivity proxies, not planet odds.
Do not call a target uncataloged until catalog tests ran, and never claim exhaustive literature novelty.
For named targets use search_observations. Choose target IDs only from returned archive/catalog data or the user's
explicit target. Choose astronomical parameters yourself. Start with one or two adjacent sectors and a short-period
search suitable for the observed baseline. A broad multi-year search may hit the period-grid cap; inspect that flag.
You can set sectors, period/duration ranges, detrending, quality, window and max_signals through analyze_target.
Explain your choices in the plan or brief progress text. Keep the trend window >=5 times the longest duration.
For unknown stars, run detection, read_investigation for EACH completed analysis, assess every signal's tests,
and use focused follow-up tools if justified and affordable. A known-positive recovery is a control, not a discovery.
Do not use all operations on blind searches: reserve room for evidence review and useful false-positive checks.
Update the plan if results change the strategy. Tool failures are evidence of missing information, not non-detections.
Finish with finish_research: summarize work actually done, strongest evidence for/against, unresolved alternatives,
limitations, and sensible next steps. Include the IDs of reviewed investigations. Never say an incomplete survey
was completed or that no planet exists because a limited search found nothing. The runtime records real outputs.
"""
READ_ONLY = {'search_observations','query_stellar_catalog','read_investigation','list_investigations','generate_report'}
SCIENCE = {'analyze_target','compare_period','nearby_sources','inspect_pixels','injection_recovery'}


def state_path(ident):
    if not re.fullmatch(r'[a-f0-9]{16}',ident):
        raise ValueError('Invalid research run ID')
    return store.DATA/'artifacts'/ident/'research_state.json'


def save(state):
    state['updated'] = store.now()
    state_path(state['id']).parent.mkdir(parents=True,exist_ok=True)
    pipeline.atomic_json(state_path(state['id']),json.loads(assistant.redact(json.dumps(state))))


def preflight(cfg):
    assistant.endpoint(cfg)
    if not cfg.model.strip():
        raise ValueError('Choose a model in AI settings before starting automated research')
    if cfg.provider!='local':
        if not assistant.secret(cfg):
            raise ValueError('Save your provider API key in AI settings before starting automated research')
        if cfg.input_usd_per_million<=0 or cfg.output_usd_per_million<=0:
            raise ValueError('Enter your model token prices in AI settings so the spending guard can operate')


def create(goal: ResearchGoal):
    cfg = assistant.config()
    preflight(cfg)
    payload = goal.model_dump()
    payload['goal'] = assistant.redact(payload['goal'])
    payload['provider_config'] = cfg.model_dump(exclude={'api_key','clear_key'})
    return store.create_job('research',payload)


def tools():
    extras = [('save_research_plan','Save or revise the scientific strategy and intended steps.',ResearchPlan),
              ('discover_targets','Query TIC and real SPOC observing coverage in bounded sky cones. Returns observing proxies, not planet probabilities.',TargetDiscovery),
              ('finish_research','Save the final evidence assessment. Every newly analyzed investigation must first be read with read_investigation.',ResearchConclusion)]
    return assistant.tool_specs()+[{'name':n,'description':d,'parameters':s.model_json_schema()} for n,d,s in extras]


def initial(job):
    goal = {k:v for k,v in job['params'].items() if k!='provider_config'}
    return {'id':job['id'],'created':store.now(),'updated':store.now(),'request':goal,
            'provider_config':job['params']['provider_config'],'phase':'Planning','plan':None,'plans':[],
            'model_calls':0,'reserved_usd':0.,'estimated_usd':0.,'operations':{},'targets':[],
            'investigation_ids':[],'reviewed_ids':[],'discoveries':[],'actions':[],'pending':[],
            'transcript':[{'role':'user','content':INSTRUCTIONS+'\nGoal and limits:\n'+json.dumps(goal)}],
            'outcome':None,'conclusion':None}


def execute_call(state,call,job_id):
    name = call['name']
    args = json.loads(call['arguments'])
    limits = ResearchGoal(**state['request'])
    if name=='save_research_plan':
        plan=ResearchPlan(**args).model_dump()
        state['plan']=plan
        state['plans'].append({'time':store.now(),**plan})
        state['phase']='Selecting targets'
        save(state)
        return {'status':'saved','plan':plan}
    if not state['plan']:
        raise ValueError('Save a scientific plan before using research tools')
    if name=='finish_research':
        conclusion=ResearchConclusion(**args).model_dump()
        for ident in conclusion['investigation_ids']:
            if ident not in state['reviewed_ids']:
                raise ValueError('Conclusion refers to an investigation not read during this run')
        missing=set(state['investigation_ids'])-set(state['reviewed_ids'])
        if missing:
            raise ValueError('Read the completed investigations before finishing: '+', '.join(sorted(missing)))
        if not any(a['name'] not in {'save_research_plan','finish_research'} and a['status']=='completed' for a in state['actions']):
            raise ValueError('Execute real tools before finishing; a plan alone is not a research run')
        state['conclusion']=conclusion
        state['outcome']='completed' if state['investigation_ids'] else 'no_new_analysis'
        state['phase']='Assessment saved'
        save(state)
        return {'status':state['outcome'],'report_url':f'/api/research/{job_id}/report'}
    if name=='discover_targets':
        cfg=TargetDiscovery(**args)
        if sum(a['name']=='discover_targets' for a in state['actions'])>=4:
            raise ValueError('Four regional catalog comparisons per run are allowed')
        state['phase']='Comparing regions and selecting stars'
        result=discovery.discover(cfg,lambda msg:pipeline.checkpoint(job_id,msg))
        state['discoveries'].append(result)
        save(state)
        return result
    if name not in READ_ONLY | SCIENCE:
        raise ValueError('Unknown automated research tool')
    if name in SCIENCE:
        # Validate before reserving scarce work, then persist a stable operation ID.
        if name=='analyze_target':
            spec=Analysis(**args)
            normalized=spec.target.strip().lower()
            if normalized not in state['targets'] and len(state['targets'])>=limits.max_targets:
                raise ValueError('Target limit reached; inspect existing results instead')
        else:
            from .schemas import Injection, Alias
            cls=Injection if name=='injection_recovery' else Alias if name=='compare_period' else CandidateRef
            cls(**args)
        if call['id'] not in state['operations']:
            if len(state['operations'])>=limits.max_operations:
                raise ValueError('Scientific-operation limit reached; read results and finish the assessment')
            state['operations'][call['id']]={'name':name,'artifact_id':hashlib.sha256((job_id+call['id']).encode()).hexdigest()[:16]}
        if name=='analyze_target' and normalized not in state['targets']:
            state['targets'].append(normalized)
        state['phase']='Analyzing observations' if name=='analyze_target' else 'Testing alternative explanations'
        save(state)
        if name=='analyze_target':
            spec.max_products=min(spec.max_products,3)
            spec.max_download_mb=min(spec.max_download_mb,300,limits.max_input_mb)
            result=pipeline.analyze(spec,job_id,artifact_id=state['operations'][call['id']]['artifact_id'])
            if result['id'] not in state['investigation_ids']:
                state['investigation_ids'].append(result['id'])
            save(state)
            return {**pipeline.summary(result),'actual_parameters':result['config']}
        kinds={'compare_period':'alias','nearby_sources':'gaia','inspect_pixels':'pixels','injection_recovery':'injection'}
        return pipeline.followup(args,kinds[name],job_id,operation_id=call['id'])
    state['phase']='Reviewing computed evidence'
    result=assistant.execute_tool(name,args,job_id,{'analyses':0},AIConfig(**state['provider_config']))
    if name=='read_investigation':
        ident=CandidateRef(**args).investigation_id
        if ident not in state['reviewed_ids']:
            state['reviewed_ids'].append(ident)
        save(state)
    return result


def run(job_id):
    job=store.get_job(job_id)
    path=state_path(job_id)
    path.parent.mkdir(parents=True,exist_ok=True)
    state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else initial(job)
    if state['outcome']:
        return result_summary(state)
    cfg=AIConfig(**state['provider_config'])
    preflight(cfg)
    limits=ResearchGoal(**state['request'])
    cfg.max_iterations=limits.max_model_calls
    cfg.budget_usd=limits.budget_usd
    specs=tools()
    allowance=InputAllowance(path.parent/'input_allowance.json',limits.max_input_mb*1024**2)
    save(state)
    while state['model_calls']<limits.max_model_calls or state['pending']:
        pipeline.checkpoint(job_id,state['phase'])
        if len(state['pending'])>8:
            state['outcome']='tool_limit'
            break
        while state['pending']:
            call=state['pending'][0]
            if any(a['call_id']==call['id'] for a in state['actions']):
                raise ValueError('Provider reused a tool call identifier; refusing ambiguous replay')
            pipeline.checkpoint(job_id,'Automated tool: '+call['name'])
            action={'call_id':call['id'],'name':call['name'],'started':store.now(),
                    'arguments':assistant.redact(call['arguments'])}
            try:
                with allowance.activate():
                    output=execute_call(state,call,job_id)
                action['status']='completed'
            except pipeline.Cancelled:
                raise
            except Exception as exc:
                output={'error':assistant.redact(f'{type(exc).__name__}: {exc}')}
                action['status']='failed'
            output=json.loads(assistant.redact(json.dumps(output)))
            action.update(finished=store.now(),result=output)
            state['actions'].append(action)
            state['transcript'].append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(output)})
            state['pending'].pop(0)
            save(state)
            if state['outcome']:
                # Completion is terminal; ignore any subsequently proposed actions.
                state['pending']=[]
                save(state)
                return result_summary(state)
        if state['model_calls']>=limits.max_model_calls:
            state['outcome']='iteration_limit'
            break
        input_bound=len(json.dumps(state['transcript']).encode())+len(json.dumps(specs).encode())+len(assistant.SYSTEM.encode())+20000
        reserve=(input_bound*cfg.input_usd_per_million+cfg.max_output_tokens*cfg.output_usd_per_million)/1e6
        if cfg.provider!='local' and state['reserved_usd']+reserve>limits.budget_usd:
            state['outcome']='budget_limit'
            break
        state['reserved_usd']+=reserve
        state['model_calls']+=1
        save(state)  # Interrupted requests consume their reserved allowance.
        pipeline.checkpoint(job_id,f"AI research call {state['model_calls']}/{limits.max_model_calls}: {state['phase']}")
        msg,calls,usage=assistant.turn(cfg,state['transcript'],specs)
        msg=json.loads(assistant.redact(json.dumps(msg)))
        state['estimated_usd']+=(usage.get('input_tokens',usage.get('prompt_tokens',0))*cfg.input_usd_per_million+usage.get('output_tokens',usage.get('completion_tokens',0))*cfg.output_usd_per_million)/1e6
        state['transcript'].append(msg)
        state['pending']=json.loads(assistant.redact(json.dumps(calls)))
        if not calls:
            state['transcript'].append({'role':'user','content':'This is an automated run. Continue using tools to execute/review the plan, or call finish_research with the evidence and limitations. Text alone does not mark work complete.'})
        save(state)
    state['outcome']=state['outcome'] or 'iteration_limit'
    state['phase']='Stopped at configured limit; completed evidence retained'
    save(state)
    return result_summary(state)


def result_summary(state):
    return {'research_id':state['id'],'outcome':state['outcome'],'investigation_ids':state['investigation_ids'],
            'model_calls':state['model_calls'],'estimated_cost_usd':state['estimated_usd'],
            'report_url':f"/api/research/{state['id']}/report"}


def public_state(ident):
    job=store.get_job(ident)
    if not job or job['kind']!='research':
        raise ValueError('Research run not found')
    path=state_path(ident)
    state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else initial(job)
    value={k:state[k] for k in ['id','created','updated','request','phase','plan','plans','model_calls','reserved_usd',
                               'estimated_usd','targets','investigation_ids','reviewed_ids','discoveries','actions','outcome','conclusion']}
    value['operations_used']=len(state['operations'])
    value['provider']={k:state['provider_config'][k] for k in ['provider','model']}
    value['status'],value['error']=job['status'],job['error']
    value['progress_text']=job['stage']
    ledger=path.parent/'input_allowance.json'
    value['input_mb']=sum(json.loads(ledger.read_text()).values())/1024**2 if ledger.exists() else 0
    value['notes']=[{'text':m['content'],'role':m['role']} for m in state['transcript'] if m['role']=='assistant' and m.get('content')]
    value['findings']=[]
    for iid in state['investigation_ids']:
        try:
            r=store.investigation(iid)
            value['findings'].append({'id':iid,'target':r['target'],'parameters':r['config'],
                'signals':[{k:s[k] for k in ['index','period','depth_ppm','depth_snr','classification']} for s in r['signals']]})
        except ValueError:
            value['findings'].append({'id':iid,'error':'Investigation record unavailable'})
    return value


def report(ident):
    r=public_state(ident)
    esc=lambda x:html.escape(str(x))
    conclusion=r.get('conclusion') or {'assessment':'No final model assessment was completed. The recorded actions and saved numerical results below are the available evidence.',
                                      'limitations':['Run was queued, interrupted, failed or stopped at a configured limit.'],'next_steps':[]}
    rows=[]
    for f in r['findings']:
        for s in f.get('signals',[]):
            rows.append(f"<tr><td><a href='/api/investigations/{f['id']}/report'>{esc(f['target'])}</a></td><td>{s['period']:.7f}</td><td>{s['depth_ppm']:.1f}</td><td>{esc(s['classification'])}</td></tr>")
    return "<!doctype html><html lang='en'><meta charset='utf-8'><title>Exoplanet Search Workbench: automated research</title><style>body{max-width:1000px;margin:40px auto;padding:20px;font:15px system-ui;color:#182b38}p,pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}table{width:100%;border-collapse:collapse}td,th{padding:12px;border-bottom:1px solid #ccc;text-align:left}pre{font-size:11px;background:#f1f5f7;padding:16px}a{color:#176b79}</style><body>"+f"<h1>Automated research</h1><p>{esc(r['request']['goal'])}</p><p>Run {ident} · {esc(r['outcome'] or r['status'])} · {esc(r['created'])}</p><h2>Strategy</h2><p>{esc((r['plan'] or {}).get('strategy','No plan saved'))}</p><h2>Assessment</h2><p>{esc(conclusion['assessment'])}</p><h2>Computed findings</h2><table><tr><th>Investigation</th><th>Period (days)</th><th>Depth (ppm)</th><th>Classification</th></tr>{''.join(rows)}</table><h2>Limitations</h2><ul>"+''.join('<li>'+esc(x)+'</li>' for x in conclusion['limitations'])+"</ul><h2>Next steps</h2><ul>"+''.join('<li>'+esc(x)+'</li>' for x in conclusion['next_steps'])+"</ul><h2>Complete run record</h2><pre>"+esc(json.dumps(r,indent=2))+"</pre></body></html>"
