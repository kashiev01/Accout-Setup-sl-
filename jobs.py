"""
Job storage (SQLite) and the single background worker that runs them.

One worker thread runs jobs strictly one at a time: they share one browser
profile, and two jobs touching the same Billing user at once could mint
keys over each other. Each job's stdout (the per-system modules print their
log lines) is captured into that job's log via a thread-aware stdout router.
"""

import io
import json
import queue
import sqlite3
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import runner
from generate_emails import render_emails
from intake import clients_from_rows, notes_to_todos

PHASE_TITLES = {"1": "Accounts", "2": "Billing", "3": "Keys in CrimeWall"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── stdout routing ────────────────────────────────────────────────────────────

class ThreadStdout(io.TextIOBase):
    """sys.stdout replacement: writes from a registered thread go to its sink."""

    def __init__(self, fallback):
        self.fallback = fallback
        self.sinks: dict[int, Callable[[str], None]] = {}

    def write(self, s: str) -> int:
        sink = self.sinks.get(threading.get_ident())
        if sink:
            sink(s)
        else:
            self.fallback.write(s)
        return len(s)

    def flush(self) -> None:
        self.fallback.flush()


def install_stdout_router() -> ThreadStdout:
    if not isinstance(sys.stdout, ThreadStdout):
        sys.stdout = ThreadStdout(sys.stdout)
    return sys.stdout


# ── storage ───────────────────────────────────────────────────────────────────

class JobStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    log TEXT NOT NULL DEFAULT ''
                )""")
            self.db.commit()

    def create(self, data: dict) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO jobs (created_at, status, data) VALUES (?, 'queued', ?)",
                (now_iso(), json.dumps(data, ensure_ascii=False)),
            )
            self.db.commit()
            return cur.lastrowid

    def save(self, job_id: int, status: str, data: dict, log: str | None = None) -> None:
        with self.lock:
            if log is None:
                self.db.execute("UPDATE jobs SET status=?, data=? WHERE id=?",
                                (status, json.dumps(data, ensure_ascii=False), job_id))
            else:
                self.db.execute("UPDATE jobs SET status=?, data=?, log=? WHERE id=?",
                                (status, json.dumps(data, ensure_ascii=False), log, job_id))
            self.db.commit()

    def get(self, job_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def recent(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, created_at, status, data FROM jobs ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def by_status(self, status: str) -> list[int]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT id FROM jobs WHERE status=? ORDER BY id", (status,))]

    def latest_keys(self, emails: list[str]) -> tuple[dict, dict]:
        """Most recent packs_api keys / SL API tokens other jobs issued for these emails."""
        wanted = {e.lower() for e in emails}
        packs, tokens = {}, {}
        for job in reversed(self.recent(limit=1000)):  # oldest first, newer overwrite
            res = job["data"].get("result") or {}
            for src, dst in ((res.get("packs_keys") or {}, packs), (res.get("slapi_tokens") or {}, tokens)):
                for email, key in src.items():
                    if email.lower() in wanted:
                        dst[email] = key
        return packs, tokens

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d


# ── job data ──────────────────────────────────────────────────────────────────

def new_job_data(rows: list[dict], *, source: str, created_by: str, request: dict | None,
                 phases: list[str], dry_run: bool, notes: str = "", slack: dict | None = None) -> dict:
    """The JSON blob stored per job; `users` and `phases` are what the job page renders."""
    phases = [p for p in runner.ALL_PHASES if p in set(phases)]
    first = rows[0] if rows else {}
    products = sorted({r["product"] for r in rows})
    users = []
    for r in rows:
        has_packs = bool(r.get("packs", "").strip())
        is_cw = r["product"] != "SL API"
        users.append({
            "email": r["Email"], "name": r["full_name"], "product": r["product"],
            "p1": "queued" if "1" in phases else "n/a",
            "p2": "queued" if "2" in phases and has_packs and not dry_run else "n/a",
            "p3": "queued" if "3" in phases and has_packs and is_cw and not dry_run else "n/a",
            "errors": [],
        })
    if len(products) == 1:
        title = f"{first.get('Company name') or first.get('full_name', '')} · {products[0]} {first.get('license_type', '').title()}"
    else:
        title = f"{len(rows)} clients · {' + '.join(products)}"
    return {
        "title": title.strip(" ·"),
        "source": source,
        "created_by": created_by,
        "request": request,
        "rows": rows,
        "phases_requested": phases,
        "dry_run": dry_run,
        "phases": {p: {"title": PHASE_TITLES[p], "status": "queued" if p in phases else "skipped"} for p in runner.ALL_PHASES},
        "users": users,
        "todos": [{"text": t, "done": False} for t in notes_to_todos(notes)],
        "result": None,
        "emails": [],
        "error": "",
        "started_at": None,
        "finished_at": None,
        "slack": slack,
    }


# ── worker ────────────────────────────────────────────────────────────────────

class Worker:
    def __init__(self, store: JobStore, profile_dir: Path, headless: bool | None,
                 on_finished: Callable[[int, dict, str], None] | None = None):
        self.store = store
        self.profile_dir = profile_dir
        self.headless = headless
        self.on_finished = on_finished
        self.q: "queue.Queue[int]" = queue.Queue()
        self.live: dict[int, dict] = {}  # job_id -> {"data", "log": [str], "status"} while running
        self.thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)

    def start(self) -> None:
        for job_id in self.store.by_status("running"):
            job = self.store.get(job_id)
            job["data"]["error"] = "The server restarted during this run — check the admins and re-run if needed."
            job["data"]["finished_at"] = now_iso()
            self.store.save(job_id, "failed", job["data"])
        for job_id in self.store.by_status("queued"):
            self.q.put(job_id)
        self.thread.start()

    def enqueue(self, job_id: int) -> None:
        self.q.put(job_id)

    def snapshot(self, job_id: int) -> dict | None:
        """The job with its in-progress log, if it's the one running now."""
        job = self.store.get(job_id)
        live = self.live.get(job_id)
        if job and live:
            job["status"] = live["status"]
            job["data"] = live["data"]
            job["log"] = "".join(live["log"])
        return job

    def queue_position(self, job_id: int) -> int:
        return list(self.q.queue).index(job_id) + 1 if job_id in self.q.queue else 0

    def _loop(self) -> None:
        while True:
            job_id = self.q.get()
            try:
                self._run(job_id)
            except Exception:
                traceback.print_exc()
            finally:
                self.live.pop(job_id, None)

    def _run(self, job_id: int) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] != "queued":
            return
        data = job["data"]
        live = {"data": data, "log": [], "status": "running"}
        self.live[job_id] = live
        data["started_at"] = now_iso()
        self.store.save(job_id, "running", data)

        users = {u["email"]: u for u in data["users"]}

        def on_event(phase: str, email: str | None, status: str, detail: str) -> None:
            if email is None:
                data["phases"][phase]["status"] = status
            elif email in users:
                users[email][f"p{phase}"] = status
                if status == "failed":
                    users[email]["errors"].append(f"{PHASE_TITLES[phase]}: {detail}")
            self.store.save(job_id, "running", data, "".join(live["log"]))

        router = sys.stdout if isinstance(sys.stdout, ThreadStdout) else None
        if router:
            router.sinks[threading.get_ident()] = live["log"].append

        rows = data["rows"]
        result = runner.RunResult()
        phases = set(data["phases_requested"])
        emails = [r["Email"] for r in rows]
        prev_packs, prev_tokens = self.store.latest_keys(emails)
        if "2" not in phases:
            result.packs_keys.update(prev_packs)  # phase 3 alone re-applies the last issued keys
        status = "done"
        try:
            print(f"Request #{job_id}: {len(rows)} client(s), phases {', '.join(sorted(phases))}"
                  f"{' (dry run)' if data['dry_run'] else ''}")
            runner.run(clients_from_rows(rows), phases=phases, dry_run=data["dry_run"], result=result,
                       profile_dir=self.profile_dir, headless=self.headless, on_event=on_event)
            if result.failures:
                status = "done_with_errors"
        except Exception as e:
            status = "failed"
            data["error"] = f"{type(e).__name__}: {e}"
            print(f"\n[ERROR] {data['error']}")
        finally:
            if router:
                router.sinks.pop(threading.get_ident(), None)

        # Whatever never started (an earlier phase failed for that user, or the
        # run crashed) is "skipped"; whatever was mid-way when it crashed is "failed".
        for p in data["phases"].values():
            if p["status"] in ("queued", "running"):
                p["status"] = "failed" if p["status"] == "running" else "skipped"
        for u in data["users"]:
            for k in ("p1", "p2", "p3"):
                if u[k] in ("queued", "running"):
                    u[k] = "failed" if u[k] == "running" else "skipped"

        data["result"] = {
            "counts": result.counts,
            "packs_keys": result.packs_keys if "2" in phases else {},
            "slapi_tokens": result.slapi_tokens,
            "failures": result.failures,
        }
        # Emails: keys from this run, falling back to what earlier jobs issued.
        data["emails"] = render_emails(rows, {**prev_tokens, **result.slapi_tokens},
                                       {**prev_packs, **result.packs_keys})
        failed = set(result.failures)
        for e in data["emails"]:
            e["failed"] = e["email"] in failed
        data["finished_at"] = now_iso()
        live["status"] = status
        self.store.save(job_id, status, data, "".join(live["log"]))
        if self.on_finished:
            try:
                self.on_finished(job_id, data, status)
            except Exception:
                traceback.print_exc()
