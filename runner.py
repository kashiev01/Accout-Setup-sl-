"""
The three-phase run over a list of clients, shared by the CLI
(create_accounts.py) and the web server (server.py).

run() never reads or writes clients.csv / packs_keys.json / slapi_tokens.json
itself — the CLI does that around it, the server keeps everything per job.
Progress is reported through the optional on_event callback; the per-system
modules keep printing their own log lines to stdout.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config
import crimewall
import billing
import slapi
from models import Client

ALL_PHASES = ("1", "2", "3")

# on_event(phase, email, status, detail)
#   phase start/end:  email=None, status "running" | "done" | "skipped"
#   per client:       status "running" | "created" | "updated" | "ok" | "failed"
EventCallback = Callable[[str, str | None, str, str], None]


@dataclass
class RunResult:
    counts: dict[str, int] = field(default_factory=lambda: {"created": 0, "updated": 0, "failed": 0})
    packs_keys: dict[str, str] = field(default_factory=dict)     # email -> packs_api key
    slapi_tokens: dict[str, str] = field(default_factory=dict)   # email -> SL API token
    failures: dict[str, list[str]] = field(default_factory=dict)  # email -> ["phase 2: ...", ...]


def client_label(client: Client) -> str:
    if client.company:
        return f"{client.full_name} / {client.company} ({client.email})"
    return f"{client.full_name} ({client.email})"


def run(
    clients: list[Client],
    phases: set[str] | None = None,
    dry_run: bool = False,
    result: RunResult | None = None,
    profile_dir: str | Path = "browser_profile",
    headless: bool | None = None,
    on_event: EventCallback | None = None,
) -> RunResult:
    """
    Run the requested phases over clients. Pass `result` to keep partial
    results if a phase raises (e.g. a login failure); pre-fill
    result.packs_keys to run phase 3 without phase 2.
    """
    phases = set(phases or ALL_PHASES)
    result = result if result is not None else RunResult()
    emit = on_event or (lambda *a: None)
    headless = config.HEADLESS if headless is None else headless

    crimewall_clients = [c for c in clients if c.product != "SL API"]
    slapi_clients = [c for c in clients if c.product == "SL API"]
    packs_clients = [c for c in clients if c.packs]
    crimewall_packs_clients = [c for c in crimewall_clients if c.packs]

    def fail(phase: str, client: Client, msg: str) -> None:
        result.failures.setdefault(client.email, []).append(f"phase {phase}: {msg}")
        print(f"  [FAIL] {msg}")
        emit(phase, client.email, "failed", msg)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
        )
        try:
            page = context.new_page()

            # ── Phase 1: accounts (CrimeWall + SL API) ────────────────────────
            if "1" in phases and clients:
                emit("1", None, "running", "")
                print(f"\n[PHASE 1] accounts — {len(clients)} total "
                      f"(CrimeWall: {len(crimewall_clients)}, SL API: {len(slapi_clients)})")
                if not dry_run:
                    if crimewall_clients:
                        crimewall.login(page)
                    if slapi_clients:
                        slapi.login_slapi(page)

                for i, client in enumerate(clients, start=1):
                    print(f"[{i}/{len(clients)}] [{client.product}] {client_label(client)}")
                    emit("1", client.email, "running", "")
                    try:
                        if client.product == "SL API":
                            action, token_key = slapi.process_slapi_client(page, client, dry_run=dry_run)
                            if token_key:
                                result.slapi_tokens[client.email] = token_key
                        else:
                            action = crimewall.process_client(page, client, dry_run=dry_run)
                        result.counts[action] += 1
                        print(f"  [{action.upper()}]")
                        emit("1", client.email, action, "")
                    except PlaywrightTimeout as e:
                        result.counts["failed"] += 1
                        fail("1", client, f"Timeout: {e}")
                    except Exception as e:
                        result.counts["failed"] += 1
                        fail("1", client, str(e))
                    if not dry_run:
                        time.sleep(config.DELAY_BETWEEN_ACCOUNTS)
                emit("1", None, "done", "")
            else:
                emit("1", None, "skipped", "")

            # ── Phase 2: Billing — create API keys + configure packs ─────────
            if "2" in phases and packs_clients and not dry_run:
                emit("2", None, "running", "")
                print(f"\n[PHASE 2] Billing — {len(packs_clients)} client(s) with packs")
                billing.login_billing(page)

                for client in packs_clients:
                    print(f"  {client.full_name} ({client.email})")
                    emit("2", client.email, "running", "")
                    try:
                        key = billing.create_or_get_billing_user(page, client)
                        print(f"  [BILLING] key: {key}")
                        billing.add_packs_to_key(page, key, client)
                        print("  [BILLING] packs configured")
                        result.packs_keys[client.email] = key
                        emit("2", client.email, "ok", key)
                    except PlaywrightTimeout as e:
                        fail("2", client, f"Timeout: {e}")
                    except Exception as e:
                        fail("2", client, str(e))
                    time.sleep(config.DELAY_BETWEEN_ACCOUNTS)
                emit("2", None, "done", "")
            else:
                emit("2", None, "skipped", "")

            # ── Phase 3: CrimeWall — apply pack keys ──────────────────────────
            targets = [c for c in crimewall_packs_clients if c.email in result.packs_keys]
            if "3" in phases and targets and not dry_run:
                emit("3", None, "running", "")
                print(f"\n[PHASE 3] CrimeWall — applying pack keys for {len(targets)} client(s)")
                if "1" not in phases:
                    crimewall.login(page)

                for client in targets:
                    print(f"  {client.full_name} ({client.email})")
                    emit("3", client.email, "running", "")
                    try:
                        crimewall.apply_pack_keys_crimewall(page, client, result.packs_keys[client.email])
                        print("  [OK] pack keys set")
                        emit("3", client.email, "ok", "")
                    except PlaywrightTimeout as e:
                        fail("3", client, f"Timeout: {e}")
                    except Exception as e:
                        fail("3", client, str(e))
                    time.sleep(config.DELAY_BETWEEN_ACCOUNTS)
                emit("3", None, "done", "")
            else:
                emit("3", None, "skipped", "")
        finally:
            context.close()

    print(f"\nDone. Created: {result.counts['created']}  Updated: {result.counts['updated']}  Failed: {result.counts['failed']}")
    return result
