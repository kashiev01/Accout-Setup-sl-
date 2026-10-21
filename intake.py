"""
Turning a request into CSV-shaped rows, for the web server and the Slack bot.

A *request* is what the web form posts (and what the Slack parser produces):

    {
      "product": "Crimewall" | "SL API",
      "license_type": "trial" | "paid" | "partner" | "internal",
      "start_date": "2026-10-20", "end_date": "2026-10-27",
      "company": "Example Corp",
      "hubspot": "https://app-eu1.hubspot.com/.../0-3/528746289356" or "528746289356",
      "packs": [{"template": "11", "start": "", "end": ""}],   # id or Billing label
      "modes": ["quicklink"],
      "points": "500",
      "users": [{"name": "Jane Doe", "email": "jane.doe@example.com"}],
      "notes": "Keep me in CC",
    }

build_rows() validates it and returns one dict per user in exactly the
clients.csv shape, so every row then goes through the same Client.from_row
as the CLI — the form, Slack and CSV can't drift apart on validation.
"""

import csv
import io
import re
from datetime import date

from models import Client, LICENSE_TYPE_MAP, SLAPI_LICENSE_TYPES, VALID_MODES
from packs import (
    BILLING_TEMPLATES,
    PRODUCT_CATEGORIES,
    CATEGORY_TITLES,
    parse_packs_field,
    template_category,
    template_label,
)

CSV_FIELDS = ["product", "full_name", "Email", "Company name", "license_type", "start_date",
              "end_date", "modes", "packs", "company_id", "points"]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── small normalizers ─────────────────────────────────────────────────────────

def normalize_date(raw: str) -> str:
    """
    '2026-10-20', '2026/10/20', '20.10.2026', '10-20-2026', '10/20/2026' -> '2026-10-20'.
    With '-' or '/' the US month-first order is assumed (that's how request
    forms write it) unless the first number can't be a month. Anything
    unrecognised is returned stripped, for is_iso_date() to reject.
    """
    t = (raw or "").strip()
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$", t)
    if m:
        y, mo, d = m.groups()
    elif (m := re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", t)):
        d, mo, y = m.groups()
    elif (m := re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", t)):
        a, b, y = m.groups()
        mo, d = (b, a) if int(a) > 12 else (a, b)
    else:
        return t
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def is_iso_date(s: str) -> bool:
    if not _ISO_RE.match(s or ""):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def is_email(s: str) -> bool:
    return bool(_EMAIL_RE.fullmatch((s or "").strip()))


def derive_name(email: str) -> str:
    """'ali.brane@x.com' -> 'Ali Brane' (used when a request has no name for a user)."""
    local = email.split("@")[0]
    return " ".join(w[:1].upper() + w[1:].lower() for w in re.split(r"[._\-+]+", local) if w)


def extract_company_id(raw: str) -> str:
    """HubSpot deal link or bare number -> the trailing number ('' if none)."""
    m = re.search(r"(\d{6,})/?\s*$", (raw or "").strip())
    return m.group(1) if m else ""


def notes_to_todos(notes: str) -> list[str]:
    """Free-text notes -> checklist items (one per line / '·' / ';' separated part)."""
    parts = re.split(r"[\n;·]+", notes or "")
    return [p.strip(" -•*\t") for p in parts if p.strip(" -•*\t")]


# ── request -> rows ───────────────────────────────────────────────────────────

def _template_id(ref: str) -> str | None:
    ref = str(ref).strip()
    if ref in BILLING_TEMPLATES:
        return ref
    try:
        parsed = parse_packs_field(ref)
    except ValueError:
        return None
    return parsed[0][1] if parsed else None


def resolve_pack_ids(req: dict) -> dict:
    """Copy of req with pack templates given by name turned into template ids (unknown names kept)."""
    out = dict(req)
    out["packs"] = []
    for p in req.get("packs") or []:
        p = dict(p) if isinstance(p, dict) else {"template": p}
        p["template"] = _template_id(p.get("template", "")) or p.get("template", "")
        out["packs"].append(p)
    return out


def build_rows(req: dict) -> tuple[list[dict], list[str], list[str]]:
    """request -> (rows, errors, warnings). rows is empty whenever errors isn't."""
    errors: list[str] = []
    warnings: list[str] = []

    product = "SL API" if str(req.get("product", "")).strip().lower() in ("sl api", "slapi", "api") else "Crimewall"
    is_cw = product == "Crimewall"

    lt = str(req.get("license_type", "")).strip().lower()
    if lt == "license":
        lt = "paid"
    valid_lt = LICENSE_TYPE_MAP if is_cw else SLAPI_LICENSE_TYPES
    if lt not in valid_lt:
        errors.append(f"License type must be one of: {', '.join(valid_lt)}")

    start = normalize_date(str(req.get("start_date", "")))
    end = normalize_date(str(req.get("end_date", "")))
    if not is_iso_date(start) or not is_iso_date(end):
        errors.append("Start and end dates must be YYYY-MM-DD")
    elif end <= start:
        errors.append("End date must be after the start date")

    company = str(req.get("company", "")).strip()
    if is_cw and not company:
        errors.append("Company name is required for Crimewall")

    hub_raw = str(req.get("hubspot", "") or req.get("company_id", "")).strip()
    company_id = extract_company_id(hub_raw)
    if hub_raw and not company_id:
        errors.append("Couldn't find a HubSpot ID in the HubSpot field — paste the deal link or the number")
    if not company_id:
        warnings.append("No HubSpot ID — the pack key gets company_id = 0")

    # Packs: one template per category, only categories the product offers.
    pack_entries: list[str] = []
    seen_cats: dict[str, str] = {}
    for p in req.get("packs") or []:
        p = p if isinstance(p, dict) else {"template": p}
        tid = _template_id(p.get("template", ""))
        if tid is None:
            errors.append(f"Unknown pack template '{p.get('template')}'")
            continue
        cat = template_category(tid)
        label = template_label(tid)
        if cat not in PRODUCT_CATEGORIES[product]:
            errors.append(f"'{label}' isn't available for {product}")
            continue
        if cat in seen_cats:
            errors.append(f"Only one {CATEGORY_TITLES[cat][0]} pack per request ('{seen_cats[cat]}' and '{label}')")
            continue
        seen_cats[cat] = label
        p_start = normalize_date(str(p.get("start") or ""))
        p_end = normalize_date(str(p.get("end") or ""))
        if p_start or p_end:
            if not (is_iso_date(p_start) and is_iso_date(p_end)):
                errors.append(f"Custom dates for '{label}' must both be YYYY-MM-DD")
                continue
            if p_end <= p_start:
                errors.append(f"Custom end date for '{label}' must be after its start")
                continue
            pack_entries.append(f"{label}:{p_start}:{p_end}")
        else:
            pack_entries.append(label)

    modes: list[str] = []
    if is_cw:
        modes = [str(m).strip().lower() for m in req.get("modes") or [] if str(m).strip()]
        bad = set(modes) - VALID_MODES
        if bad:
            errors.append(f"Unknown modes: {', '.join(sorted(bad))}")

    points = ""
    if not is_cw:
        points = re.sub(r"[\s,]", "", str(req.get("points", "") or "500"))
        if not points.isdigit():
            errors.append("API credits must be a whole number")

    users = [u for u in req.get("users") or [] if str(u.get("name", "")).strip() or str(u.get("email", "")).strip()]
    if not users:
        errors.append("Add at least one user")
    seen_emails: set[str] = set()
    derived = 0
    for i, u in enumerate(users, start=1):
        email = str(u.get("email", "")).strip()
        if not is_email(email):
            errors.append(f"User #{i}: invalid email '{email}'")
        elif email.lower() in seen_emails:
            errors.append(f"User #{i}: {email} is listed twice")
        seen_emails.add(email.lower())
        if not str(u.get("name", "")).strip():
            derived += 1
    if derived:
        warnings.append(f"{derived} name(s) taken from the email address — check them")

    if errors:
        return [], errors, warnings

    rows = []
    for u in users:
        email = u["email"].strip()
        rows.append({
            "product": product,
            "full_name": str(u.get("name", "")).strip() or derive_name(email),
            "Email": email,
            "Company name": company,
            "license_type": lt,
            "start_date": start,
            "end_date": end,
            "modes": ",".join(modes) if modes else "none",
            "packs": ",".join(pack_entries),
            "company_id": company_id,
            "points": points,
        })

    # Final say goes to the same parser the CLI uses.
    for row in rows:
        try:
            Client.from_row(row)
        except (KeyError, ValueError) as e:
            errors.append(f"{row['Email']}: {e}")
    return ([] if errors else rows), errors, warnings


def rows_from_csv(text: str) -> tuple[list[dict], list[str]]:
    """clients.csv text -> (valid rows, errors); same rules as create_accounts.py."""
    rows, errors = [], []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            Client.from_row(row)
            rows.append({k: (row.get(k) or "") for k in CSV_FIELDS})
        except (KeyError, ValueError) as e:
            errors.append(f"Row {i}: {e}")
    if not rows and not errors:
        errors.append("The CSV has no rows")
    return rows, errors


def clients_from_rows(rows: list[dict]) -> list[Client]:
    return [Client.from_row(r) for r in rows]


# ── Slack request text -> request ─────────────────────────────────────────────

_SLACK_LABELS = {
    "product": "product",
    "main license type": "license",
    "sl api main license type": "license",
    "license type": "license",
    "license": "license",
    "packs to include": "packs",
    "packs": "packs",
    "hubspot deal id (company id)": "hubspot",
    "hubspot deal id": "hubspot",
    "company id": "hubspot",
    "hubspot": "hubspot",
    "company name": "company",
    "company": "company",
    "period": "period",
    "modes": "modes",
    "users": "other",
    "other details": "other",
}
_DATE_TOKEN_RE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4}")
_POINTS_RE = re.compile(r"(\d[\d\s,]*)\s*(?:api\s*)?(?:credits|calls|points)\b", re.I)


def _clean_slack(text: str) -> str:
    text = re.sub(r"<mailto:([^|>]+)\|[^>]*>", r"\1", text)
    text = re.sub(r"<(https?://[^|>]+)(?:\|[^>]*)?>", r"\1", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def parse_request_text(text: str) -> dict:
    """
    Parse the request form as posted in Slack (label line, then value lines):

        Product
        Crimewall
        Main license type
        Trial
        Packs to include
        DN Customer Trial, TG Insight Trial
        ...
        Other details
        - Jane Doe: jane.doe@example.com

    Users are every email found under "Other details" (the text before an
    email on its line is the name), "N api credits" sets points, and the
    remaining "Other details" lines become notes. Returns a request for
    build_rows(); it does no validation itself.
    """
    fields: dict[str, list[str]] = {}
    current = None
    for line in _clean_slack(text).splitlines():
        stripped = line.strip().strip("*_").strip()
        key = _SLACK_LABELS.get(stripped.rstrip(":").strip().lower())
        if key:
            current = key
            fields.setdefault(key, [])
            continue
        if current and stripped:
            fields[current].append(stripped)

    def one(key: str) -> str:
        return " ".join(fields.get(key, [])).strip()

    product = "SL API" if "api" in one("product").lower() else "Crimewall"
    dates = [normalize_date(d) for d in _DATE_TOKEN_RE.findall(one("period"))]

    packs = [p.strip() for p in one("packs").split(",") if p.strip() and p.strip().lower() != "none"]
    modes = [m.strip().lower().replace(" ", "_") for m in one("modes").split(",")
             if m.strip() and m.strip().lower() != "none"]

    users, notes, points = [], [], ""
    for line in fields.get("other", []):
        pm = _POINTS_RE.search(line)
        if pm:
            points = re.sub(r"[\s,]", "", pm.group(1))
        found = list(_EMAIL_RE.finditer(line))
        if not found:
            if not pm:
                notes.append(line)
            continue
        prev_end = 0
        for m in found:
            before = line[prev_end:m.start()]
            # "1000 api credits under the Contact - Jane Doe: jane@x" -> "Jane Doe"
            before = re.sub(r"^[\s\-•*\d.)]+", "", before).rstrip(" :<-–—(\t")
            name = re.split(r"\s[-–—]\s|:", before)[-1].strip()
            users.append({"name": name if not _EMAIL_RE.search(name) else "", "email": m.group(0)})
            prev_end = m.end()

    req = {
        "product": product,
        "license_type": one("license").lower(),
        "start_date": dates[0] if dates else "",
        "end_date": dates[1] if len(dates) > 1 else "",
        "company": one("company"),
        "hubspot": one("hubspot"),
        "packs": [{"template": p} for p in packs],
        "modes": modes,
        "users": users,
        "notes": "\n".join(notes),
    }
    if product == "SL API":
        req["points"] = points or "500"
    return req
