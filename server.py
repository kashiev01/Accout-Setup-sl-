"""
Web UI + Slack intake for account setup.

    uvicorn server:app --host 0.0.0.0 --port 8000

Pages: /  (new request form), /jobs/<id>  (progress, then emails), /history.
Every request — form, CSV upload or Slack message — becomes CSV-shaped rows
(intake.py), is queued as a job (jobs.py) and runs through the same
runner.run() as `create_accounts.py`. Settings come from the environment,
see .env.example.
"""

import base64
import json
import os
import secrets
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import slack_bot
from intake import build_rows, parse_request_text, resolve_pack_ids, rows_from_csv
from jobs import JobStore, Worker, install_stdout_router, new_job_data
from models import LICENSE_TYPE_MAP, SLAPI_LICENSE_TYPES, VALID_MODES
from packs import CATEGORY_TITLES, PRODUCT_CATEGORIES, templates_by_category

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
PROFILE_DIR = Path(os.environ.get("BROWSER_PROFILE", BASE_DIR / "browser_profile"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
# "user:pass,user2:pass2" — HTTP basic auth for the UI/API (not /slack/events). Empty = off.
BASIC_AUTH = os.environ.get("BASIC_AUTH", "")
# Header set by an auth proxy in front of the app (e.g. oauth2-proxy's X-Auth-Request-Email).
AUTH_USER_HEADER = os.environ.get("AUTH_USER_HEADER", "")
_headless_env = os.environ.get("HEADLESS")
HEADLESS = None if _headless_env is None else _headless_env.lower() in ("1", "true", "yes")

store = JobStore(DATA_DIR / "jobs.sqlite3")
_seen_slack_events: set[str] = set()
_slack_lock = threading.Lock()


def job_url(job_id: int, request: Request | None = None) -> str:
    base = PUBLIC_BASE_URL or (str(request.base_url).rstrip("/") if request else "")
    return f"{base}/jobs/{job_id}"


def _on_finished(job_id: int, data: dict, status: str) -> None:
    slack = data.get("slack")
    if slack and slack_bot.enabled():
        slack_bot.post(slack["channel"], slack["ts"], slack_bot.finished_text(job_id, data, status, job_url(job_id)))


worker = Worker(store, PROFILE_DIR, HEADLESS, on_finished=_on_finished)

app = FastAPI(title="Account Setup", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    install_stdout_router()
    worker.start()


# ── auth ──────────────────────────────────────────────────────────────────────

def _basic_users() -> dict[str, str]:
    users = {}
    for pair in BASIC_AUTH.split(","):
        if ":" in pair:
            u, p = pair.split(":", 1)
            users[u.strip()] = p.strip()
    return users


@app.middleware("http")
async def _auth(request: Request, call_next):
    request.state.user = ""
    if AUTH_USER_HEADER:
        request.state.user = request.headers.get(AUTH_USER_HEADER, "")
    users = _basic_users()
    if users and not request.url.path.startswith("/slack/"):
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                u, p = base64.b64decode(header[6:]).decode().split(":", 1)
                ok = u in users and secrets.compare_digest(users[u], p)
                if ok and not request.state.user:
                    request.state.user = u
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Account Setup"'})
    return await call_next(request)


# ── pages ─────────────────────────────────────────────────────────────────────

@app.get("/")
def page_form():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/jobs/{job_id}")
def page_job(job_id: int):
    return FileResponse(STATIC_DIR / "job.html")


@app.get("/history")
def page_history():
    return FileResponse(STATIC_DIR / "history.html")


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/meta")
def api_meta(request: Request):
    """Everything the form needs, from the same registries the scripts use."""
    by_cat = templates_by_category()
    products = {}
    for product, cats in PRODUCT_CATEGORIES.items():
        products[product] = {
            "licenses": list(LICENSE_TYPE_MAP) if product == "Crimewall" else sorted(SLAPI_LICENSE_TYPES, reverse=True),
            "categories": [
                {"id": c, "title": CATEGORY_TITLES[c][0], "hint": CATEGORY_TITLES[c][1],
                 "templates": [{"id": tid, "label": label} for tid, label in by_cat.get(c, [])]}
                for c in cats
            ],
        }
    return {
        "today": date.today().isoformat(),
        "user": request.state.user,
        "products": products,
        "modes": [m for m in ("quicklink", "dashboard_manager", "osint_start") if m in VALID_MODES],
    }


def _phases_and_dry_run(body: dict) -> tuple[list[str], bool]:
    phases = [str(p) for p in body.get("phases") or ["1", "2", "3"] if str(p) in ("1", "2", "3")]
    if not phases:
        raise HTTPException(422, detail={"errors": ["Pick at least one phase"]})
    return phases, bool(body.get("dry_run"))


def _create_job(rows, *, source, request, created_by, body, notes, slack=None) -> int:
    phases, dry_run = _phases_and_dry_run(body)
    data = new_job_data(rows, source=source, created_by=created_by, request=request,
                        phases=phases, dry_run=dry_run, notes=notes, slack=slack)
    job_id = store.create(data)
    worker.enqueue(job_id)
    return job_id


@app.post("/api/requests")
async def api_create(request: Request):
    body = await request.json()
    rows, errors, warnings = build_rows(body)
    if errors:
        return JSONResponse({"errors": errors, "warnings": warnings}, status_code=422)
    job_id = _create_job(rows, source="web", request=body, created_by=request.state.user or "web",
                         body=body, notes=body.get("notes", ""))
    return {"id": job_id, "url": f"/jobs/{job_id}", "warnings": warnings}


@app.post("/api/requests/validate")
async def api_validate(request: Request):
    body = await request.json()
    rows, errors, warnings = build_rows(body)
    return {"errors": errors, "warnings": warnings, "rows": rows}


@app.post("/api/requests/csv")
async def api_create_csv(request: Request):
    body = await request.json()
    rows, errors = rows_from_csv(body.get("csv", ""))
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    job_id = _create_job(rows, source="csv", request=None, created_by=request.state.user or "web",
                         body=body, notes=body.get("notes", ""))
    return {"id": job_id, "url": f"/jobs/{job_id}", "warnings": []}


@app.post("/api/requests/parse")
async def api_parse(request: Request):
    """Slack-format request text -> form request (the form's "Paste request" button)."""
    body = await request.json()
    return resolve_pack_ids(parse_request_text(body.get("text", "")))


@app.get("/api/jobs")
def api_jobs(limit: int = 200):
    out = []
    for j in store.recent(limit=limit):
        d = j["data"]
        out.append({
            "id": j["id"], "created_at": j["created_at"], "status": j["status"],
            "title": d["title"], "source": d["source"], "created_by": d["created_by"],
            "users": len(d["users"]), "dry_run": d["dry_run"], "finished_at": d["finished_at"],
        })
    return out


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    job = worker.snapshot(job_id)
    if not job:
        raise HTTPException(404, "No such request")
    d = job["data"]
    return {
        "id": job["id"], "created_at": job["created_at"], "status": job["status"],
        "queue_position": worker.queue_position(job_id) if job["status"] == "queued" else 0,
        "log": job["log"],
        "request": resolve_pack_ids(d["request"]) if d["request"] else None,
        **{k: d[k] for k in ("title", "source", "created_by", "rows", "phases_requested", "dry_run",
                             "phases", "users", "todos", "result", "emails", "error", "started_at", "finished_at")},
    }


@app.post("/api/jobs/{job_id}/todos/{idx}")
async def api_todo(job_id: int, idx: int, request: Request):
    body = await request.json()
    job = store.get(job_id)
    if not job or not (0 <= idx < len(job["data"]["todos"])):
        raise HTTPException(404)
    if job_id in worker.live:
        raise HTTPException(409, "Wait for the run to finish")
    job["data"]["todos"][idx]["done"] = bool(body.get("done"))
    store.save(job_id, job["status"], job["data"])
    return {"ok": True}


# ── Slack ─────────────────────────────────────────────────────────────────────

@app.post("/slack/events")
async def slack_events(request: Request):
    raw = await request.body()
    if not slack_bot.enabled() or not slack_bot.verify(request.headers, raw):
        raise HTTPException(401)
    body = json.loads(raw)
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
    if request.headers.get("x-slack-retry-num"):
        return {"ok": True}  # first delivery already handled; don't queue twice

    event = body.get("event") or {}
    event_id = body.get("event_id", "")
    with _slack_lock:
        if event_id in _seen_slack_events:
            return {"ok": True}
        _seen_slack_events.add(event_id)
    if not slack_bot.is_request_message(event):
        return {"ok": True}

    channel, ts = event["channel"], event["ts"]
    req = parse_request_text(slack_bot.message_text(event))
    req["phases"] = ["1", "2", "3"]
    rows, errors, warnings = build_rows(req)
    if errors:
        slack_bot.post(channel, ts, ":x: Couldn't start this request:\n" + "\n".join(f"• {e}" for e in errors)
                       + f"\nFix it in the form instead: {PUBLIC_BASE_URL or ''}/")
        return {"ok": True}
    job_id = _create_job(rows, source="slack", request=req, created_by=f"slack:{event.get('user', '')}",
                         body=req, notes=req.get("notes", ""), slack={"channel": channel, "ts": ts})
    note = ("\n" + "\n".join(f"• {w}" for w in warnings)) if warnings else ""
    slack_bot.post(channel, ts, f":hourglass_flowing_sand: Request #{job_id} queued — {len(rows)} user(s). "
                                f"Progress: {job_url(job_id, request)}{note}")
    return {"ok": True}
