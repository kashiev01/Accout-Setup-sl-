# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Playwright-based browser automation that provisions client accounts across three separate admin systems. Two front doors over the same code: the operator CLI driven by a CSV (run locally by an admin), and a web server (`server.py`) where colleagues fill a form, upload a CSV, or post the request in Slack, and get the onboarding emails back.

## Commands

```
pip install -r requirements.txt
playwright install chromium      # first-time browser install

py -3.11 create_accounts.py --phase 1        # CrimeWall accounts + SL API user/token
py -3.11 create_accounts.py --phase 2        # Billing (sl-area.com) packs_api keys + packs
py -3.11 create_accounts.py --phase 3        # apply pack keys back into CrimeWall
py -3.11 create_accounts.py                  # all phases
py -3.11 create_accounts.py --dry-run        # print planned actions, no browser writes
py -3.11 create_accounts.py --csv other.csv  # use a different input file

py -3.11 generate_emails.py                  # render onboarding emails from clients.csv -> emails.html

py -3.11 -m uvicorn server:app --port 8000   # web UI locally (http://localhost:8000)
docker compose up -d --build                 # web UI on the server (fill .env from .env.example first)
```

Phases are independent processes and can be run separately in any order after phase 1 (phase 3 re-logs into CrimeWall itself if phase 1 wasn't run in the same invocation). There is no test suite — verification is done via `--dry-run` and by re-reading state back from the admin UI (see `debug`/`verify` throwaway scripts pattern below).

## CSV contract (`clients.csv`)

Semicolon-delimited: `product;full_name;Email;Company name;license_type;start_date;end_date;modes;packs;company_id;points`

- `product` — `Crimewall` (default) or `SL API`; determines which fields are required and which systems get touched.
- `Crimewall` rows need: full_name, Email, Company name, license_type (`trial|paid|partner|internal`; `paid` maps to CrimeWall's "license" option), start_date, end_date. `modes` is comma-separated from `{quicklink, dashboard_manager, osint_start}`.
- `SL API` rows need: full_name, Email, license_type (`trial|paid` → token Plan `API Trial`/`API Paid`, see `slapi.SLAPI_PLANS`; also re-applied when an existing token is updated), end_date, start_date (used as the token's "Contract start date" on `/tokens/new`, and also needed for the Billing packs_api key/pack rows when `packs` is set); `points` (API call budget) defaults to `500` if left blank.
- `packs` (both product types): comma-separated Billing template names, exactly as shown in Billing's Pack inline template select, each optionally followed by `:start:end`, e.g. `API DN Trial,SMP Trial:2026-01-01:2026-06-01`. Names are matched case-insensitively against `packs.BILLING_TEMPLATES` (id → label + category); `packs.TEMPLATE_ALIASES` adds request-form names like `DN Customer Trial` → `Customer Trial` (id 1). Crimewall clients normally get `DN Customer Trial/Small/Big`, SL API clients the `API DN ...` templates. Each template has a category (`darknet`, `telegram_insight`, `smp`, and for the SL API `API DN ...` templates `paranoidlab` = leaks + stealer logs, `paranoidlab_leaks`, `paranoidlab_stealers`) that drives the Billing `type` select, CrimeWall's "User keys" type in phase 3, and email wording. Dates default to the row's own `start_date`/`end_date` when omitted. Optional overall. `smp` has no `PACK_KEY_TYPES` entry, so it can't be applied to CrimeWall's "User keys" inline in phase 3 — it's Billing-only for now.
- `company_id`: optional integer used only for the Billing `packs_api` key's `company_id` field; defaults to `0`.

`Client.from_row` in `models.py` is the single source of truth for parsing/validation — invalid rows are skipped with a `[SKIP]` log line, not fatal.

## Module layout

| Module | Contents |
|---|---|
| `packs.py` | Pack template registry: `BILLING_TEMPLATES` (id → label, category), `TEMPLATE_ALIASES`, type-select mappings, `parse_packs_field`. **This is the file to touch when a new template is added in Billing** — see the docstring at the top of it for the checklist. Pure data + parsing, no Playwright/config dependency. |
| `models.py` | `Client` dataclass, `Client.from_row`, `load_clients`. CSV parsing/validation only. |
| `crimewall.py` | CrimeWall admin automation: login, account create/update, modes, "User keys" inline (phase 3). |
| `billing.py` | Billing (sl-area.com) admin automation: login, `packs_api` key find/renew/create, `Pack` inline (phase 2). |
| `slapi.py` | SL API (React SPA) automation: login, user + token issuance (phase 1). |
| `runner.py` | The three-phase loop: `run(clients, phases, dry_run, result, on_event)` -> `RunResult` (counts, packs_keys, slapi_tokens, failures). Shared by the CLI and the server; never touches clients.csv / the JSON files. |
| `create_accounts.py` | CLI only: argparse, loads clients.csv, calls `runner.run`, reads/writes `packs_keys.json` / `slapi_tokens.json`. |
| `intake.py` | Request -> CSV-shaped rows for the server: `build_rows` (form/Slack request JSON, validated, then every row through `Client.from_row`), `rows_from_csv`, `parse_request_text` (the Slack request-form text). |
| `jobs.py` | SQLite job store (`DATA_DIR/jobs.sqlite3`) and the single worker thread that runs jobs one at a time; per-job stdout capture into the job log. |
| `server.py` | FastAPI app: pages `/`, `/jobs/<id>`, `/history`; `/api/*` JSON; `/slack/events`. Settings from env (`.env.example`). |
| `slack_bot.py` | Slack Events API: signature check, which messages count as requests, thread replies. |
| `static/` | Plain HTML/CSS/JS front end (no build step). The form gets products/categories/templates from `/api/meta`, i.e. from `packs.py`. |

Adding a new pack should not require touching `crimewall.py`, `billing.py`, `runner.py`, or the web front end — only `packs.py` (registry, incl. `PRODUCT_CATEGORIES`/`CATEGORY_TITLES` for a new category) and, if it changes what's required in a row, `models.py`.

## Architecture: three systems, one CSV

| System | Base URL (config.py) | Auth | Purpose |
|---|---|---|---|
| CrimeWall admin | `ADMIN_URL` | scripted login, no captcha | the actual product account (license, modes, pack API keys) |
| Billing admin | `BILLING_URL` (sl-area.com) | scripted login (the reCAPTCHA was removed; `login_billing` still waits 90s for a human if one ever renders) | issues `packs_api` keys and configures pack quota/expiry |
| SL API | `SLAPI_URL` | scripted login, React SPA | separate product; user + token issuance |

`runner.run()` orchestrates three phases over the union of all clients, sharing one Playwright `launch_persistent_context(user_data_dir="browser_profile")` — sessions/cookies persist across runs so login is only needed once per system per profile. Without the captcha it can run headless (the server does).

- **Phase 1** (`crimewall.process_client` / `slapi.process_slapi_client`) — for each client, either creates a new CrimeWall/SL API account or updates an existing one (matched by email search in the admin's changelist). Also creates/refreshes SL API tokens; existing tokens are found and updated in place rather than duplicated. Writes `slapi_tokens.json` (CLI) / the job's result (server).
- **Phase 2** (`billing.create_or_get_billing_user` + `billing.add_packs_to_key`) — only for clients with `packs` set. Finds-or-creates a Billing user, then reuses that user's existing `packs_api` key: the non-expired one with the latest `started` date if any are active, or renews (extends the dates of) the most-recently-expired one if none are active; only mints a brand-new key if the user has none at all. Exception: if that key already carries a pack of the same category under a different template (e.g. `API DN Trial` on the key, `API DN Business` requested), a brand-new key is minted instead and the old one is left as is — phase 3 then overwrites the CrimeWall "User keys" row with the new key. Then adds/updates `Pack` inline rows on the chosen key — a template already on the key gets its dates updated in place (always, even if still active; if that pack had expired, its nested PackRows' `monthly_limit_left` is also reset to their `monthly_limit`), otherwise a new row is added; rows already matching template and dates are left alone (there's a two-step save because the Billing admin resets `expired` to a template default on first save — see `add_packs_to_key`'s re-open-and-correct step, which now only re-checks rows this run touched since packs can carry independent dates). Writes `packs_keys.json` (CLI) / the job's result (server).
- **Phase 3** (`crimewall.apply_pack_keys_crimewall`) — uses the keys from phase 2 of the same run, else `packs_keys.json` (CLI) or the latest keys earlier jobs issued for those emails (server), and writes them into CrimeWall's "User keys" inline for Crimewall-product clients only.

`generate_emails.py` is a separate, read-only consumer: it re-reads `clients.csv` plus the two key/token JSON files to render onboarding HTML emails. It doesn't touch the browser and doesn't import the Playwright-dependent modules above, but it does import `packs.parse_packs_field` to read the `packs` column, so pack subject lines/bullets ("SL API + Telegram Insight + SMP trial license", "Additional key for Telegram Insight and SMP") stay correct for any combination of registered packs — no per-combination code to update when a pack is added, just its display name in `PACK_DISPLAY_NAMES` / `SLAPI_PACK_DISPLAY_NAMES`. `render_emails(rows, slapi_tokens, packs_keys)` is the part the server reuses.

### Web server

Form, CSV upload and Slack all end as CSV-shaped rows (`intake.py`), so validation is `Client.from_row` everywhere. A job stores its rows, request, per-user/per-phase status, log, result keys and rendered emails in one JSON blob; `/jobs/<id>` polls `/api/jobs/<id>` until it finishes, then shows the emails (recipient dropdown, copy subject / copy as rich HTML), issued keys and the notes checklist. One worker thread, jobs strictly sequential (shared browser profile; concurrent Billing edits could mint keys over each other). On restart, jobs left `running` are marked failed and `queued` ones resume. Slack: top-level messages in `SLACK_CHANNEL_ID` containing a request form are parsed with `intake.parse_request_text` and queued automatically; replies go to the message's thread.

### Admin UI quirks worth knowing before editing selectors

- CrimeWall's **add** form exposes company as a select2 field (`#id_team` + `.select2-selection`); the **change** form for an existing user exposes it as a plain text input (`#id_company`) instead. `fill_billing_fields(..., include_team=...)` branches on this — don't assume add/change forms share field ids.
- `select2_choose()` types into the select2 search box and falls back to pressing Enter if no dropdown option renders in time — needed because company names in the admin don't always exact-match the CSV's `Company name`.
- Billing's packs inline resets `expired` dates to a template default (`started + 10`) on first save; `add_packs_to_key()` deliberately reloads and re-saves to correct it. Don't remove that second pass.
- The `packs_api` changelist's ability to filter by user (rather than just an exact key value) is unverified against the live admin — `_find_reusable_packs_key()` tries `?q=<email>` first, then falls back to `?user__id__exact=<id>`. If key reuse ever fails to find keys that should exist, that's the first thing to check.
- SL API is a React SPA: form submissions can succeed via AJAX without a URL change, so success is verified by re-querying the user/token list rather than trusting `page.url`.

## Config / secrets

`config.py` holds plaintext credentials for all three systems (local CLI use); every value can be overridden by an environment variable of the same name. The Docker image doesn't get `config.py` (`.dockerignore`) — it uses the secret-free `config.example.py` and takes everything from `.env`. `DELAY_BETWEEN_ACCOUNTS` throttles between clients; `HEADLESS` toggles browser visibility.
