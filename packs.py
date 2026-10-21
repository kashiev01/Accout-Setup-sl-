"""
Registry of Billing pack templates and the admin-side identifiers each maps to.

The CSV `packs` column names Billing templates directly, exactly as they
appear in the Pack inline's template select (e.g. "API DN Trial", "SMP Trial").
Each template belongs to a pack category (darknet / telegram_insight / smp /
paranoidlab / paranoidlab_leaks / paranoidlab_stealers),
which drives the Billing pack_set-N-type select, CrimeWall's "User keys" type
(phase 3) and the email wording.

Adding a new template should only require touching this file:
- add it to BILLING_TEMPLATES (id from the select's option value, its exact
  label, and its pack category)
- if it's a new category: add it to PACK_BILLING_TYPE, and to PACK_KEY_TYPES
  if it needs to be applied to CrimeWall's "User keys" inline (phase 3) —
  categories without an entry there are Billing-only; phase 3 will KeyError
  if such a pack reaches a Crimewall-product client.
- if it's a new category: also add it to CATEGORY_TITLES and to the products
  that offer it in PRODUCT_CATEGORIES (that's what the web form shows)

crimewall.py, billing.py, and create_accounts.py's orchestration should
never need to change just because a template was added.
"""

from datetime import datetime

# Billing packs_api Pack inline: template select value -> (label, pack category).
# Labels match the admin's option text ("&amp;" in the HTML is "&" here).
BILLING_TEMPLATES: dict[str, tuple[str, str]] = {
    "1":  ("Customer Trial",                 "darknet"),
    "3":  ("Customer Small",                 "darknet"),
    "2":  ("Customer Big",                   "darknet"),
    "5":  ("Internal & Partner Demos",       "darknet"),
    "4":  ("Dev&QA",                         "darknet"),
    "6":  ("Custom",                         "darknet"),
    "7":  ("TG Insight Dev&QA",              "telegram_insight"),
    "8":  ("TG Insight Customer Big",        "telegram_insight"),
    "9":  ("TG Insight Customer Small",      "telegram_insight"),
    "10": ("TG Insight Demo",                "telegram_insight"),
    "11": ("TG Insight Trial",               "telegram_insight"),
    "13": ("SMP Trial",                      "smp"),
    "14": ("SMP Paid Basic",                 "smp"),
    "15": ("SMP Paid Advanced",              "smp"),
    "16": ("SMP Paid Pro",                   "smp"),
    "12": ("SMP Staff",                      "smp"),
    # SL API darknet templates: paranoidlab = leaks + stealer logs,
    # paranoidlab_leaks / paranoidlab_stealers = only one of the two.
    "18": ("API DN Trial",                   "paranoidlab"),
    "19": ("API DN Starter",                 "paranoidlab"),
    "20": ("API DN Business",                "paranoidlab"),
    "21": ("API DN Enterprise",              "paranoidlab"),
    "22": ("API DN Dev",                     "paranoidlab"),
    "23": ("API DN Leaks Starter",           "paranoidlab_leaks"),
    "24": ("API DN Leaks Business",          "paranoidlab_leaks"),
    "25": ("API DN Leaks Enterprise",        "paranoidlab_leaks"),
    "26": ("API DN Stealer Logs Starter",    "paranoidlab_stealers"),
    "27": ("API DN Stealer Logs Business",   "paranoidlab_stealers"),
    "28": ("API DN Stealer Logs Enterprise", "paranoidlab_stealers"),
}

# Alternative names the request form uses for the same templates.
TEMPLATE_ALIASES: dict[str, str] = {
    "DN Customer Trial": "1",
    "DN Customer Small": "3",
    "DN Customer Big":   "2",
}


def _norm(name: str) -> str:
    return " ".join(name.replace("&amp;", "&").split()).lower()


_TEMPLATE_ID_BY_NAME: dict[str, str] = {
    **{_norm(label): tid for tid, (label, _cat) in BILLING_TEMPLATES.items()},
    **{_norm(alias): tid for alias, tid in TEMPLATE_ALIASES.items()},
}


def template_label(template_id: str) -> str:
    return BILLING_TEMPLATES.get(template_id, (f"template #{template_id}", ""))[0]


def template_category(template_id: str) -> str | None:
    return BILLING_TEMPLATES.get(template_id, (None, None))[1]


# CrimeWall User keys inline: keys-N-type select values, by pack category.
PACK_KEY_TYPES = {
    "darknet":              "slDarknetApiKey",
    "telegram_insight":     "TelegramInsightAuth",
    "paranoidlab":          "slDarknetApiKey",
    "paranoidlab_leaks":    "slDarknetApiKey",
    "paranoidlab_stealers": "slDarknetApiKey",
}

# Billing packs_api Pack inline: pack_set-N-type select values, by pack
# category. Most categories' type option matches the name (with underscores
# as spaces), but some (e.g. smp) don't have a dedicated option and use
# "other" instead, so this is kept explicit rather than derived.
PACK_BILLING_TYPE = {
    "darknet":          "darknet",
    "telegram_insight": "telegram insight",
    "smp":              "other",
    "paranoidlab":          "paranoidlab",
    "paranoidlab_leaks":    "paranoidlab_leaks",
    "paranoidlab_stealers": "paranoidlab_stealers",
}
BILLING_TYPE_TO_PACK: dict[str, str] = {v: k for k, v in PACK_BILLING_TYPE.items()}

# Web form (server.py): which categories each product offers, in display order,
# and how a category is titled there. A category missing from a product's list
# can't be picked for it in the form (smp isn't offered for Crimewall since
# phase 3 can't apply it). Templates themselves come from BILLING_TEMPLATES.
PRODUCT_CATEGORIES: dict[str, list[str]] = {
    "Crimewall": ["darknet", "telegram_insight"],
    "SL API":    ["paranoidlab", "paranoidlab_leaks", "paranoidlab_stealers", "telegram_insight", "smp"],
}
CATEGORY_TITLES: dict[str, tuple[str, str]] = {  # category -> (title, hint)
    "darknet":              ("Darknet", "Leaks and stealer logs in CrimeWall"),
    "telegram_insight":     ("Telegram Insight", ""),
    "smp":                  ("SMP", ""),
    "paranoidlab":          ("Darknet", "Leaks + stealer logs"),
    "paranoidlab_leaks":    ("Darknet · Leaks only", ""),
    "paranoidlab_stealers": ("Darknet · Stealer Logs only", ""),
}


def templates_by_category() -> dict[str, list[tuple[str, str]]]:
    """category -> [(template_id, label), ...] in BILLING_TEMPLATES order."""
    out: dict[str, list[tuple[str, str]]] = {}
    for tid, (label, cat) in BILLING_TEMPLATES.items():
        out.setdefault(cat, []).append((tid, label))
    return out


def parse_packs_field(raw_packs: str) -> list[tuple[str, str, str | None, str | None]]:
    """
    Parse 'API DN Trial,SMP Trial:2026-01-01:2026-06-01' into
    [(category, template_id, start_override, end_override), ...], e.g.
    [('darknet', '18', None, None), ('smp', '13', '2026-01-01', '2026-06-01')].
    start/end default to None, meaning "use the client's own start_date/end_date".
    Template names are matched case-insensitively.
    """
    entries = [e.strip() for e in raw_packs.split(",") if e.strip() and e.strip().lower() != "none"]
    parsed: list[tuple[str, str, str | None, str | None]] = []

    for entry in entries:
        parts = entry.split(":")
        if len(parts) == 1:
            name = parts[0]
            start = end = None
        elif len(parts) == 3:
            name, start, end = parts
            start, end = start.strip(), end.strip()
            for label, value in (("start", start), ("end", end)):
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    raise ValueError(f"Invalid packs entry '{entry}': {label} date '{value}' must be YYYY-MM-DD")
        else:
            raise ValueError(f"Invalid packs entry '{entry}': expected 'Template Name' or 'Template Name:start:end'")

        template_id = _TEMPLATE_ID_BY_NAME.get(_norm(name))
        if template_id is None:
            valid = sorted(label for label, _cat in BILLING_TEMPLATES.values())
            raise ValueError(f"Unknown pack template '{name.strip()}'. Valid: {valid}")
        parsed.append((template_category(template_id), template_id, start, end))

    return parsed
