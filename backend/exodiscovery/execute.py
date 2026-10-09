import sys
from . import archive, pipeline, store
from .schemas import Analysis, Search


def run(ident):
    store.init()
    job = store.get_job(ident)
    try:
        if job["kind"] == "analysis":
            result = pipeline.summary(pipeline.analyze(Analysis(**job["params"]), ident))
        elif job["kind"] == "search":
            cfg = Search(**job["params"])
            pipeline.checkpoint(ident, "Querying MAST", .1)
            result = archive.search(cfg.target, cfg.mission)
        elif job["kind"] == "tic":
            result = archive.tic(job["params"]["target"])
        elif job["kind"] == "chat":
            from .assistant import investigate
            result = investigate(job["params"], ident)
        elif job['kind']=='research':
            from .research import run as run_research
            result=run_research(ident)
        else:
            result = pipeline.followup(job["params"], job["kind"], ident)
        stage = 'Completed'
        if job['kind']=='research':
            stage = 'Research outcome: '+str(result['outcome']).replace('_',' ')
        store.update_job(ident, status="completed", progress=1, stage=stage, result=result)
    except pipeline.Cancelled:
        store.update_job(ident, status="cancelled", stage="Cancelled; saved artifacts retained")
    except Exception as exc:
        from .assistant import redact
        store.update_job(ident, status="failed", error=redact(f"{type(exc).__name__}: {exc}")[:1500], stage="Failed")


if __name__ == "__main__":
    run(sys.argv[1])
