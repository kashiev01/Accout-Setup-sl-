"""SL API (React SPA) automation: scripted login, user + token issuance."""

import secrets
from datetime import date
from urllib.parse import quote

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config
from models import Client

SLAPI_BASE = config.SLAPI_URL.rstrip("/")
SLAPI_PLANS = {
    "trial": "API Trial",
    "paid":  "API Paid",
}


def login_slapi(page: Page) -> None:
    page.goto(SLAPI_BASE + "/login")
    page.wait_for_load_state("networkidle")
    if "login" not in page.url:
        print("[OK] SL API session restored")
        return
    for sel in ("input[name='email']", "input[name='username']", "input[type='email']"):
        if page.locator(sel).count() > 0:
            page.fill(sel, config.SLAPI_LOGIN)
            break
    page.fill("input[name='password']", config.SLAPI_PASSWORD)
    page.locator("button[type='submit'], input[type='submit']").first.click()
    page.wait_for_load_state("networkidle")
    if "login" in page.url:
        raise RuntimeError("SL API login failed — check SLAPI_* in config.py")
    print("[OK] Logged in to SL API")


def _slapi_user_exists(page: Page, email: str) -> bool:
    page.goto(f"{SLAPI_BASE}/users/?q={quote(email)}")
    page.wait_for_load_state("networkidle")
    return page.locator("table tbody tr").filter(has_text=email).count() > 0


def _slapi_get_existing_token(page: Page, email: str) -> str | None:
    """Open the user's detail page and read the first key from their 'Tokens' table, if any."""
    page.goto(f"{SLAPI_BASE}/users/?q={quote(email)}")
    page.wait_for_load_state("networkidle")
    page.locator("table tbody tr td a").filter(has_text=email).first.click()
    page.wait_for_load_state("networkidle")

    token_cells = page.locator("h3:text-is('Tokens') ~ table tbody tr td:first-child")
    if token_cells.count() == 0:
        return None
    return token_cells.first.inner_text().strip()


def _slapi_create_user(page: Page, client: Client) -> None:
    page.goto(f"{SLAPI_BASE}/users/new")
    page.wait_for_load_state("networkidle")
    page.fill("input[name='email']", client.email)
    page.fill("input[name='name']", client.full_name)
    pwd = secrets.token_urlsafe(16)
    page.fill("input[name='password']", pwd)
    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")
    # React SPA: URL may not change after successful AJAX POST — verify by checking users list
    if not _slapi_user_exists(page, client.email):
        raise RuntimeError(f"SL API user creation failed for {client.email}")
    print(f"  [SLAPI] User created: {client.email}")


def _slapi_react_select(page: Page, label_text: str, search_value: str) -> None:
    """Select a value in a react-select v1 dropdown, located via its form label."""
    # Find the label, then its following-sibling col div — that div has exactly one Select
    label = page.locator(f".control-label:text('{label_text}')")
    col = label.locator("xpath=following-sibling::div[1]")
    col.locator(".Select-control").click()
    search_input = col.locator(".Select-input input")
    search_input.wait_for(state="visible", timeout=5000)
    search_input.type(search_value, delay=50)
    page.wait_for_timeout(600)
    page.locator(".Select-option").filter(has_text=search_value).first.click()


def _slapi_set_expired(page: Page, date_str: str) -> None:
    """Fill react-datetime-picker expired field by interacting with the visible number inputs."""
    year, month, day = date_str.split("-")
    container = page.locator(".react-datetime-picker__inputGroup")
    for name, value in [("year", year), ("month", month), ("day", day),
                        ("hour24", "23"), ("minute", "59")]:
        inp = container.locator(f"input[name='{name}']")
        inp.fill(str(int(value)))
        page.wait_for_timeout(80)


def _slapi_token_still_active(page: Page) -> bool:
    """True if the token's currently-saved expired date is still in the future."""
    container = page.locator(".react-datetime-picker__inputGroup")
    try:
        year = container.locator("input[name='year']").input_value().strip()
        month = container.locator("input[name='month']").input_value().strip()
        day = container.locator("input[name='day']").input_value().strip()
        return date(int(year), int(month), int(day)) >= date.today()
    except (ValueError, TypeError):
        return False


def _slapi_create_token(page: Page, client: Client) -> str:
    page.goto(f"{SLAPI_BASE}/tokens/new")
    page.wait_for_load_state("networkidle")

    _slapi_set_expired(page, client.end_date)
    page.fill("input[name='email']", client.email)
    page.wait_for_timeout(300)
    _slapi_react_select(page, "User", client.email)
    _slapi_react_select(page, "Plan", SLAPI_PLANS[client.license_type])

    if not client.start_date:
        raise ValueError(f"'start_date' not set in CSV for {client.email} (needed for contract start date)")
    page.fill("input[name='contract_start_date']", client.start_date)

    points_value = client.points
    page.locator("input[name='points']").fill(points_value)

    page.get_by_role("button", name="Save").click()
    # key input exists on /tokens/new with empty value; wait until it's populated
    try:
        page.wait_for_function(
            "() => { const k = document.querySelector('input[name=\"key\"]'); return k && k.value && k.value.length > 0; }",
            timeout=10000,
        )
    except PlaywrightTimeout:
        raise RuntimeError(f"SL API token creation failed for {client.email}")

    key_value = page.locator("input[name='key']").input_value().strip()
    if not key_value:
        raise RuntimeError(f"Could not read SL API token key for {client.email}")
    print(f"  [SLAPI] Token created ({SLAPI_PLANS[client.license_type]}, {points_value} calls), key: {key_value[:20]}..., expires {client.end_date}")
    return key_value


def _slapi_update_token(page: Page, token_key: str, client: Client) -> str:
    """Find an existing token by its key on /tokens/ and update plan, expired date + points from the CSV."""
    page.goto(f"{SLAPI_BASE}/tokens/?q={quote(token_key)}")
    page.wait_for_load_state("networkidle")

    links = page.locator("table tbody tr td a").filter(has_text=token_key)
    if links.count() == 0:
        raise RuntimeError(f"Token '{token_key}' not found on {SLAPI_BASE}/tokens/")
    links.first.click()
    page.wait_for_load_state("networkidle")

    still_active = _slapi_token_still_active(page)
    if still_active:
        print(f"  [SLAPI] token still active, keeping existing expiry for {client.email}")
    else:
        _slapi_set_expired(page, client.end_date)

    _slapi_react_select(page, "Plan", SLAPI_PLANS[client.license_type])

    points_value = client.points
    page.locator("input[name='points']").fill(points_value)

    page.get_by_role("button", name="Save").click()
    page.wait_for_load_state("networkidle")

    key_value = page.locator("input[name='key']").input_value().strip()
    if not key_value:
        raise RuntimeError(f"Could not read SL API token key after update for {client.email}")
    expiry_note = "unchanged" if still_active else client.end_date
    print(f"  [SLAPI] Token updated ({SLAPI_PLANS[client.license_type]}, {points_value} calls), key: {key_value[:20]}..., expires {expiry_note}")
    return key_value


def process_slapi_client(page: Page, client: Client, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        print(f"  [DRY RUN] SL API: would create/update user+token for {client.full_name} ({client.email}), plan {SLAPI_PLANS[client.license_type]}, expires {client.end_date}")
        return "created", ""

    if not _slapi_user_exists(page, client.email):
        _slapi_create_user(page, client)
        token_key = _slapi_create_token(page, client)
        return "created", token_key

    print(f"  [SLAPI] User already exists: {client.email}")
    existing_token = _slapi_get_existing_token(page, client.email)
    if existing_token:
        print(f"  [SLAPI] Existing token found: {existing_token[:20]}...")
        token_key = _slapi_update_token(page, existing_token, client)
        return "updated", token_key

    print("  [SLAPI] No existing token for user, creating a new one")
    token_key = _slapi_create_token(page, client)
    return "created", token_key
