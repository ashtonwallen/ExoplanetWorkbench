"""SQLite metadata, immutable numerical artifacts, and an atomic durable queue."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("EXO_DATA_DIR", ROOT / "data")).resolve()


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:16]


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "research.sqlite", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
          params TEXT NOT NULL, result TEXT, error TEXT, progress REAL DEFAULT 0,
          stage TEXT DEFAULT 'Queued', created TEXT NOT NULL, updated TEXT NOT NULL,
          cancel INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, job_id TEXT, time TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS investigations (
          id TEXT PRIMARY KEY, job_id TEXT UNIQUE, target TEXT, created TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS cache (
          key TEXT PRIMARY KEY, created TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, title TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT, created TEXT);
        """)
    (DATA / "artifacts").mkdir(exist_ok=True)
    (DATA / "observations").mkdir(exist_ok=True)


def decode(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("params", "result", "payload"):
        if k in d and d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def create_job(kind, params):
    ident = uid()
    with db() as c:
        c.execute("INSERT INTO jobs(id,kind,status,params,created,updated) VALUES(?,?,'queued',?,?,?)",
                  (ident, kind, json.dumps(params), now(), now()))
    return get_job(ident)


def get_job(ident):
    with db() as c:
        return decode(c.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone())


def jobs():
    with db() as c:
        return [decode(r) for r in c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 2000")]


def update_job(ident, **kw):
    allowed = {"status", "result", "error", "progress", "stage", "cancel", "attempts"}
    assert set(kw) <= allowed
    if "result" in kw:
        kw["result"] = json.dumps(kw["result"])
    kw["updated"] = now()
    with db() as c:
        c.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", (*kw.values(), ident))


def claim_job():
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT id FROM jobs WHERE status='queued' AND cancel=0 ORDER BY created LIMIT 1").fetchone()
        if not r:
            return None
        c.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=?", (now(), r[0]))
    return get_job(r[0])


def event(ident, message, progress=None):
    with db() as c:
        c.execute("INSERT INTO events(job_id,time,message) VALUES(?,?,?)", (ident, now(), message))
    update_job(ident, stage=message, **({"progress": progress} if progress is not None else {}))


def recover():
    with db() as c:
        c.execute("UPDATE jobs SET status='interrupted',stage='Worker stopped; resume from saved artifacts',updated=? WHERE status='running'", (now(),))


def cache_get(key):
    with db() as c:
        return decode(c.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone())


def cache_put(key, payload):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, now(), json.dumps(payload)))


def investigations():
    with db() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT payload FROM investigations ORDER BY created DESC")]


def investigation(ident):
    with db() as c:
        r = c.execute("SELECT payload FROM investigations WHERE id=?", (ident,)).fetchone()
    if not r:
        raise ValueError("Investigation not found")
    return json.loads(r[0])


def save_investigation(payload, job_id):
    with db() as c:
        c.execute("INSERT INTO investigations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                  (payload["id"], job_id, payload["target"], payload["created"], json.dumps(payload, allow_nan=False)))


def message(conversation_id, role, content):
    with db() as c:
        c.execute("INSERT INTO messages(conversation_id,role,content,created) VALUES(?,?,?,?)",
                  (conversation_id, role, content, now()))


def messages(conversation_id):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
