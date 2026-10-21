"""
Generate onboarding emails as HTML from clients.csv.
Usage: py -3.11 generate_emails.py [--csv clients.csv] [--out emails.html]
Output: one email per client, separated by <hr>
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from packs import parse_packs_field

# ── Video links (fill in empty strings with actual URLs) ──────────────────────
VIDEO_LINKS = {
    "onboarding":       "https://www.youtube.com/watch?v=jC-wd7kNwZE",
    "darknet_leaks":    "https://drive.google.com/file/d/1KooT_BMEg5H2OicDEhxHDbEn4sTzOJ7W/view?usp=sharing",
    "darknet_stealers": "https://drive.google.com/file/d/1jcu07d92FfwTjEvLOagIBlb9ymx4cDOA/view?usp=sharing",
    "tg_insight":       "",
}

PACK_DISPLAY_NAMES = {
    "darknet":              "Darknet pack",
    "telegram_insight":     "Telegram Insight",
    "smp":                  "SMP",
    "paranoidlab":          "Darknet pack",
    "paranoidlab_leaks":    "Darknet Leaks pack",
    "paranoidlab_stealers": "Darknet Stealer Logs pack",
}

# Same packs, but without the "pack" suffix — matches the wording used in
# SL API emails' subject line and "Additional key for ..." line.
SLAPI_PACK_DISPLAY_NAMES = {
    "darknet":              "Darknet",
    "telegram_insight":     "Telegram Insight",
    "smp":                  "SMP",
    "paranoidlab":          "Darknet",
    "paranoidlab_leaks":    "Darknet Leaks",
    "paranoidlab_stealers": "Darknet Stealer Logs",
}

# Pack categories that grant darknet data (CrimeWall DN Customer templates
# and the SL API "API DN ..." paranoidlab templates).
DARKNET_PACKS = {"darknet", "paranoidlab", "paranoidlab_leaks", "paranoidlab_stealers"}

LICENSE_LABELS = {
    "trial":    "Trial",
    "paid":     "License",
    "partner":  "Partner",
    "internal": "Internal",
}


def link(text: str, url: str) -> str:
    if url:
        return f'<a href="{url}">{text}</a>'
    return text


def extract_pack_names(raw_packs: str, email: str = "") -> list[str]:
    """
    Parse the CSV 'packs' column (e.g. 'API DN Trial,SMP Trial') into an
    ordered, deduplicated list of pack categories ('darknet', 'smp'). Exact
    templates and date overrides aren't relevant for email content, only which packs
    are present.
    """
    try:
        parsed = parse_packs_field(raw_packs)
    except ValueError as e:
        print(f"[WARN] {email}: {e} — treating as no packs for this email")
        return []
    names = []
    seen = set()
    for pack, _template, _start, _end in parsed:
        if pack not in seen:
            seen.add(pack)
            names.append(pack)
    return names


def join_natural(items: list[str]) -> str:
    """['A'] -> 'A', ['A','B'] -> 'A and B', ['A','B','C'] -> 'A, B and C'."""
    if len(items) <= 1:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def generate_email_html(row: dict) -> str:
    name  = row["full_name"].strip()
    email = row["Email"].strip()
    lt    = row["license_type"].strip().lower()
    start = row["start_date"].strip()
    end   = row["end_date"].strip()

    packs = extract_pack_names(row.get("packs", ""), email)

    is_trial  = lt == "trial"
    has_packs = bool(packs)

    lines = []

    # Subject line
    if has_packs:
        pack_names    = " + ".join(PACK_DISPLAY_NAMES.get(p, p.title()) for p in packs)
        license_label = LICENSE_LABELS.get(lt, lt.title())
        lines.append(f'<p><strong>SL Crimewall + {pack_names} {license_label} license</strong></p>')
    elif is_trial:
        lines.append('<p><strong>Crimewall Trial License Instructions</strong></p>')
    elif lt == "paid":
        lines.append('<p><strong>Crimewall License Instructions</strong></p>')

    lines.append("<p>Dear Customer,</p>")

    if is_trial:
        lines.append(
            f"<p>We are pleased to send you the instructions for the Social Links Crimewall test:<br>"
            f"Trial Dates: Your trial period will run from {start} until {end}<br>"
            f"The trial account(s) is/are issued to:</p>"
        )
        lines.append(f"<ul><li>Name: {name}</li><li>Email: {email}</li></ul>")
        lines.append(
            f"<p>Key Issuance: On&nbsp;&nbsp;{start}, you will receive an invitation to set up a password "
            f"for your trial account via a separate message from Social Links. "
            f"Please ensure you check your SPAM folder as well.</p>"
        )
    else:
        lines.append(
            f"<p>We are pleased to send you the instructions for Social Links Crimewall:<br>"
            f"Your license period will run from {start} until {end}. The account is issued to:</p>"
        )
        lines.append(f"<ul><li>Name: {name}</li><li>Email: {email}</li></ul>")
        lines.append(
            f"<p>Key Issuance: On {start}, you will receive an invitation to set up a password "
            f"for your account via a separate message from Social Links. "
            f"Please ensure you check your SPAM folder as well.</p>"
        )

    # Main bullet list
    items = []
    items.append(
        f'{link("Onboarding Video", VIDEO_LINKS["onboarding"])} '
        f"with the explanation of Social Links Crimewall functionalities."
    )

    if DARKNET_PACKS & set(packs):
        sub = (
            f"<ul>"
            f'<li>{link("Video on how to work with the Leaks.", VIDEO_LINKS["darknet_leaks"])}</li>'
            f'<li>{link("Video on how to work with Stealer Logs.", VIDEO_LINKS["darknet_stealers"])}</li>'
            f"</ul>"
        )
        items.append(f"How to work with Darknet Pack Capabilities:{sub}")

    if "telegram_insight" in packs:
        items.append(
            f'{link("How to work with Telegram Insight Pack Capabilities.", VIDEO_LINKS["tg_insight"])}'
        )

    items.append(
        "Social Links Crimewall is a web-based solution. "
        "For the best experience with the tool, we suggest using Google Chrome as the browser."
    )

    lines.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")

    lines.append(
        "<p>If you don't receive the password setup email, you can set your password "
        'manually using this link: <a href="https://crimewall.sociallinks.io/auth/reset-password">'
        "https://crimewall.sociallinks.io/auth/reset-password</a>.</p>"
    )
    lines.append("<p>We wish you a pleasant experience with Social Links Crimewall.</p>")
    lines.append("<p>Best regards,<br>Social Links Team</p>")

    return "\n".join(lines)


def generate_slapi_email_html(row: dict, token_key: str, packs_key: str) -> str:
    start = row.get("start_date", "").strip()
    end   = row["end_date"].strip()

    packs = extract_pack_names(row.get("packs", ""), row.get("Email", "").strip())
    has_darknet = bool(DARKNET_PACKS & set(packs))
    has_packs   = bool(packs)

    points = row.get("points", "").strip()

    lines = []

    # Subject line
    pack_display_names = [SLAPI_PACK_DISPLAY_NAMES.get(p, p.title()) for p in packs]
    if has_packs:
        lines.append(f'<p><strong>SL API + {" + ".join(pack_display_names)} trial license</strong></p>')
    else:
        lines.append('<p><strong>SL API trial license</strong></p>')

    lines.append("<p>Dear Customer,</p>")
    lines.append("<p>We are pleased to send you the instructions for the Social Links API test:</p>")

    items = []
    valid_range = f"valid from {start} until {end}" if start else f"valid until {end}"
    items.append(
        f"Main API trial key for {points} calls, {valid_range}:<br>"
        f"<code>{token_key}</code>"
    )
    if has_packs:
        items.append(
            f"Additional key for {join_natural(pack_display_names)}:<br>"
            f"<code>{packs_key}</code>"
        )
    items.append(
        "You will receive access to our documentation shortly. "
        "Please double-check your spam folder for the Postman invitation."
    )

    lines.append("<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>")

    lines.append("<p>Some quick tips for getting started:<br>")
    if has_darknet:
        lines.append("To retrieve data from leaks, please use the SL ISE and SL Darknet methods.<br>")
    else:
        lines.append("To retrieve data from leaks, please use the SL ISE methods.<br>")
    lines.append('For facial recognition capabilities, use the endpoints related to &ldquo;Social Mapper&rdquo; transforms.</p>')

    lines.append("<p>We wish you a pleasant experience with Social Links API.</p>")
    lines.append("<p>Best regards,<br>Social Links Team</p>")

    return "\n".join(lines)


HTML_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Onboarding Emails</title>
<style>
  body { font-family: Arial, sans-serif; font-size: 14px; color: #222; max-width: 720px; margin: 40px auto; padding: 0 20px; }
  .email { margin-bottom: 40px; padding: 24px; border: 1px solid #ddd; border-radius: 4px; }
  hr { border: none; border-top: 2px solid #ccc; margin: 40px 0; }
  a { color: #1a73e8; }
  ul { margin: 4px 0; padding-left: 24px; }
  li { margin: 4px 0; }
  p { margin: 6px 0; }
</style>
</head>
<body>
"""

HTML_FOOTER = "\n</body>\n</html>\n"

_SUBJECT_RE = re.compile(r"<p><strong>(.*?)</strong></p>")


def render_emails(rows: list[dict], slapi_tokens: dict, packs_keys: dict) -> list[dict]:
    """
    One email per CSV-shaped row: [{email, name, product, subject, html}].
    Missing keys/tokens render as [TOKEN_KEY] / [PACKS_KEY] placeholders.
    Shared by main() and the web server.
    """
    emails = []
    for r in rows:
        email_addr = r.get("Email", "").strip()
        if not email_addr:
            continue
        product = r.get("product", "Crimewall").strip()
        if product == "SL API":
            token_key = slapi_tokens.get(email_addr, "[TOKEN_KEY]")
            packs_key = packs_keys.get(email_addr, "[PACKS_KEY]")
            html = generate_slapi_email_html(r, token_key, packs_key)
        else:
            html = generate_email_html(r)
        m = _SUBJECT_RE.search(html)
        emails.append({
            "email": email_addr,
            "name": r.get("full_name", "").strip(),
            "product": product,
            "subject": m.group(1) if m else "",
            "html": html,
        })
    return emails


def main() -> None:
    # Windows consoles default to cp1251/cp1252 and crash on names like "Peña".
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="clients.csv")
    parser.add_argument("--out", default="emails.html")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        sys.exit(1)

    slapi_tokens: dict = {}
    packs_keys: dict = {}
    if Path("slapi_tokens.json").exists():
        slapi_tokens = json.loads(Path("slapi_tokens.json").read_text(encoding="utf-8"))
    if Path("packs_keys.json").exists():
        packs_keys = json.loads(Path("packs_keys.json").read_text(encoding="utf-8"))

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter=";"))

    emails = render_emails(rows, slapi_tokens, packs_keys)
    if not emails:
        print("[ERROR] No clients found in CSV.")
        sys.exit(1)

    body = "\n<hr>\n".join(f'<div class="email">\n{e["html"]}\n</div>' for e in emails)

    Path(args.out).write_text(HTML_HEADER + body + HTML_FOOTER, encoding="utf-8")
    print(f"[OK] {len(emails)} email(s) written to {args.out}")


if __name__ == "__main__":
    main()
