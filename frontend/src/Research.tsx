import { useEffect, useState, type FormEvent } from 'react'
import { Play, Square, RefreshCw, Settings2, ExternalLink, ArrowRight, LoaderCircle } from 'lucide-react'

type Data = Record<string, any>
async function api(path:string, method='GET', body?:unknown) {
  const response=await fetch('/api'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined})
  const data=await response.json()
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail))
  return data
}
const examples=[
  ['Compare sky regions','Analyze the most likely region of the sky for exoplanets. Compare TESS coverage around both ecliptic poles, select promising small, bright stars, and investigate up to 3 targets. Explain the selection bias and check any signals against known planets and TOIs.'],
  ['Recover a known planet','Analyze WASP-18 in TESS sector 2 as a known-positive control. Choose appropriate transit search parameters, recover its signal, review the validation tests and catalog match, and explain the evidence and limitations.'],
  ['Investigate a target','Analyze TIC 4206066 for periodic transit signals. Choose the observations and search parameters, evaluate alternative periods and false-positive explanations, and report only what the computed evidence supports.'],
]
export default function Research({open,settings}:{open:(id:string)=>void;settings:()=>void}) {
  const [goal,setGoal]=useState('')
  const [limits,setLimits]=useState({max_targets:3,max_operations:10,max_model_calls:20,max_input_mb:600,timeout_seconds:3600,budget_usd:1})
  const [runs,setRuns]=useState<Data[]>([])
  const [selected,setSelected]=useState<string|null>(null)
  const [record,setRecord]=useState<Data|null>(null)
  const [config,setConfig]=useState<Data|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  useEffect(()=>{api('/settings/ai').then(c=>{setConfig(c);setLimits(v=>({...v,budget_usd:c.budget_usd}))}).catch(e=>setError(e.message))},[])
  useEffect(()=>{let active=true;async function poll(){try{const r=await api('/research');if(active){setRuns(r);setSelected(old=>old||r[0]?.id||null)}}catch(e){if(active)setError((e as Error).message)}}void poll();const timer=setInterval(()=>void poll(),2500);return()=>{active=false;clearInterval(timer)}},[])
  useEffect(()=>{let active=true;setRecord(null);if(!selected)return;async function poll(){try{const r=await api('/research/'+selected);if(active)setRecord(r)}catch(e){if(active)setError((e as Error).message)}}void poll();const timer=setInterval(()=>void poll(),2500);return()=>{active=false;clearInterval(timer)}},[selected])
  async function start(event:FormEvent){event.preventDefault();setError('');setBusy(true);try{const job=await api('/research','POST',{goal,...limits});setSelected(job.id);setRuns(await api('/research'))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function control(action:string){setBusy(true);setError('');try{await api(`/jobs/${selected}/${action}`,'POST');setRecord(await api('/research/'+selected))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  const running=record&&['queued','running'].includes(record.status)
  const resumable=record&&['failed','cancelled','interrupted'].includes(record.status)&&!record.outcome
  const ready=config?.model&&(config.provider==='local'||config.key_configured)
  const update=(key:keyof typeof limits,value:number)=>setLimits(previous=>({...previous,[key]:value}))
  return <div className="page automated-page">
    <div className="page-heading"><h1>Automated research</h1><button onClick={settings}><Settings2 size={14}/>{config?.model?`${config.provider} / ${config.model}`:'Configure AI'}</button></div>
    {error&&<div role="alert" className="alert error">{error}</div>}
    <div className="automation-layout"><section className="panel goal-panel"><div className="panel-head"><h3>Research goal</h3></div><form className="panel-body" onSubmit={e=>void start(e)}>
      <label htmlFor="research-goal">What should the AI investigate?</label><textarea id="research-goal" rows={6} maxLength={12000} minLength={8} required value={goal} onChange={e=>setGoal(e.target.value)} placeholder="Analyze the most likely region of the sky for exoplanets…"/>
      <div className="preset-row">{examples.map(([label,text])=><button type="button" key={label} onClick={()=>setGoal(text)}>{label}</button>)}</div>
      <p className="small">The AI selects targets, observations and search parameters, runs the scientific tools, and assesses the saved results.</p>
      <details><summary>Run limits</summary><div className="form-grid">
        <label>Targets<input type="number" min={1} max={30} required value={limits.max_targets} onChange={e=>update('max_targets',+e.target.value)}/></label>
        <label>Scientific operations<input type="number" min={1} max={80} required value={limits.max_operations} onChange={e=>update('max_operations',+e.target.value)}/></label>
        <label>AI calls<input type="number" min={3} max={80} required value={limits.max_model_calls} onChange={e=>update('max_model_calls',+e.target.value)}/></label>
        <label>API budget (USD)<input type="number" min={0} max={100} step={0.1} required value={limits.budget_usd} onChange={e=>update('budget_usd',+e.target.value)}/></label>
        <label>Input files (MB)<input type="number" min={10} max={5000} required value={limits.max_input_mb} onChange={e=>update('max_input_mb',+e.target.value)}/></label>
        <label>Minutes per attempt<input type="number" min={2} max={240} required value={limits.timeout_seconds/60} onChange={e=>update('timeout_seconds',+e.target.value*60)}/></label>
      </div><p className="small">One scientific process at a time. Analyses and follow-up tests each use one operation. Distinct input files, including cached files, count toward the data allowance. API reservations use the token prices you enter; they are conservative estimates, not provider billing.</p></details>
      {!ready&&<p className="setup-note">Select a model and save your API key in AI settings, or connect a local model that supports tools.</p>}
      <button className="primary start-research" type="submit" disabled={busy||!ready||goal.trim().length<8}>{busy?<LoaderCircle size={15} className="spin"/>:<Play size={15}/>}Start research</button>
      <details><summary>How to use this mode</summary><ol className="research-help"><li>Configure a provider, model and token prices in AI settings. Use Test connection.</li><li>Describe your goal and set the run limits. The AI chooses the astronomical parameters.</li><li>Follow the plan and tool results here. Open each investigation to inspect plots, validation tests and original data.</li></ol><p className="small">A “best region” request compares bounded sky areas using observing coverage, stellar brightness and size. It does not establish a global optimum or a probability that a planet exists.</p></details>
    </form></section>
    <div className="automation-results"><div className="run-picker"><label htmlFor="research-run">Saved runs</label><select id="research-run" value={selected||''} onChange={e=>setSelected(e.target.value)}><option value="" disabled>{runs.length?'Select a run':'No automated runs yet'}</option>{runs.map(r=><option value={r.id} key={r.id}>{new Date(r.created).toLocaleString()} · {r.goal.slice(0,70)} · {r.outcome||r.status}</option>)}</select></div>
      {!record?<div className="panel panel-body muted-text">The investigation plan, selected stars and computed evidence will appear here after you start a run.</div>:<>
        <section className="panel"><div className="panel-head"><h3>{record.outcome?record.outcome.replaceAll('_',' '):record.status}</h3><div className="button-row run-controls">{running&&<button disabled={busy} onClick={()=>void control('cancel')}><Square size={12}/>Stop</button>}{resumable&&<button disabled={busy} onClick={()=>void control('resume')}><RefreshCw size={12}/>Resume</button>}<a className="button" target="_blank" rel="noreferrer" href={`/api/research/${selected}/report`}><ExternalLink size={12}/>Report</a><a className="button" href={`/api/research/${selected}/json`}>JSON</a></div></div><div className="panel-body">
          <p className="research-question">{record.request.goal}</p><div role="status" className="research-stage">{running&&<LoaderCircle size={14} className="spin"/>}{record.progress_text||record.phase}</div>{record.error&&<div className="alert error">{record.error}</div>}
          <div className="run-usage">{[['Targets',record.targets.length,record.request.max_targets],['Operations',record.operations_used,record.request.max_operations],['AI calls',record.model_calls,record.request.max_model_calls],['Input MB',record.input_mb.toFixed(1),record.request.max_input_mb]].map(([label,used,max])=><div key={label}><span>{label}</span><strong>{used} <small>/ {max}</small></strong></div>)}</div><p className="small">API usage estimate ${record.estimated_usd.toFixed(4)} · reserved ${record.reserved_usd.toFixed(4)} / ${record.request.budget_usd.toFixed(2)} · {record.provider.model}</p>
          {record.plan&&<div className="research-plan"><h3>Investigation plan</h3><p>{record.plan.strategy}</p><ol>{record.plan.steps.map((step:string,i:number)=><li key={i}>{step}</li>)}</ol><p className="small">Target selection: {record.plan.selection_reason}</p></div>}
        </div></section>
        {record.discoveries.map((d:Data,i:number)=><section className="panel" key={i}><div className="panel-head"><h3>Region comparison {i+1}</h3><span className="small">{d.ranking} · {d.query.radius_degrees}° radius</span></div><div className="panel-body">{d.regions.map((r:Data)=><div className="data-row" key={r.name}><span>{r.name}<small className="block-small">RA {r.ra}° · Dec {r.dec}°</small></span><span>{r.status==='complete'?`${r.eligible_observed_stars} eligible observed stars`:`Unavailable: ${r.error}`}</span></div>)}<div className="table-scroll"><table><thead><tr><th>Catalog target</th><th>Region</th><th>Sectors</th><th>Tmag</th><th>R☉</th></tr></thead><tbody>{d.targets.map((t:Data)=><tr key={t.tic_id+t.region}><td>{t.target}</td><td>{t.region}</td><td title={t.available_sectors.join(', ')}>{t.sector_count}</td><td>{t.tmag.toFixed(2)}</td><td>{t.radius_solar.toFixed(2)}</td></tr>)}</tbody></table></div><details><summary>Selection limits and source data</summary><ul>{d.limitations.map((l:string)=><li key={l}>{l}</li>)}</ul><pre>{JSON.stringify(d,null,2)}</pre></details></div></section>)}
        {record.findings.length>0&&<section className="panel"><div className="panel-head"><h3>Computed investigations</h3></div><div className="panel-body">{record.findings.map((f:Data)=><div className="research-finding" key={f.id}><div className="target-title"><strong>{f.target||f.id}</strong><button onClick={()=>open(f.id)}>Open investigation<ArrowRight size={12}/></button></div>{f.signals?.length?f.signals.map((s:Data)=><p key={s.index}><span className="mono">{s.period.toFixed(6)} d · {s.depth_ppm.toFixed(0)} ppm</span><br/>{s.classification}</p>):<p>No saved transit signals.</p>}<details><summary>Analysis parameters chosen by AI</summary><pre>{JSON.stringify(f.parameters,null,2)}</pre></details></div>)}</div></section>}
        {record.conclusion&&<section className="panel"><div className="panel-head"><h3>AI assessment</h3><span className="small">Interpretation of tool results</span></div><div className="panel-body"><div className="message-text">{record.conclusion.assessment}</div><h4>Limitations</h4><ul>{record.conclusion.limitations.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>{record.conclusion.next_steps.length>0&&<><h4>Next steps</h4><ul>{record.conclusion.next_steps.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></>}</div></section>}
        <section className="panel"><div className="panel-head"><h3>Tool activity</h3><span className="small">{record.actions.length} completed attempts</span></div><div className="panel-body">{!record.actions.length&&<p className="small">Waiting for the first tool call.</p>}{record.actions.map((a:Data)=><details className="research-action" key={a.call_id}><summary><span>{a.name.replaceAll('_',' ')}</span><span className={'badge '+(a.status==='failed'?'bad':'muted')}>{a.status}</span></summary><p className="small">{new Date(a.started).toLocaleString()}</p><pre>{a.arguments}</pre><pre>{JSON.stringify(a.result,null,2)}</pre></details>)}{record.notes.length>0&&<details><summary>AI progress notes</summary>{record.notes.map((n:Data,i:number)=><p className="message-text" key={i}>{n.text}</p>)}</details>}</div></section>
      </>}
    </div></div>
  </div>
}
