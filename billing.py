"""
Billing (sl-area.com) admin automation: scripted login with manual reCAPTCHA,
packs_api key find-or-create/renew, and the Pack inline that configures
per-pack quota/expiry on a key.
"""

import re
import secrets
from datetime import datetime, date
from urllib.parse import quote, urljoin

from playwright.sync_api import Page

import config
from models import Client
from packs import (
    PACK_BILLING_TYPE,
    BILLING_TYPE_TO_PACK,
    template_category,
    template_label,
)

# Billing admin: plan select value for packs_api
PACKS_API_PLAN_VALUE = "42"

BILLING_BASE = config.BILLING_URL.rstrip("/")
BILLING_USERS_URL = BILLING_BASE + "/users/user/"
BILLING_APIKEY_URL = BILLING_BASE + "/service/apikey/"


def login_billing(page: Page) -> None:
    page.goto(config.BILLING_URL)
    page.wait_for_load_state("networkidle")
    if "login" not in page.url:
        print("[OK] Billing session restored")
        return
    page.fill("#id_username", config.BILLING_LOGIN)
    page.fill("#id_password", config.BILLING_PASSWORD)
    # reCAPTCHA isn't always rendered on the login form; only wait for a human when it is
    has_captcha = page.locator(".g-recaptcha, iframe[src*='recaptcha']").count() > 0
    if has_captcha:
        print("  [!] reCAPTCHA — solve it in the browser and click 'Log in' (waiting up to 90s)...")
    else:
        page.click("#login-form input[type='submit']")
    page.wait_for_url(lambda url: "login" not in url, timeout=90000)
    page.wait_for_load_state("networkidle")
    print("[OK] Logged in to Billing admin (session saved for next run)")


def _limit_update_day(start_date: str) -> str:
    """Return limits_restore_day value: start day minus 1, clamped to 1–28."""
    day = int(start_date.split("-")[2])
    update_day = day - 1
    if update_day < 1:
        update_day = 28
    if update_day > 28:
        update_day = 28
    return str(update_day)


def _started_time(start_date: str) -> str:
    """06:00:00 if start is in the future, current time if start is today."""
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    if d > date.today():
        return "06:00:00"
    return datetime.now().strftime("%H:%M:%S")


def _ensure_billing_user(page: Page, client: Client) -> str:
    """Find or create billing user. Returns user ID as string."""
    page.goto(f"{BILLING_USERS_URL}?q={client.email}")
    page.wait_for_load_state("networkidle")

    if page.locator("#result_list tbody tr").count() > 0:
        page.locator("#result_list tbody tr:first-child th a").click()
        page.wait_for_load_state("networkidle")
    else:
        page.goto(f"{BILLING_USERS_URL}add/")
        page.wait_for_load_state("networkidle")

        page.fill("#id_email", client.email)
        page.fill("#id_name", client.full_name)

        pwd = secrets.token_urlsafe(16)
        page.fill("#id_password1", pwd)
        page.fill("#id_password2", pwd)

        page.click("input[name='_continue']")
        page.wait_for_load_state("networkidle")

        if "/add/" in page.url:
            errors = page.locator(".errorlist li").all_text_contents()
            raise RuntimeError(f"Billing user form errors: {errors}")

    # URL is .../users/user/<id>/change/  (may have query string)
    m = re.search(r"/users/user/(\d+)/", page.url)
    if not m:
        raise RuntimeError(f"Cannot extract billing user ID from URL: {page.url}")
    return m.group(1)


def create_or_get_billing_user(page: Page, client: Client) -> str:
    """
    Find or create billing user, then reuse an existing non-expired packs_api
    key if one exists (newest `started` wins), renew the most-recently-expired
    one if all existing keys are expired, or mint a fresh one if none exist.
    Returns the key string (e.g. 'packs_api-XXXXXXXXXXXXXXXX').
    """
    user_id = _ensure_billing_user(page, client)
    print(f"  [BILLING] user id: {user_id}")

    found = _find_reusable_packs_key(page, client, user_id)
    if found:
        key_value, needs_renewal, href, conflicts = found
        if conflicts:
            # Switching a pack to a different template means a new key (which
            # phase 3 then swaps into CrimeWall's User keys); the old key is left as is.
            print(f"  [BILLING] key {key_value} has {', '.join(conflicts)} — minting a new key")
            return _create_packs_key(page, client, user_id)
        if needs_renewal:
            _renew_packs_key(page, client, href)
            print(f"  [BILLING] renewed expired key: {key_value}")
        else:
            print(f"  [BILLING] reusing existing key: {key_value}")
        return key_value

    return _create_packs_key(page, client, user_id)


def _find_reusable_packs_key(page: Page, client: Client, user_id: str) -> tuple[str, bool, str, list[str]] | None:
    """
    Inspect this billing user's packs_api keys and decide what to do:
    - one or more non-expired: return (key with latest `started`, False, its
      href, its template conflicts)
    - none non-expired but at least one exists: return (most-recently-expired
      key, True, its href, its template conflicts) so the caller can jump
      straight back to it and renew
    - no packs_api keys at all: return None (caller mints a new one)
    In both cases a key with no template conflicts (see _template_conflicts)
    is preferred over the date ordering; a non-empty conflict list tells the
    caller to mint a new key instead.

    NOTE: unverified against the live admin whether `?q=<email>` filters the
    packs_api changelist by the related user (only `?q=<exact key>` is proven,
    see add_packs_to_key). Falls back to `?user__id__exact=` if the first
    query returns nothing.
    """
    hrefs: list[str] = []
    for query in (f"?q={quote(client.email)}", f"?user__id__exact={user_id}"):
        page.goto(f"{BILLING_APIKEY_URL}{query}")
        page.wait_for_load_state("networkidle")
        rows = page.locator("#result_list tbody tr")
        count = rows.count()
        if count > 0:
            links = page.locator("#result_list tbody tr th a")
            hrefs = [links.nth(i).get_attribute("href") for i in range(count)]
            break
    if not hrefs:
        return None

    candidates: list[tuple[str, date, date, str, list[str]]] = []  # (key_value, started_d, expired_d, href, conflicts)
    for href in hrefs:
        page.goto(urljoin(BILLING_APIKEY_URL, href))
        page.wait_for_load_state("networkidle")

        try:
            started_d = datetime.strptime(page.locator("#id_started_0").input_value().strip(), "%Y-%m-%d").date()
            expired_d = datetime.strptime(page.locator("#id_expired_0").input_value().strip(), "%Y-%m-%d").date()
        except ValueError:
            continue

        key_value = page.locator(".form-row.field-key .readonly").inner_text().strip()
        if not key_value.startswith("packs_api-"):
            continue  # user's changelist search can surface other plan types (e.g. hackathon keys) too
        candidates.append((key_value, started_d, expired_d, href, _template_conflicts(_read_pack_rows(page), client)))

    if not candidates:
        return None

    today = date.today()
    # Keys without a template conflict win first, so a key minted by an
    # earlier run for a template switch is picked up again instead of
    # minting yet another one.
    active = [c for c in candidates if c[2] >= today]
    if active:
        best = max(active, key=lambda c: (not c[4], c[1]))
        return best[0], False, best[3], best[4]

    stale = max(candidates, key=lambda c: (not c[4], c[2]))
    return stale[0], True, stale[3], stale[4]


def _renew_packs_key(page: Page, client: Client, href: str) -> None:
    """
    Extend an expired packs_api key's own started/expired to the client's
    current dates. Jumps straight back to the key via the href already found
    during discovery (_find_reusable_packs_key) instead of re-searching by
    key value. No reload-and-correct pass is needed here (unlike Pack rows):
    the admin doesn't reset a key's own `expired` field on save.
    """
    page.goto(urljoin(BILLING_APIKEY_URL, href))
    page.wait_for_load_state("networkidle")

    page.fill("#id_started_0", client.start_date)
    page.fill("#id_started_1", _started_time(client.start_date))
    page.fill("#id_expired_0", client.end_date)
    page.fill("#id_expired_1", "23:59:59")
    page.click("input[name='_save']")
    page.wait_for_load_state("networkidle")


def _create_packs_key(page: Page, client: Client, user_id: str) -> str:
    """Mint a brand-new packs_api key for this billing user."""
    page.goto(f"{BILLING_APIKEY_URL}add/")
    page.wait_for_load_state("networkidle")

    # User FK: could be raw_id_field (text input) or <select>
    user_field = page.locator("#id_user")
    tag = user_field.evaluate("el => el.tagName.toLowerCase()")
    if tag == "select":
        user_field.select_option(user_id)
    else:
        user_field.fill(user_id)

    page.select_option("#id_plan", PACKS_API_PLAN_VALUE)
    page.fill("#id_licenses", "0")
    page.fill("#id_company_id", client.company_id)
    page.fill("#id_started_0", client.start_date)
    page.fill("#id_started_1", _started_time(client.start_date))
    page.fill("#id_expired_0", client.end_date)
    page.fill("#id_expired_1", "23:59:59")

    page.click("input[name='_continue']")
    page.wait_for_load_state("networkidle")

    if "/add/" in page.url:
        errors = page.locator(".errorlist li").all_text_contents()
        raise RuntimeError(f"Billing API key form errors: {errors}")

    # Edit page shows the auto-generated key in a readonly div
    key_value = page.locator(".form-row.field-key .readonly").inner_text().strip()
    if not key_value:
        raise RuntimeError(f"Could not read generated packs_api key for {client.email}")
    return key_value


def _read_pack_rows(page: Page) -> list[dict]:
    """Saved Pack inline rows on the open key page: [{row_idx, pack (category), type, template, started, expired}]."""
    initial_count = int(
        page.locator("#id_pack_set-INITIAL_FORMS").get_attribute("value") or "0"
    )
    rows: list[dict] = []
    for i in range(initial_count):
        type_sel = page.locator(f"select[name='pack_set-{i}-type']")
        if not type_sel.count():
            continue
        raw_type = type_sel.evaluate("el => el.value")
        template_val = page.locator(f"select[name='pack_set-{i}-template']").evaluate("el => el.value")
        pack_name = template_category(template_val) or BILLING_TYPE_TO_PACK.get(raw_type, raw_type.replace(" ", "_"))
        started_val = page.locator(f"input[name='pack_set-{i}-started_0']").input_value().strip()
        expired_val = page.locator(f"input[name='pack_set-{i}-expired_0']").input_value().strip()
        rows.append({
            "row_idx": i, "pack": pack_name, "type": raw_type, "template": template_val,
            "started": started_val, "expired": expired_val,
        })
    return rows


def _template_conflicts(rows: list[dict], client: Client) -> list[str]:
    """
    Requested templates whose category is already on the key under a
    different template (e.g. key has 'API DN Trial', CSV asks for
    'API DN Business'), as 'old -> new' strings. Empty if the key can be reused.
    """
    on_key = {row["template"] for row in rows}
    conflicts = []
    for pack, template_id, _s, _e in client.packs:
        if template_id in on_key:
            continue
        for row in rows:
            if row["pack"] == pack:
                conflicts.append(f"'{template_label(row['template'])}' -> '{template_label(template_id)}'")
                break
    return conflicts


def _pack_expired(expired_val: str) -> bool:
    try:
        return datetime.strptime(expired_val, "%Y-%m-%d").date() < date.today()
    except ValueError:
        return False


def _restore_monthly_limits(page: Page, row_idx: int) -> None:
    """Set monthly_limit_left = monthly_limit on every saved PackRow nested under pack row `row_idx`."""
    limit_inputs = page.locator(
        f"input[name^='pack_set-{row_idx}-packrow_set-'][name$='-monthly_limit']"
    )
    for i in range(limit_inputs.count()):
        limit_input = limit_inputs.nth(i)
        name = limit_input.get_attribute("name")
        limit = limit_input.input_value().strip()
        if not limit:
            continue
        left_name = name + "_left"
        left_input = page.locator(f"input[name='{left_name}']")
        if not left_input.count():
            continue
        old_left = left_input.input_value().strip()
        if old_left != limit:
            print(f"  [BILLING]   {left_name}: {old_left or '-'} -> {limit}")
            left_input.fill(limit)


def add_packs_to_key(page: Page, api_key_value: str, client: Client) -> None:
    """
    Open the packs_api key edit page in billing admin and add/update Pack
    inline rows for each template in client.packs: a template already on the
    key gets its dates updated in place (left untouched if they already
    match) and, if it had expired, its PackRows' monthly_limit_left reset to
    monthly_limit; otherwise a new row is added. A same-category row on a different
    template never reaches here — create_or_get_billing_user mints a new key
    for that case instead.
    """
    page.goto(f"{BILLING_APIKEY_URL}?q={api_key_value}")
    page.wait_for_load_state("networkidle")

    if page.locator("#result_list tbody tr").count() == 0:
        raise RuntimeError(f"API key '{api_key_value}' not found in billing admin")

    page.locator("#result_list tbody tr:first-child th a").click()
    page.wait_for_load_state("networkidle")

    existing = _read_pack_rows(page)
    total = int(
        page.locator("#id_pack_set-TOTAL_FORMS").get_attribute("value") or "0"
    )
    touched_end_dates: dict[int, str] = {}  # row_idx -> expected expired_0 value, for the correction pass

    for pack, template_id, start_override, end_override in client.packs:
        pack_start = start_override or client.start_date
        pack_end = end_override or client.end_date
        label = template_label(template_id)
        old = next((row for row in existing if row["template"] == template_id), None)

        if (
            old
            and old["template"] == template_id
            and old["started"] == pack_start
            and old["expired"] == pack_end
            and old["type"] == PACK_BILLING_TYPE[pack]
        ):
            print(f"  [BILLING] pack '{label}' already up to date ({pack_start} -> {pack_end}) — skipping")
            continue  # already correct template and dates — leave untouched

        if old:
            row_idx = old["row_idx"]
            print(
                f"  [BILLING] updating pack '{label}' dates "
                f"{old['started']}->{old['expired']} -> {pack_start}->{pack_end}"
                + (f", type '{old['type']}' -> '{PACK_BILLING_TYPE[pack]}'" if old["type"] != PACK_BILLING_TYPE[pack] else "")
            )
            page.select_option(f"select[name='pack_set-{row_idx}-type']", PACK_BILLING_TYPE[pack])
            if _pack_expired(old["expired"]):
                print(f"  [BILLING] pack '{label}' was expired — restoring monthly_limit_left from monthly_limit")
                _restore_monthly_limits(page, row_idx)
        else:
            page.locator("a.add-handler.djn-model-service-pack").click()
            page.wait_for_timeout(400)
            row_idx = total
            total += 1
            page.select_option(f"select[name='pack_set-{row_idx}-type']", PACK_BILLING_TYPE[pack])
            print(f"  [BILLING] adding new pack '{label}' {pack_start} -> {pack_end}")

        page.select_option(f"select[name='pack_set-{row_idx}-template']", template_id)
        page.select_option(f"select[name='pack_set-{row_idx}-limits_restore_day']", _limit_update_day(pack_start))
        page.fill(f"input[name='pack_set-{row_idx}-started_0']", pack_start)
        page.fill(f"input[name='pack_set-{row_idx}-started_1']", _started_time(pack_start))
        page.fill(f"input[name='pack_set-{row_idx}-expired_0']", pack_end)
        page.fill(f"input[name='pack_set-{row_idx}-expired_1']", "23:59:59")
        touched_end_dates[row_idx] = pack_end

    if not touched_end_dates:
        return

    page.click("input[name='_continue']")
    page.wait_for_load_state("networkidle")

    # The server resets expired → started+10 on first save (template default).
    # _continue reloads the same change form, so just re-check the rows this
    # run touched and save once more — no need to re-navigate to get there.
    needs_resave = False
    for row_idx, expected_end in touched_end_dates.items():
        expired_field = page.locator(f"input[name='pack_set-{row_idx}-expired_0']")
        if expired_field.count() and expired_field.get_attribute("value") != expected_end:
            print(f"  [BILLING] correcting reset expiry on pack row {row_idx}: -> {expected_end}")
            expired_field.fill(expected_end)
            page.fill(f"input[name='pack_set-{row_idx}-expired_1']", "23:59:59")
            needs_resave = True

    if needs_resave:
        page.click("input[name='_save']")
        page.wait_for_load_state("networkidle")
