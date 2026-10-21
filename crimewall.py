"""
CrimeWall admin automation: scripted login, account create/update, modes,
and the "User keys" inline used to apply Billing pack API keys (phase 3).
"""

from datetime import datetime, date

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config
from models import Client, LICENSE_TYPE_MAP
from packs import PACK_KEY_TYPES, template_label

USERS_URL = config.ADMIN_URL.rstrip("/") + "/users/user/"


def login(page: Page) -> None:
    page.goto(config.ADMIN_URL)
    page.wait_for_load_state("networkidle")
    if "login" not in page.url:
        print("[OK] CrimeWall session restored")
        return
    page.fill("#id_username", config.ADMIN_LOGIN)
    page.fill("#id_password", config.ADMIN_PASSWORD)
    page.click("input[type='submit']")
    page.wait_for_load_state("networkidle")
    if "login" in page.url:
        raise RuntimeError("CrimeWall login failed — check ADMIN_* in config.py")
    print("[OK] Logged in to CrimeWall")


def account_exists(page: Page, client: Client) -> bool:
    page.goto(f"{USERS_URL}?q={client.email}")
    page.wait_for_load_state("networkidle")
    return page.locator("#result_list tbody tr").count() > 0


def select2_choose(page: Page, field_id: str, value: str) -> None:
    page.locator(f"#{field_id} + span .select2-selection").click()
    search_box = page.locator(".select2-dropdown .select2-search__field")
    search_box.wait_for(state="visible", timeout=5000)
    search_box.type(value, delay=50)
    try:
        page.wait_for_selector(
            ".select2-results__option:not(.select2-results__option--disabled)",
            timeout=5000,
        )
        options = page.locator(".select2-results__option:not(.select2-results__option--disabled)")
        exact = options.filter(has_text=value)
        if exact.count() > 0:
            exact.first.click()
        else:
            options.first.click()
    except PlaywrightTimeout:
        search_box.press("Enter")


def _license_still_active(page: Page) -> bool:
    """True if the account's currently-saved active_until date is still in the future."""
    current = page.locator("#id_active_until_0").input_value().strip()
    if not current:
        return False
    try:
        return datetime.strptime(current, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return False


def fill_billing_fields(page: Page, client: Client, include_team: bool = True, update_dates: bool = True) -> None:
    """
    Fill name/license_type/activation dates — shared by create and update flows.
    include_team=False on the change form: it exposes a plain #id_company text
    field instead of the add form's #id_team select2, so team is set differently there.
    update_dates=False leaves the currently-saved activate_on/active_until alone —
    used on update when the existing license hasn't expired yet, so CSV re-runs
    don't shift/shorten a still-active license's window.
    """
    page.fill("#id_name", client.full_name)
    if include_team:
        select2_choose(page, "id_team", client.company)
    else:
        page.fill("#id_company", client.company)
    page.select_option("#id_license_type", LICENSE_TYPE_MAP[client.license_type])
    if update_dates:
        page.fill("#id_activate_on", client.start_date)
        page.fill("#id_active_until_0", client.end_date)
        page.fill("#id_active_until_1", "23:59:59")


def fill_create_form(page: Page, client: Client) -> None:
    page.goto(f"{USERS_URL}add/")
    page.wait_for_load_state("networkidle")

    page.fill("#id_email", client.email)
    fill_billing_fields(page, client)

    page.click("input[name='_continue']")
    page.wait_for_load_state("networkidle")

    if "/add/" in page.url:
        errors = page.locator(".errorlist li").all_text_contents()
        raise RuntimeError(f"CrimeWall form errors: {errors}")


def open_existing_account(page: Page, client: Client) -> None:
    page.goto(f"{USERS_URL}?q={client.email}")
    page.wait_for_load_state("networkidle")
    page.locator("#result_list tbody tr:first-child th a").click()
    page.wait_for_load_state("networkidle")


DASHBOARD_MANAGER_PERMISSION_SELECTORS = [
    "#id_manager_dashboard_activity_enabled",
    "#id_manager_dashboard_sessions_enabled",
    "#id_manager_dashboard_users_enabled",
]


def set_modes(page: Page, client: Client) -> None:
    mode_selectors = {
        "quicklink":         "#id_quicklink_enabled",
        "dashboard_manager": "#id_manager_dashboard_enabled",
        "osint_start":       "#id_osint_start_enabled",
    }
    for mode, selector in mode_selectors.items():
        checkbox = page.locator(selector)
        should = mode in client.modes
        if should and not checkbox.is_checked():
            checkbox.check()
        elif not should and checkbox.is_checked():
            checkbox.uncheck()

    dashboard_manager = "dashboard_manager" in client.modes
    for selector in DASHBOARD_MANAGER_PERMISSION_SELECTORS:
        checkbox = page.locator(selector)
        if dashboard_manager and not checkbox.is_checked():
            checkbox.check()
        elif not dashboard_manager and checkbox.is_checked():
            checkbox.uncheck()

    page.select_option("#id_two_factor_auth_type", "BY_EMAIL")


def set_pack_keys(page: Page, client: Client, packs_api_key: str) -> None:
    """
    Add/update rows in the CrimeWall 'User keys' inline (prefix: keys).
    Each pack category gets its own row: type=slDarknetApiKey or TelegramInsightAuth,
    value=packs_api_key. Updates existing rows if type already present.
    """
    initial_count = int(
        page.locator("#id_keys-INITIAL_FORMS").get_attribute("value") or "0"
    )

    # Map already-saved key types to their row index
    existing: dict[str, int] = {}
    for i in range(initial_count):
        type_val = page.locator(f"select[name='keys-{i}-type']").evaluate("el => el.value")
        existing[type_val] = i

    total = int(page.locator("#id_keys-TOTAL_FORMS").get_attribute("value") or str(initial_count))
    add_btn = page.locator("fieldset[aria-labelledby='keys-heading'] a.addlink")

    for pack, _template, _start, _end in client.packs:
        key_type = PACK_KEY_TYPES[pack]

        if key_type in existing:
            row_idx = existing[key_type]
        else:
            add_btn.click()
            page.wait_for_timeout(300)
            row_idx = total
            total += 1
            existing[key_type] = row_idx  # several templates of one category share a key row

        page.select_option(f"select[name='keys-{row_idx}-type']", key_type)
        page.fill(f"input[name='keys-{row_idx}-value']", packs_api_key)


def process_client(page: Page, client: Client, dry_run: bool) -> str:
    exists = account_exists(page, client)
    action = "updated" if exists else "created"

    if dry_run:
        pack_info = f", packs: {', '.join(template_label(t) for _, t, _, _ in client.packs)}" if client.packs else ""
        print(f"  [DRY RUN] Would be {action}: {client.full_name} / {client.company} ({client.email}){pack_info}")
        return action

    if exists:
        open_existing_account(page, client)
        update_dates = not _license_still_active(page)
        if not update_dates:
            print(f"  [CW] license still active, keeping existing activate_on/active_until for {client.email}")
        fill_billing_fields(page, client, include_team=False, update_dates=update_dates)
    else:
        fill_create_form(page, client)

    set_modes(page, client)
    page.click("input[name='_save']")
    page.wait_for_load_state("networkidle")

    return action


def apply_pack_keys_crimewall(page: Page, client: Client, packs_api_key: str) -> None:
    """Open existing CrimeWall account and set User keys for packs, then save."""
    open_existing_account(page, client)
    set_pack_keys(page, client, packs_api_key)
    page.click("input[name='_save']")
    page.wait_for_load_state("networkidle")
