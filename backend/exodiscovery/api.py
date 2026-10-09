import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import store, pipeline, assistant, reports
from .schemas import Analysis, Search, Batch, CandidateRef, Injection, Alias, AIConfig, Chat, ResearchGoal


@asynccontextmanager
async def lifespan(app):
    store.init()
    yield


app = FastAPI(title="EXODISCOVERY", version="0.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api") and origin and origin not in ["http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:5173", "http://localhost:5173"]:
        return JSONResponse({"detail": "Cross-origin API access is disabled"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    # Pydantic errors normally echo input, which may contain credentials.
    return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}, status_code=422)


@app.exception_handler(ValueError)
async def bad_request(request, exc):
    return JSONResponse({"detail": assistant.redact(str(exc))}, status_code=400)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/dashboard")
def dashboard():
    inv = store.investigations()
    signals = [s for r in inv for s in r["signals"]]
    return {"stars_analyzed": len({r['target'].lower() for r in inv}),
            "observations": len(list((store.DATA / 'observations').glob('*.fits'))),
            "signals": len(signals), "unclassified": sum(s['classification'] == 'Interesting unclassified transit signal' for s in signals),
            "known_recovered": len({(r['star'].get('tic_id'), m['record'].get('pl_name')) for r in inv for s in r['signals'] for m in s['catalog_matches'] if m['catalog'] == 'confirmed'}),
            "cache_mb": round(sum(p.stat().st_size for p in (store.DATA/'observations').glob('*.fits'))/1024**2, 1)}


@app.get("/api/jobs")
def jobs():
    return store.jobs()


@app.get("/api/jobs/{ident}")
def job(ident: str):
    item = store.get_job(ident)
    if not item:
        raise HTTPException(404, "Job not found")
    with store.db() as c:
        item["events"] = [dict(r) for r in c.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (ident,))]
    return item


@app.post("/api/jobs/{ident}/{action}")
def control(ident: str, action: str):
    item = store.get_job(ident)
    if not item:
        raise HTTPException(404, "Job not found")
    if action == "cancel" and item["status"] in ("running", "queued"):
        store.update_job(ident, cancel=1, **({"status": "cancelled"} if item["status"] == "queued" else {}))
    elif action == "resume" and item["status"] in ("cancelled", "failed", "interrupted"):
        store.update_job(ident, cancel=0, status="queued", error=None, stage="Queued for resumption")
    else:
        raise ValueError("Action not available for this job state")
    return store.get_job(ident)


@app.post("/api/search")
def search(cfg: Search):
    return store.create_job("search", cfg.model_dump())


@app.post("/api/tic")
def tic(cfg: Search):
    return store.create_job("tic", cfg.model_dump())


@app.post("/api/analyze")
def analyze(cfg: Analysis):
    return store.create_job("analysis", cfg.model_dump())


@app.post("/api/batch")
def batch(cfg: Batch):
    targets = list(dict.fromkeys(t.strip() for t in cfg.targets if t.strip()))
    # Validate the whole batch before creating any jobs.
    configs = [Analysis(**{**cfg.config.model_dump(), "target": t}) for t in targets]
    return [store.create_job("analysis", c.model_dump()) for c in configs]


@app.get("/api/investigations")
def investigations():
    return [{k: v for k,v in r.items() if k in ("id", "target", "created", "star", "cadences", "baseline_days")} |
            {"signals": [{k:s[k] for k in ("index", "period", "depth_ppm", "depth_snr", "classification")} for s in r["signals"]]} for r in store.investigations()]


def valid_id(ident):
    import re
    if not re.fullmatch(r"[a-f0-9]{16}", ident):
        raise HTTPException(404, "Investigation not found")


@app.get("/api/investigations/{ident}")
def investigation(ident: str):
    valid_id(ident)
    return store.investigation(ident)


@app.get("/api/investigations/{ident}/plots")
def plots(ident: str, signal: int = 0):
    valid_id(ident)
    if signal < 0 or signal >= len(store.investigation(ident)["signals"]):
        raise ValueError("Signal index out of range")
    return pipeline.plots(ident, signal)


@app.get("/api/investigations/{ident}/report", response_class=HTMLResponse)
def report(ident: str):
    valid_id(ident)
    return reports.report(ident)


@app.get("/api/investigations/{ident}/export")
def export(ident: str):
    valid_id(ident)
    return FileResponse(reports.bundle(ident), filename=f"exodiscovery-{ident}.zip", media_type="application/zip")


@app.get("/api/investigations/{ident}/json")
def export_json(ident: str):
    valid_id(ident)
    return JSONResponse(store.investigation(ident), headers={"Content-Disposition": f'attachment; filename="{ident}.json"'})


@app.post("/api/followup/{kind}")
def followup(kind: str, body: dict):
    if kind not in ("gaia", "pixels", "alias", "injection"):
        raise ValueError("Unknown follow-up type")
    cls = Injection if kind == "injection" else Alias if kind == "alias" else CandidateRef
    cfg = cls(**body)
    r = store.investigation(cfg.investigation_id)
    if cfg.signal_index >= len(r["signals"]):
        raise ValueError("Signal index out of range")
    return store.create_job(kind, cfg.model_dump())


@app.get("/api/settings/ai")
def ai_settings():
    return assistant.public_config()


@app.put("/api/settings/ai")
def save_settings(cfg: AIConfig):
    return assistant.save_config(cfg)


@app.get("/api/settings/models")
def ai_models():
    return assistant.models()


@app.post("/api/settings/test")
def ai_test():
    return assistant.connection_test()


@app.get("/api/conversations")
def conversations():
    with store.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM conversations ORDER BY created DESC")]


@app.post("/api/conversations")
def new_conversation():
    ident = store.uid()
    with store.db() as c:
        c.execute("INSERT INTO conversations VALUES(?,?,?)", (ident, "Research conversation", store.now()))
    return {"id": ident}


@app.get("/api/conversations/{ident}")
def messages(ident: str):
    return store.messages(ident)


@app.post("/api/chat")
def chat(cfg: Chat):
    with store.db() as c:
        exists = c.execute("SELECT id FROM conversations WHERE id=?", (cfg.conversation_id,)).fetchone()
        if not exists:
            raise ValueError("Conversation not found")
        # Prevent overlapping provider transcripts in one conversation.
        active = c.execute("SELECT params FROM jobs WHERE kind='chat' AND status IN ('queued','running')").fetchall()
        if any(json.loads(j[0])["conversation_id"] == cfg.conversation_id for j in active):
            raise ValueError("A research message is already queued or running in this conversation")
        c.execute("UPDATE conversations SET title=? WHERE id=?", (assistant.redact(cfg.text[:70]), cfg.conversation_id))
    cfg.text = assistant.redact(cfg.text)
    store.message(cfg.conversation_id, "user", cfg.text)
    return store.create_job("chat", cfg.model_dump())


@app.post('/api/research')
def start_research(goal: ResearchGoal):
    from . import research
    return research.create(goal)


@app.get('/api/research')
def research_runs():
    return [{'id':j['id'],'goal':j['params']['goal'],'status':j['status'],'created':j['created'],
             'stage':j['stage'],'outcome':(j['result'] or {}).get('outcome')} for j in store.jobs() if j['kind']=='research']


@app.get('/api/research/{ident}')
def research_state(ident: str):
    from . import research
    return research.public_state(ident)


@app.get('/api/research/{ident}/report',response_class=HTMLResponse)
def research_report(ident: str):
    from . import research
    return research.report(ident)


@app.get('/api/research/{ident}/json')
def research_json(ident: str):
    from . import research
    return JSONResponse(research.public_state(ident),headers={'Content-Disposition':f'attachment; filename="research-{ident}.json"'})


dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="workbench")
